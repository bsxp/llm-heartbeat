#!/usr/bin/env python3
"""MCP server exposing the latency measurements to other agents.

    pip install "mcp[cli]"
    python mcp_server.py

Then point an MCP client at it, e.g. in Claude Code's config:

    { "mcpServers": {
        "llm-heartbeat": { "command": "python",
                           "args": ["/absolute/path/to/mcp_server.py"] } } }

All the logic lives in bench/advisor.py, which has no MCP dependency and can be
exercised directly. This file is only the protocol surface.

Design note: every tool returns its caveats inline. A calling agent sees tool
output, not this docstring and not the README, so anything it must know in order
to use the numbers responsibly has to travel with the numbers. The single most
important one is that this measures latency on one fixed prompt and says nothing
about whether a model can do the caller's task.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from bench import advisor

mcp = FastMCP("llm-heartbeat")


@mcp.tool()
def fastest_model(metric: str = "total", window_hours: float = 24,
                  vendor: str | None = None, exclude_unhealthy: bool = True,
                  limit: int = 3) -> dict[str, Any]:
    """Rank frontier models fastest-first on a fixed prompt measured hourly.

    Use to compare response speed or to check whether a provider is slow right
    now. Do NOT use to choose a model for a task: this measures latency only and
    carries no signal about capability or quality.

    metric: "total" for time to a finished answer, or "first_token" for time
      until anything appears (what a user watching a stream experiences). These
      disagree on reasoning models -- ask for the one you actually care about.
    window_hours: lookback; 0 for all retained history.
    vendor: optional filter, e.g. "Anthropic", "OpenAI", "xAI".
    exclude_unhealthy: drop models with failed or degraded runs in the window.
      Left on by default because a refused or empty response returns FAST, and
      would otherwise rank a broken model at the top.
    """
    return advisor.rank(advisor.load(), hours=window_hours, metric=metric,
                        vendor=vendor, exclude_unhealthy=exclude_unhealthy,
                        limit=limit)


@mcp.tool()
def list_models(window_hours: float = 24) -> dict[str, Any]:
    """Every tracked model with its latency profile over the window.

    Returns median and p90 total time, median time to first token, median silent
    thinking time, output tokens, cost per run, and counts of failed or degraded
    runs. Use when you want the full picture rather than a ranking.
    """
    data = advisor.load()
    return {"measured_at": data.get("generated_at"),
            "window_hours": window_hours,
            "models": advisor.summarize(data, window_hours),
            "caveats": advisor.CAVEATS}


@mcp.tool()
def model_status(model: str, window_hours: float = 24) -> dict[str, Any]:
    """Latency profile for one model, by key ("opus-5") or label ("Claude Opus 5").

    Use to check a specific model before routing traffic to it -- in particular
    failed_runs and degraded_runs, which indicate the provider is erroring or
    returning empty responses rather than merely running slow.
    """
    return advisor.status(advisor.load(), model, window_hours)


@mcp.tool()
def measurement_method() -> dict[str, Any]:
    """How these numbers are produced, and what they cannot tell you.

    Call this before drawing any conclusion stronger than "model X answered this
    one prompt faster than model Y over the last N hours".
    """
    data = advisor.load()
    return {
        "what_is_measured": (
            "One fixed prompt is sent to every model on an hourly schedule and the "
            "reply is timed. The clock starts when the request is sent and stops "
            "when the response stream ends."
        ),
        "phases": {
            "transport": "request sent until the first byte comes back",
            "thinking": "first byte until the first visible word -- silent reasoning",
            "writing": "first visible word until the stream closes",
        },
        "not_measured": [
            "correctness", "quality", "capability", "throughput under load",
            "latency from any region other than the measurement host",
        ],
        "prompt_sha": data.get("prompt_sha"),
        "measured_at": data.get("generated_at"),
        "source": advisor.RESULTS_URL,
        "caveats": advisor.CAVEATS,
    }


if __name__ == "__main__":
    mcp.run()
