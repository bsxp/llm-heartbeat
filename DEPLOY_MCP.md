# Deploying the remote MCP server

The code is in this repo; the deploy configuration is not. This file is the contract
between the two — what the private infra repo has to create for `lambda_handler.py`
to work. Nothing here is account-specific.

## Shape

```
mcp.ismyllmslow.com  ──▶  CloudFront  ──▶  Lambda Function URL  ──▶  lambda_handler.handler
                                                                        │
                                                        https://ismyllmslow.com/results.json
```

The function has no VPC, no database, no secrets, and no IAM permissions beyond
writing its own logs. It fetches one public URL over HTTPS and returns JSON.

## Function

| setting | value | why |
|---|---|---|
| runtime | `python3.12` | matches `requires-python = ">=3.11"` |
| handler | `lambda_handler.handler` | |
| memory | 512 MB | CPU scales with memory; below this, cold start roughly doubles |
| timeout | 30 s | `advisor.load()` uses a 20 s urllib timeout, so this must exceed it |
| architecture | `arm64` | cheaper per ms, no native deps to worry about |
| reserved concurrency | **10** | the cost ceiling — see below |

### Environment

| var | value |
|---|---|
| `MCP_ALLOWED_HOSTS` | `mcp.ismyllmslow.com` |

This is the DNS-rebinding-protection allow-list, checked against the incoming `Host`
header. It must list every hostname the server is reachable on. If clients will also
hit the raw Function URL, add that hostname too — comma-separated — or those requests
get a `421`. Leaving the var unset falls back to `mcp.ismyllmslow.com` only.

### Package

```bash
pip install --target build/ "mcp>=2,<3" mangum
cp -r bench mcp_server.py lambda_handler.py build/
cd build && zip -r ../mcp.zip .
```

`anthropic`, `openai` and `google-genai` are the *measurement* path's dependencies and
must not be in this bundle — the server never calls a model provider, it only reads
the published JSON. Including them roughly triples the package for nothing.

## Function URL

- Auth type: `NONE` (public — see below)
- Invoke mode: `BUFFERED` (Mangum does not stream; `RESPONSE_STREAM` buys nothing)
- CORS: leave **off** at the Function URL. The app sets its own CORS headers, and two
  layers both setting them produces duplicate `Access-Control-Allow-Origin`, which
  browsers reject.

## Why it is public, and what bounds the cost

The endpoint serves the same numbers as `https://ismyllmslow.com/results.json`, which
is already public. A key would protect nothing that is not already downloadable, so
the exposure that matters is spend, not data.

Two things bound it:

- **Reserved concurrency of 10.** A hostile client cannot fan out past ten concurrent
  executions, so the worst case is bounded per unit time rather than unbounded.
- **The 5-minute cache in `bench/advisor.py`.** A warm container answers from memory,
  so request volume does not translate into egress from the results bucket.

If the bill ever looks wrong, drop reserved concurrency first — it is the only lever
that changes the ceiling rather than the slope.

## Custom domain

`mcp.ismyllmslow.com` needs a CloudFront distribution in front of the Function URL
(Function URLs cannot take a custom domain directly) with an ACM certificate in
`us-east-1`. Forward the `Host` header as the custom domain, not the origin's — the
allow-list above is checked against whatever arrives.

## Verifying a deploy

```bash
curl -sS https://mcp.ismyllmslow.com/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"curl","version":"1"}}}'
```

Expect a JSON-RPC result naming the server `slow`.

**Then call it a second time.** One successful request proves almost nothing here: the
failure mode this design exists to avoid — a session manager that can only start once
per container — passes on request 1 and fails on request 2 against the same warm
container. Run the curl above three or four times in a row before believing a deploy.
