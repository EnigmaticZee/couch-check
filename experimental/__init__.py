"""Experiments that can be switched on per server and removed by deleting this folder.

Turn one on with PG_EXPERIMENTAL, a comma-separated list of experiment names (e.g. PG_EXPERIMENTAL=tv_description).
With it unset, nothing here changes how Couch Check behaves. Production code may call into here; nothing here
is required for production to run.
"""
from __future__ import annotations

import os

ENABLED = {name.strip().lower() for name in os.environ.get("PG_EXPERIMENTAL", "").split(",") if name.strip()}


def enabled(name: str) -> bool:
    return name in ENABLED
