# Deploying the remote MCP server

The code is in this repo; the deploy configuration is not. This file is the contract
between the two — what the private infra repo has to create for `lambda_handler.py`
to work. Nothing here is account-specific.

The first deploy was done by hand against the live account, and every warning below
is written from something that actually broke rather than from anticipation. The
resources exist now and want codifying in the infra repo; treat this as the spec to
codify against, not as a substitute for doing it.

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
| `MCP_ALLOWED_HOSTS` | `<function-url-host>,mcp.ismyllmslow.com` |

This is the DNS-rebinding-protection allow-list, checked against the incoming `Host`
header. It must list every hostname the server is reachable on, comma-separated, or
those requests get a `421`. Leaving the var unset falls back to `mcp.ismyllmslow.com`
only.

The Function URL hostname belongs in the list even though nobody types it, because
CloudFront rewrites `Host` to the origin — see *Custom domain* below. Chicken-and-egg
on first deploy: the hostname does not exist until the Function URL is created, so
create the function with a placeholder, create the URL, then update the variable.

Note the comma breaks `--environment 'Variables={...}'` shorthand in the AWS CLI. Pass
a JSON file instead, and read the value back afterwards — the shorthand fails in a way
that is easy to mistake for success.

### Package

```bash
pip install --target build/ \
  --platform manylinux2014_aarch64 --python-version 3.12 \
  --implementation cp --only-binary=:all: \
  "mcp>=2,<3" mangum
cp -r bench mcp_server.py lambda_handler.py build/
find build -name __pycache__ -type d -exec rm -rf {} +
cd build && zip -r ../mcp.zip .          # ~8.9 MB
```

Both awkward flags in that first command are load-bearing.

**The `--platform` flags are not optional.** `mcp` pulls in `pydantic-core`, `cffi`,
`rpds` and `cryptography`, all of which ship compiled extensions. A plain
`pip install --target` on a Mac silently produces macOS binaries, and the function
then fails at import with an error that does not mention architecture. Confirm before
uploading: `file $(find build -name '*.so' | head -1)` must say `ARM aarch64`.

**Do not exclude `*.dist-info/`.** It looks like dead weight and is not: `httpx2` reads
its own version through `importlib.metadata` at import time, so stripping the metadata
gives `No package metadata was found for httpx2` and a 502 on every request.

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
`us-east-1` — CloudFront reads certificates only from that region, wherever the
function lives.

Two managed policies on the default cache behaviour do the work:

| policy | id | |
|---|---|---|
| cache | `4135ea2d-6df8-44a3-9df3-4b5a84be39ad` | CachingDisabled — every response is live data |
| origin request | `b689b0a8-53d0-40ab-baf2-68738e2966ac` | AllViewerExceptHostHeader |

**Do not forward the `Host` header.** It is tempting, because the app validates
`Host` and you want it to see the real one. It does not work: a Lambda Function URL
rejects any request whose `Host` is not its own generated hostname, with a 403 that
never reaches the app. `AllViewerExceptHostHeader` lets CloudFront rewrite `Host` to
the origin — which is why `MCP_ALLOWED_HOSTS` must contain the Function URL hostname,
not just the pretty one.

Allowed methods must include `POST`, `OPTIONS` and `DELETE`; the MCP protocol uses all
three. Leave CloudFront's own CORS alone for the same reason as the Function URL — the
app already sets those headers.

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
