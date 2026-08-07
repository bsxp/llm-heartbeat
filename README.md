# llm-heartbeat

A public heartbeat for frontier language models. Every hour, every model gets the
**same byte-identical prompt**, and we time the response.

That's it. It is not a leaderboard, not a capability benchmark, and nothing is graded.
The question it answers is narrow on purpose:

> **How long do you wait for an answer, and is that changing?**

## Why this exists

Published benchmarks tell you how smart a model is. They don't tell you what it feels
like to use one on a Tuesday afternoon, or whether it got slower last week. Latency
drifts — providers change routing, defaults, and capacity, and models change how much
they write. Almost none of that is announced.

A fixed prompt on a fixed schedule turns that drift into a line on a chart.

## What we measure

| Metric | Meaning |
|---|---|
| **Total time** | **The headline.** Request sent → response complete. What you actually wait. |
| Time to first token | Responsiveness, independent of how long the answer is. |
| Tokens / second | Generation rate once output starts. |
| Output tokens | How much the model chose to write. |
| Health | A cheap sanity flag (empty / no code). **Not a grade.** |

**Total time is deliberately not normalised for length.** Given an identical prompt, how
much a model writes is part of the answer to "how long does this take" — a model that
emits 3× the tokens genuinely does make you wait 3× longer. We don't correct for that.

Time-to-first-token and tokens/sec are published alongside it so a change is
*diagnosable*: total time up with a flat generation rate means the model got wordier;
generation rate down means the provider got slower. Same headline number, but you can
tell the two apart.

## What we do *not* measure

- **Correctness.** The prompt asks for a program; nothing runs it. A model could return
  beautiful nonsense and score identically. The `health` flag only distinguishes "a slow
  run" from "a broken run" — it is not a quality signal, and shouldn't be read as one.
- **Capability, reasoning, or quality** of any kind.
- **Cost-effectiveness.** Cost per run is shown because it's cheap to compute, not
  because it's a verdict.

If you want to know which model is *better*, this is the wrong project.

## Methodology, in full

**The prompt is fixed and public.** It lives in [`bench/prompt.py`](bench/prompt.py) —
a request to write a program solving a non-trivial counting problem. It's a real
reasoning workload on purpose; `"say hello"` would measure almost nothing, and slowdowns
tend to surface under load.

A SHA-256 prefix of the prompt is recorded on **every single run**, shown in the method
section, and marked on the charts wherever it changes. If the prompt ever changes, the hash changes, and the numbers before
and after are a different series. This is the main thing that keeps the chart honest, and
it's why the prompt file carries a do-not-edit warning.

**Thinking mode is pinned and logged.** Anthropic generations differ in what
happens when you *omit* the `thinking` parameter — Opus 5 runs adaptive thinking by
default, while Opus 4.8/4.7/4.6 run with none. Leaving it to defaults would have made
Opus 5 look dramatically slower than its predecessors purely because it was the only one
thinking. Every model therefore pins `thinking` explicitly, and the value is written onto
every row.

**Reasoning effort is pinned and logged.** Effort settings directly change latency, so
each model's is fixed in [`bench/models.json`](bench/models.json) and written onto every row. An
unlogged change there would look exactly like a provider regression.

**Timing is measured client-side around a streaming call.** See
[`bench/providers.py`](bench/providers.py) — that file is the measurement, and it's
public precisely so the numbers can be audited rather than trusted.

- The clock starts immediately before the request and stops when the stream closes.
- Time-to-first-token is stamped on the first **visible text** delta -- never on a
  reasoning/thinking delta. Reasoning models produce no user-visible output until
  reasoning finishes, so this keeps TTFT the same quantity across vendors: how long
  until words appear. It does mean TTFT *includes* reasoning time, which is the honest
  number for "when do I see something" but is not a pure network-latency figure.
- Tokens/sec is output tokens ÷ (total − TTFT), so the first-token wait doesn't drag the
  rate down.
- Token counts come from each provider's own usage reporting, never estimated.

**Each model runs in its own isolated invocation**, so a slow provider can't inflate
another's number.

## Prompt history

The task is **Conway's Game of Life**: run the acorn pattern for 5000 generations on an
unbounded grid and count the live cells. Unbounded means a fixed array will not do, so the
model has to reach for a sparse representation. It is a real algorithmic choice with no
cryptographic adjacency.

| Hash | Retired | Why it changed |
|---|---|---|
| `8ce9dff508ea` | 2026-08-01 | A reworded squarefree clause. It did not work — Fable 5 refused 0/6. It was adopted on a single passing probe, which was not evidence: the classifier is stochastic. Listed because the runs it produced are in the data. |
| `10471c8576af` | 2026-08-01 | *"n is squarefree (no prime p has p^2 dividing n)"* plus a prime digit sum. Fable 5 refused every run, category `cyber` — squarefree testing is factorisation. The wider lesson: the trigger is resemblance to **cryptographic primitives**, not number theory as such. A later candidate seeded by a linear congruential generator was refused just as hard. Rather than keep rewording around a classifier, the task moved to a domain with no such adjacency. |

Runs before a change carry the old hash and form a separate series. The charts draw a
marker at the boundary so the discontinuity is not mistaken for a change in speed.

## Known limitations

Stated up front, because a latency number without its caveats is misleading:

- **Network round-trip is included.** Runs execute from a single AWS region, so TTFT is
  not a pure server-side figure and won't match a provider's own published numbers. It is
  consistent run-to-run, which is what matters for drift.
- **Provider-side caching may flatter results over time.** A fixed prompt is exactly the
  thing a cache is good at. Hourly gaps exceed most cache TTLs, but not all providers
  document this. **A sharp, permanent step down in TTFT should be treated as suspected
  caching, not a speed improvement.**
- **One prompt is one workload.** A model tuned for short answers looks fast here. This
  measures one specific shape of request, not general speed.
- **One region, one time zone.** Regional capacity differences are invisible to us.
- **Small n.** One sample per model per hour. Single-point spikes are noise; read the
  trend, not the dot.
- **Token counts are not comparable across Anthropic generations.** Opus 4.7 introduced a
  new tokenizer, so the same output text counts ~1x-1.35x higher on Opus 4.7/4.8/5 than on
  Opus 4.6. Total time and time-to-first-token compare cleanly; **output-token and
  tokens/sec comparisons between Opus 4.6 and the newer models do not** — 4.6 will look
  artificially terse and artificially fast per token.

## Repository layout

```
bench/prompt.py       the exact prompt + its hash, and the health check
bench/providers.py    the measurement: streaming call + timing, per provider
bench/models.json     which models run, at what effort, at what price
bench/config.py       roster loading
bench/advisor.py      ranking + summaries over the published results (no MCP dep)
mcp_server.py         MCP server exposing those to other agents
web/                  the dashboard (static HTML, no build step, no dependencies)
scripts/              helper to list live model IDs from each provider
amplify.yml           AWS Amplify build spec for the dashboard
```

The AWS infrastructure that runs this on a schedule lives in a separate private
repository. It contains no methodology — only account-specific plumbing (IAM, table and
bucket names, schedules, deploy config). Everything that determines *what the numbers
mean* is in this repo.

**No credentials are in this repository, and none ever should be.** API keys are read
from the environment at runtime and stored in AWS Secrets Manager. `bench/models.json`
references environment variable *names* only.

## Asking this from an agent (MCP)

The published measurements are available to other agents over MCP, so a coding agent
can ask which model is answering fastest right now rather than guessing.

```bash
pip install -e ".[mcp]"
python mcp_server.py
```

Register it with an MCP client — for Claude Code:

```json
{ "mcpServers": {
    "slow": { "command": "python",
              "args": ["/absolute/path/to/mcp_server.py"] } } }
```

Four tools:

| tool | answers |
|---|---|
| `fastest_model` | ranked fastest-first, by `total` or `first_token` |
| `list_models` | every model's latency profile over a window |
| `model_status` | one model — including whether it is erroring or degraded |
| `measurement_method` | how the numbers are made, and what they cannot tell you |

`metric` is the parameter that matters. **`total`** is time to a finished answer;
**`first_token`** is time until anything appears, which is what someone watching a
stream experiences. On reasoning models these disagree — at the time of writing Claude
Opus 4.8 is fastest to first word while Claude Opus 4.7 is fastest to finish.

Two deliberate choices worth knowing:

**Every response carries its caveats inline.** A calling agent sees tool output, not
this README, so anything required to use the numbers responsibly travels with them.
The load-bearing one: this measures latency on one fixed prompt and says *nothing*
about whether a model can do the caller's task. "Fastest" is not "best", and routing
hard work to whatever tops this ranking is a misuse of it.

**Unhealthy models are excluded from rankings by default.** A refused or empty
response comes back fast, so a broken model would otherwise rank first. Pass
`exclude_unhealthy=false` if you want to see them anyway.

It reads the same public `results.json` the dashboard does, cached for five minutes
against an hourly publish.

## Running a measurement yourself

You don't need the infrastructure to reproduce a number:

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/measure.py opus-5
```

That runs one real measurement against the live API and prints the same numbers the
dashboard records. It is the whole measurement path — there is nothing else behind it.

To preview the dashboard locally, drop a `results.json` next to `web/index.html` (the
schema is whatever the dashboard reads — see `draw()` in the page) and:

```bash
python -m http.server 8811 --directory web
```

## Contributing

Useful contributions: additional provider adapters, corrections to the measurement
methodology, and dashboard improvements.

Please **don't** open PRs that change `bench/prompt.py` or a model's `effort`. Both break
comparability with every historical data point. If you think the prompt should change,
open an issue first — the right answer is usually a second series, not an edit.

## License

MIT. See [LICENSE](LICENSE).
