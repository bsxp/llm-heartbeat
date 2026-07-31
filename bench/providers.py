"""Per-provider single-call adapters.

One streaming request per model per run. Streaming is not for the UX -- it is
the only way to capture time-to-first-token, which is what separates "the
provider got slower" from "the model got chattier".

Every adapter reports the same three timings:

  ttft_ms   -- request sent -> first content byte back
  total_ms  -- request sent -> stream closed (the headline: what a user waits)
  tok_per_s -- output tokens / (total_ms - ttft_ms), the generation rate

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
    ttft_ms: int | None = None
    total_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    error: str | None = None

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
        self.ttft: int | None = None

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

    with client.messages.stream(
        model=cfg["model_id"],
        max_tokens=MAX_TOKENS,
        output_config={"effort": cfg.get("effort", "medium")},
        messages=[{"role": "user", "content": text}],
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta":
                clock.mark_first_token()
        message = stream.get_final_message()

    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.input_tokens = message.usage.input_tokens or 0
    out.output_tokens = message.usage.output_tokens or 0
    out.cached_input_tokens = getattr(message.usage, "cache_read_input_tokens", 0) or 0

    if message.stop_reason == "refusal":
        out.status = "refusal"
        out.error = f"refusal: {getattr(message.stop_details, 'category', 'unknown')}"
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
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            clock.mark_first_token()
            chunks.append(chunk.choices[0].delta.content)

    out.ttft_ms = clock.ttft
    out.total_ms = clock.elapsed_ms()
    out.text = "".join(chunks)
    if usage:
        out.input_tokens = usage.prompt_tokens or 0
        out.output_tokens = usage.completion_tokens or 0
        details = getattr(usage, "prompt_tokens_details", None)
        out.cached_input_tokens = getattr(details, "cached_tokens", 0) or 0
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
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
        piece = getattr(chunk, "text", None)
        if piece:
            clock.mark_first_token()
            chunks.append(piece)

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
    "openai": run_openai_compatible,
    "google": run_google,
}
