#!/usr/bin/env python3
"""Remote MCP server exposing the latency measurements to other agents.

This is a Streamable HTTP server, not a stdio one: agents reach it at a URL and
there is nothing to install. It is deployed as an AWS Lambda behind a Function
URL (see lambda_handler.py); the infrastructure that does the deploying lives in
a separate private repository.

Run it locally against the live published data:

    pip install -e ".[serve]"
    python mcp_server.py            # http://127.0.0.1:8000/mcp

All the logic lives in bench/advisor.py, which has no MCP dependency and can be
exercised directly. This file is only the protocol surface.

Design note: every tool returns its caveats inline. A calling agent sees tool
output, not this docstring and not the README, so anything it must know in order
to use the numbers responsibly has to travel with the numbers. The single most
important one is that this measures latency on one fixed prompt and says nothing
about whether a model can do the caller's task.
"""

from __future__ import annotations

import os
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware

from bench import advisor

# Host allow-list for DNS-rebinding protection. The library's two defaults are
# both wrong for us: omitting the settings disables host validation outright,
# while enabling it with an empty list rejects every request. So the list is
# explicit, and settable from the environment because the Function URL hostname
# is only known once the private infra repo has deployed the function.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    "MCP_ALLOWED_HOSTS", "mcp.ismyllmslow.com").split(",") if h.strip()]

INSTRUCTIONS = """\
Latency measurements for frontier language models, from one fixed prompt sent to
every model hourly.

Use this to compare response speed, or to check whether a provider is slow or
erroring right now. Do NOT use it to choose a model for a task: it measures
latency only and carries no signal about capability or quality. Call
measurement_method before drawing any conclusion stronger than "model X answered
this one prompt faster than model Y over the last N hours".
"""

mcp = MCPServer(
    name="slow",
    title="Is my LLM slow?",
    instructions=INSTRUCTIONS,
    website_url="https://ismyllmslow.com",
    version="0.1.0",
)


async def _load() -> dict[str, Any]:
    """Fetch the published results off the event loop.

    advisor.load() is deliberately synchronous -- it is the same code path the
    tests and the CLI use -- and it does a blocking HTTP GET on a cache miss.
    Calling it directly from an async handler would stall every other in-flight
    request for the duration of that fetch, so it goes to a worker thread.
    """
    return await anyio.to_thread.run_sync(advisor.load)


@mcp.tool()
async def fastest_model(metric: str = "total", window_hours: float = 24,
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
    return advisor.rank(await _load(), hours=window_hours, metric=metric,
                        vendor=vendor, exclude_unhealthy=exclude_unhealthy,
                        limit=limit)


@mcp.tool()
async def list_models(window_hours: float = 24) -> dict[str, Any]:
    """Every tracked model with its latency profile over the window.

    Returns median and p90 total time, median time to first token, median silent
    thinking time, output tokens, cost per run, and counts of failed or degraded
    runs. Use when you want the full picture rather than a ranking.
    """
    data = await _load()
    return {"measured_at": data.get("generated_at"),
            "window_hours": window_hours,
            "models": advisor.summarize(data, window_hours),
            "caveats": advisor.CAVEATS}


@mcp.tool()
async def model_status(model: str, window_hours: float = 24) -> dict[str, Any]:
    """Latency profile for one model, by key ("opus-5") or label ("Claude Opus 5").

    Use to check a specific model before routing traffic to it -- in particular
    failed_runs and degraded_runs, which indicate the provider is erroring or
    returning empty responses rather than merely running slow.
    """
    return advisor.status(await _load(), model, window_hours)


@mcp.tool()
async def measurement_method() -> dict[str, Any]:
    """How these numbers are produced, and what they cannot tell you.

    Call this before drawing any conclusion stronger than "model X answered this
    one prompt faster than model Y over the last N hours".
    """
    data = await _load()
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


def build_app(allowed_hosts: list[str] | None = None):
    """The ASGI app, served by Lambda in production and uvicorn in development.

    stateless_http means no session is carried between requests, and json_response
    means a single JSON reply rather than an SSE stream. Both are required here
    rather than merely convenient: Lambda gives each request its own short-lived
    execution environment with no shared memory, so a server that parked session
    state in one process would hand out session IDs that the next request cannot
    honour. It also cannot hold an SSE stream open across an invocation.
    """
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts if allowed_hosts is not None else ALLOWED_HOSTS,
        ),
    )
    # The data is already public, so any origin may read it. Browser-based
    # clients need mcp-session-id exposed or they cannot follow the protocol.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "accept", "mcp-session-id",
                       "mcp-protocol-version", "last-event-id"],
        expose_headers=["mcp-session-id", "mcp-protocol-version"],
    )
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    # Local runs are reached as 127.0.0.1/localhost, which are not in the
    # production allow-list, so the dev app gets its own.
    dev = build_app(allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"])
    print(f"MCP endpoint: http://127.0.0.1:{port}/mcp")
    uvicorn.run(dev, host="127.0.0.1", port=port)
