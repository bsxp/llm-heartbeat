"""AWS Lambda entrypoint for the remote MCP server.

Set the function's handler to `lambda_handler.handler`. Everything else lives in
mcp_server.py; this file exists only so that importing the app does not require
mangum, which is dead weight when running locally under uvicorn.

The app is rebuilt on every invocation, which looks wasteful and is not.

Mangum runs the ASGI lifespan per invocation -- startup before the request and
shutdown after it -- while StreamableHTTPSessionManager.run() may be called only
once per instance. Pair a module-level app with a warm container and you get a
server that answers the first request and then returns 500 for every request
after it, for the life of that container. It passes any test that makes a single
call, which is what makes it worth a comment.

Rebuilding hands each lifespan cycle its own session manager. Nothing is lost:
the server runs stateless, so no session was meant to outlive a request, and the
expensive part -- the fetched results.json -- is cached in bench.advisor at
module scope and does survive between invocations.
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from mcp_server import build_app


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return Mangum(build_app(), lifespan="auto")(event, context)
