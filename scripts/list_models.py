#!/usr/bin/env python3
"""Print the model IDs each provider currently exposes.

Use this to fill in the `TODO-...` placeholders in models.json rather than
guessing an ID -- a wrong string is a 404 at 3am, an hour of missing data, and
a gap in the chart. Only providers whose key is set in the environment are
queried.

    export OPENAI_API_KEY=... GEMINI_API_KEY=... XAI_API_KEY=... DEEPSEEK_API_KEY=...
    python scripts/list_models.py
"""

from __future__ import annotations

import os


def _openai_compatible(label: str, key_env: str, base_url: str | None) -> None:
    if key_env not in os.environ:
        print(f"\n{label}: skipped ({key_env} not set)")
        return
    from openai import OpenAI

    client = OpenAI(api_key=os.environ[key_env], base_url=base_url)
    print(f"\n{label}:")
    for model in sorted(client.models.list().data, key=lambda m: m.id):
        print(f"  {model.id}")


def _anthropic() -> None:
    if "ANTHROPIC_API_KEY" not in os.environ:
        print("\nAnthropic: skipped (ANTHROPIC_API_KEY not set)")
        return
    import anthropic

    client = anthropic.Anthropic()
    print("\nAnthropic:")
    for model in client.models.list():
        print(f"  {model.id:32} {model.display_name}")


def _google() -> None:
    if "GEMINI_API_KEY" not in os.environ:
        print("\nGoogle: skipped (GEMINI_API_KEY not set)")
        return
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    print("\nGoogle:")
    for model in client.models.list():
        print(f"  {model.name}")


if __name__ == "__main__":
    _anthropic()
    _openai_compatible("OpenAI", "OPENAI_API_KEY", None)
    _google()
    _openai_compatible("xAI", "XAI_API_KEY", "https://api.x.ai/v1")
    _openai_compatible("DeepSeek", "DEEPSEEK_API_KEY", "https://api.deepseek.com")
