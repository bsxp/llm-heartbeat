"""Model roster loader.

`models.json` lives inside the package so the measurement half of this project
is self-contained and pip-installable -- the infrastructure repo consumes it as
a dependency rather than vendoring a copy that can drift.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "models.json"


def load_models() -> list[dict[str, Any]]:
    with open(CONFIG_PATH) as fh:
        return json.load(fh)["models"]


def get(key: str) -> dict[str, Any]:
    for cfg in load_models():
        if cfg["key"] == key:
            return cfg
    raise KeyError(f"no model with key {key!r} in {CONFIG_PATH}")


def runnable_models() -> list[dict[str, Any]]:
    """Enabled models whose model_id is filled in and whose API key is present.

    Skips rather than raises: a missing key for one provider should cost you
    that provider's data point, not the whole hour.
    """
    out = []
    for cfg in load_models():
        if not cfg.get("enabled"):
            continue
        if str(cfg.get("model_id", "")).startswith("TODO"):
            print(f"skipping {cfg['key']}: model_id not configured")
            continue
        if cfg["api_key_env"] not in os.environ:
            print(f"skipping {cfg['key']}: {cfg['api_key_env']} not set")
            continue
        out.append(cfg)
    return out
