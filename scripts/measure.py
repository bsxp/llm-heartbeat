#!/usr/bin/env python3
"""Run one real measurement against a live API and print the result.

    python scripts/measure.py opus-5

This is the entire measurement path -- the same code the hourly job runs. Use it
to reproduce or audit any number on the dashboard.
"""

from __future__ import annotations

import sys

from bench import config, prompt, providers


def main(key: str) -> int:
    cfg = config.get(key)
    if str(cfg.get("model_id", "")).startswith("TODO"):
        print(f"{key}: model_id is not configured yet -- see scripts/list_models.py")
        return 1

    print(f"model  {cfg['label']} ({cfg['model_id']})")
    print(f"effort {cfg.get('effort', 'n/a')}   prompt {prompt.SHA}\n")

    result = providers.ADAPTERS[cfg["adapter"]](cfg, prompt.TEXT)
    if result.status != "ok":
        print(f"status {result.status}: {result.error}")
        return 1

    print(f"total time     {result.total_ms:>8,} ms   <- the headline")
    print(f"first token    {result.ttft_ms:>8,} ms")
    print(f"rate           {result.tok_per_s:>8} tok/s")
    print(f"output tokens  {result.output_tokens:>8,}")
    print(f"input tokens   {result.input_tokens:>8,}")
    print(f"health         {prompt.health_flag(result.text):>8}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <model-key>")
        print("keys:", ", ".join(m["key"] for m in config.load_models()))
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
