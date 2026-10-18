"""Experiments that can be switched on per server and removed by deleting this folder.

Settings (all off by default except the Samsung TV line):
- PG_EXPERIMENTAL: the ISCCI rubric. "off" (base rubric only), "both" (base and ISCCI side by side), "only" (ISCCI alone).
- PG_EPISODE_SCORES: "on" scores episodes that have their own IMDb guide.
- PG_TV_DESCRIPTION: "off" stops putting the score in the description for Samsung TVs (see tv_description.py).
Production code may call into here; nothing here is required for production to run.
"""
from __future__ import annotations

import os

_ISCCI = os.environ.get("PG_EXPERIMENTAL", "off").strip().lower()
ISCCI_MODE = _ISCCI if _ISCCI in ("both", "only") else "off"
EPISODE_SCORES = os.environ.get("PG_EPISODE_SCORES", "off").strip().lower() == "on"

ENABLED = {name for name, on in (("iscci", ISCCI_MODE != "off"), ("episode_scores", EPISODE_SCORES)) if on}


def enabled(name: str) -> bool:
    return name in ENABLED
