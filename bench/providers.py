"""Per-provider single-call adapters.

One streaming request per model per run. Streaming is not for the UX -- it is
the only way to capture time-to-first-token, which is what separates "the
provider got slower" from "the model got chattier".

Every adapter reports the same three timings:

  ttfb_ms   -- request sent -> first event on the stream. Network round-trip,
               TLS, and provider accept/queue. NOT thinking: this event fires
               before the model has produced anything.
  ttft_ms   -- request sent -> first VISIBLE word
  total_ms  -- request sent -> stream closed (the headline: what a user waits)
  tok_per_s -- output tokens / (total_ms - ttft_ms), the generation rate

  ttft_ms - ttfb_ms is therefore silent reasoning time, isolated from transport.

`effort` is fixed per model in models.json and logged on every row, because it
directly changes latency: an unlogged change to it would look like a provider
regression.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

MAX_TOKENS = 16_000


@dataclass
class RunResult:
    status: str = "ok"          # ok | refusal | error
    text: str = ""
    ttfb_ms: int | None = None
    ttft_ms: int | None = None
    total_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0   # subset of output_tokens; 0 where unreported
    error: str | None = None

    @property
    def thinking_ms(self) -> int | None:
        """Silent reasoning: the stream was open but nothing was readable yet."""
        if self.ttft_ms is None or self.ttfb_ms is None:
            return None
        return max(0, self.ttft_ms - self.ttfb_ms)

    @property
    def tok_per_s(self) -> float | None:
        if not self.output_tokens or self.ttft_ms is None:
            return None
        gen_ms = self.total_ms - self.ttft_ms
        if gen_ms <= 0:
            return None
        return round(self.output_tokens / (gen_ms / 1000), 2)


class _Clock:
    def __init__(self) -> None:
        self.start = time.monotonic()
        self.ttfb: int | None = None
        self.ttft: int | None = None

    def mark_stream_open(self) -> None:
        """First event of any kind -- the request has landed and the server is
        responding. Everything before this is transport and provider accept."""
        if self.ttfb is None:
            self.ttfb = int((time.monotonic() - self.start) * 1000)

    def mark_first_token(self) -> None:
        if self.ttft is None:
            self.ttft = int((time.monotonic() - self.start) * 1000)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.start) * 1000)


# --------------------------------------------------------------------------


def run_anthropic(cfg: dict[str, Any], text: str) -> RunResult:
    import anthropic

    out = RunResult()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)
    clock = _Clock()

    # Thinking MUST be set explicitly, never left to the model's default: on
    # Opus 5 omitting it runs adaptive thinking, while on Opus 4.8/4.7/4.6
    # omitting it runs with no thinking at all. Relying on defaults would make
    # a newer generation look slower purely because it was the only one
    # thinking -- a config asymmetry that reads as a model difference.
    with client.messages.stream(
        model=cfg["model_id"],
        max_tokens=MAX_TOKENS,
        thinking={"type": cfg.get("thinking", "adaptive")},
        output_config={"effort": cfg.get("effort", "medium")},
        messages=[{"role": "user", "content": text}],
    ) as stream:
        for event in stream:
            clock.mark_stream_open()
            # Mark on VISIBLE text only, never on thinking deltas. Two reasons:
            # thinking is streamed with empty text under the default
            # display="omitted", so a thinking delta times something nobody
            # sees; and OpenAI-style reasoning models emit no content until
            # reasoning finishes. Marking on text in both keeps TTFT the same
            # quantity across vendors: "how long until words appear".
            if (
                event.type == "content_block_delta"
                and getattr(event.delta, "type", None) == "text_delta"
            ):
                clock.mark_first_token()
        message = stream.get_final_message()

    out.ttfb_ms = clock.ttfb
    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.input_tokens = message.usage.input_tokens or 0
    out.output_tokens = message.usage.output_tokens or 0
    out.cached_input_tokens = getattr(message.usage, "cache_read_input_tokens", 0) or 0

    if message.stop_reason == "refusal":
        # Capture the explanation, not just the category: "cyber" alone does not
        # tell you WHICH part of a prompt tripped a classifier, and without it a
        # refusal is undiagnosable.
        details = message.stop_details
        out.status = "refusal"
        out.error = "refusal[{}]: {}".format(
            getattr(details, "category", "unknown") or "uncategorised",
            (getattr(details, "explanation", None) or "no explanation given")[:400],
        )
        return out

    out.text = "".join(b.text for b in message.content if b.type == "text")
    return out


def run_openai_compatible(cfg: dict[str, Any], text: str) -> RunResult:
    from openai import OpenAI

    out = RunResult()
    client = OpenAI(
        api_key=os.environ[cfg["api_key_env"]],
        base_url=cfg.get("base_url") or None,
        max_retries=0,
    )
    clock = _Clock()
    chunks: list[str] = []
    usage = None

    stream = client.chat.completions.create(
        model=cfg["model_id"],
        messages=[{"role": "user", "content": text}],
        max_completion_tokens=MAX_TOKENS,
        stream=True,
        stream_options={"include_usage": True},  # usage arrives on the final chunk
    )
    for chunk in stream:
        clock.mark_stream_open()
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            clock.mark_first_token()
            chunks.append(chunk.choices[0].delta.content)

    out.ttfb_ms = clock.ttfb
    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.text = "".join(chunks)
    if usage:
        out.input_tokens = usage.prompt_tokens or 0
        out.output_tokens = usage.completion_tokens or 0
        details = getattr(usage, "prompt_tokens_details", None)
        out.cached_input_tokens = getattr(details, "cached_tokens", 0) or 0
        # Recorded even though this endpoint cannot TIME the reasoning: a nonzero
        # count next to a near-zero thinking_ms is proof the provider withheld
        # the stream, which is otherwise only guessable from how long ttfb was.
        out_details = getattr(usage, "completion_tokens_details", None)
        out.reasoning_tokens = getattr(out_details, "reasoning_tokens", 0) or 0
    return out


def run_openai_responses(cfg: dict[str, Any], text: str) -> RunResult:
    """OpenAI via the Responses API, which streams the reasoning phase.

    `chat.completions` sends nothing at all until reasoning has finished, so
    ttfb absorbs the entire silent phase and thinking_ms falls out as ~0 -- the
    dashboard has to report those runs as "not separable" because claiming the
    model thought for 21ms would be false. Responses emits `response.created`
    immediately and reasoning events while the model works, so ttfb times the
    network alone and ttft - ttfb is real reasoning time.

    Kept as a separate adapter rather than a flag on run_openai_compatible:
    that function also serves xAI and DeepSeek through OpenAI-compatible
    endpoints, and those do not implement Responses.
    """
    from openai import OpenAI

    out = RunResult()
    client = OpenAI(api_key=os.environ[cfg["api_key_env"]],
                    base_url=cfg.get("base_url") or None, max_retries=0)
    clock = _Clock()
    chunks: list[str] = []
    usage = None

    kwargs: dict[str, Any] = {
        "model": cfg["model_id"],
        "input": text,
        "max_output_tokens": MAX_TOKENS,
        "stream": True,
    }
    if cfg.get("effort"):
        kwargs["reasoning"] = {"effort": cfg["effort"]}

    for event in client.responses.create(**kwargs):
        clock.mark_stream_open()
        etype = getattr(event, "type", "")
        # Mark on VISIBLE text only, exactly as the Anthropic adapter does, so
        # ttft means the same thing across vendors: how long until words appear.
        # Reasoning summary deltas are NOT the answer and must not stop the clock.
        if etype == "response.output_text.delta":
            clock.mark_first_token()
            chunks.append(getattr(event, "delta", "") or "")
        elif etype in ("response.completed", "response.incomplete"):
            usage = getattr(getattr(event, "response", None), "usage", None)

    out.ttfb_ms = clock.ttfb
    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.text = "".join(chunks)
    if usage:
        out.input_tokens = getattr(usage, "input_tokens", 0) or 0
        out.output_tokens = getattr(usage, "output_tokens", 0) or 0
        # Reasoning tokens are counted inside output_tokens. Recorded separately
        # because they are the only direct evidence of thinking that survives
        # when the timing cannot be split.
        details = getattr(usage, "output_tokens_details", None)
        out.reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
        cached = getattr(usage, "input_tokens_details", None)
        out.cached_input_tokens = getattr(cached, "cached_tokens", 0) or 0
    return out


def run_google(cfg: dict[str, Any], text: str) -> RunResult:
    from google import genai
    from google.genai import types

    out = RunResult()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    clock = _Clock()
    chunks: list[str] = []
    usage = None

    stream = client.models.generate_content_stream(
        model=cfg["model_id"],
        contents=text,
        config=types.GenerateContentConfig(max_output_tokens=MAX_TOKENS),
    )
    for chunk in stream:
        clock.mark_stream_open()
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
        piece = getattr(chunk, "text", None)
        if piece:
            clock.mark_first_token()
            chunks.append(piece)

    out.ttfb_ms = clock.ttfb
    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.text = "".join(chunks)
    if usage:
        out.input_tokens = usage.prompt_token_count or 0
        out.output_tokens = usage.candidates_token_count or 0
        out.cached_input_tokens = getattr(usage, "cached_content_token_count", 0) or 0
    return out


ADAPTERS: dict[str, Callable[[dict[str, Any], str], RunResult]] = {
    "anthropic": run_anthropic,
    "openai": run_openai_compatible,        # chat.completions; also xAI, DeepSeek
    "openai_responses": run_openai_responses,
    "google": run_google,
}
