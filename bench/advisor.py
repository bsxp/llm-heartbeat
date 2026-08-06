"""Answer "which model is fastest right now" from the published measurements.

Kept free of any MCP dependency so the logic can be tested directly; mcp_server.py
is a thin wrapper over these functions.

The hard part here is not ranking -- it is refusing to answer more than the data
supports. These runs are ONE fixed prompt, at pinned effort, from one region.
That makes them a fair comparison of how long each model takes on that prompt and
a fair signal of whether a provider is degraded right now. It does NOT make them
a general speed benchmark, and it says nothing about whether a model can do the
caller's task. An agent that reads "fastest: Claude Opus 4.7" and routes hard
work there because of it has been misled, so every response carries the caveats
inline rather than leaving them in documentation the caller may never read.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

RESULTS_URL = "https://ismyllmslow.com/results.json"
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
CACHE_TTL_S = 300          # the upstream job publishes hourly; 5 min is plenty

CAVEATS = [
    "One fixed prompt, identical for every model, sent hourly from a single region.",
    "Reasoning effort and thinking mode are pinned per model; both change latency.",
    "Nothing is smoothed and nothing is adjusted for response length -- a model "
    "that chose to write more took longer.",
    "Measures latency only. Says nothing about correctness, quality or capability: "
    "do not use it to choose a model for a task, only to compare speed or to spot "
    "a provider that is slow right now.",
]


def load(url: str = RESULTS_URL, force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]
    with urllib.request.urlopen(url, timeout=20) as fh:
        data = json.load(fh)
    _CACHE.update(at=now, data=data)
    return data


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def _recent(series: dict[str, Any], hours: float) -> list[dict[str, Any]]:
    if not hours:
        return list(series.get("runs", []))
    newest = max((_ts(r) for r in series.get("runs", [])), default=0.0)
    cutoff = newest - hours * 3600
    return [r for r in series.get("runs", []) if _ts(r) >= cutoff]


def _ts(run: dict[str, Any]) -> float:
    from datetime import datetime
    return datetime.fromisoformat(run["ts"]).timestamp()


def summarize(data: dict[str, Any], hours: float = 24) -> list[dict[str, Any]]:
    """One record per model over the window, newest data last."""
    out = []
    for s in data.get("series", []):
        runs = _recent(s, hours)
        ok = [r for r in runs if r.get("status") == "ok" and r.get("total_ms") is not None]
        if not ok:
            out.append({
                "key": s.get("key"), "label": s.get("label"), "vendor": s.get("vendor"),
                "runs": len(runs), "available": False,
                "note": "no completed runs in this window",
            })
            continue
        totals = [r["total_ms"] for r in ok]
        ttfts = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
        latest = ok[-1]
        # A run that came back empty or refused is not a healthy sample of speed;
        # it is usually FAST, which would flatter a broken model in a ranking.
        degraded = [r for r in runs if r.get("health") not in (None, "ok")]
        errored = [r for r in runs if r.get("status") != "ok"]
        think = [r["thinking_ms"] for r in ok if r.get("thinking_ms") is not None]
        out.append({
            "key": s.get("key"), "label": s.get("label"), "vendor": s.get("vendor"),
            "available": True,
            "runs": len(runs),
            "median_total_ms": round(_median(totals)),
            "p90_total_ms": round(_pct(totals, 0.90)),
            "median_first_token_ms": round(_median(ttfts)) if ttfts else None,
            "median_thinking_ms": round(_median(think)) if think else None,
            "latest_total_ms": latest["total_ms"],
            "latest_run_at": latest["ts"],
            "degraded_runs": len(degraded),
            "failed_runs": len(errored),
            "median_output_tokens": round(_median([r["output_tokens"] for r in ok
                                                   if r.get("output_tokens") is not None]) or 0),
            "cost_usd_per_run": latest.get("cost_usd"),
            "effort": latest.get("effort"),
            "thinking_mode": latest.get("thinking"),
        })
    return out


def rank(data: dict[str, Any], hours: float = 24, metric: str = "total",
         vendor: str | None = None, exclude_unhealthy: bool = True,
         limit: int = 3) -> dict[str, Any]:
    """Rank models fastest-first.

    `metric` matters more than it looks. "total" is time to a finished answer;
    "first_token" is time until anything appears, which is what a user watching a
    stream actually experiences. On reasoning models these disagree sharply -- a
    model can start writing quickly and finish late, or sit silent for 15s and
    then finish in a burst -- so the caller has to say which one it cares about.
    """
    if metric not in ("total", "first_token"):
        raise ValueError("metric must be 'total' or 'first_token'")
    field = "median_total_ms" if metric == "total" else "median_first_token_ms"

    rows = summarize(data, hours)
    pool = [r for r in rows if r.get("available") and r.get(field) is not None]
    if vendor:
        pool = [r for r in pool if (r.get("vendor") or "").lower() == vendor.lower()]
    skipped = []
    if exclude_unhealthy:
        keep = []
        for r in pool:
            if r["failed_runs"] or r["degraded_runs"]:
                skipped.append({"label": r["label"],
                                "reason": f"{r['failed_runs']} failed, "
                                          f"{r['degraded_runs']} degraded runs in window"})
            else:
                keep.append(r)
        pool = keep

    pool.sort(key=lambda r: r[field])
    return {
        "metric": metric,
        "window_hours": hours,
        "measured_at": data.get("generated_at"),
        "ranking": pool[:limit],
        "excluded_as_unhealthy": skipped,
        "caveats": CAVEATS,
    }


def status(data: dict[str, Any], key: str, hours: float = 24) -> dict[str, Any]:
    for r in summarize(data, hours):
        if r.get("key") == key or (r.get("label") or "").lower() == key.lower():
            return {**r, "measured_at": data.get("generated_at"), "caveats": CAVEATS}
    known = [r.get("key") for r in summarize(data, hours)]
    return {"error": f"unknown model {key!r}", "known_keys": known}
