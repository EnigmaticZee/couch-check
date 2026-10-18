"""The owner's own levels for titles, from data/overrides.json. They replace the computed level everywhere.

The file maps IMDb IDs to {"level": 0–10, "note": "why", "set": "YYYY-MM-DD"}, e.g.
{"tt0106179": {"level": 4, "note": "Nudity is sci-fi, not sexual", "set": "2026-10-17"}}
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path

from . import rubric

log = logging.getLogger("pg.overrides")

PATH = Path(os.environ.get("PG_OVERRIDES", Path(__file__).parent.parent / "data" / "overrides.json"))

_lock = threading.Lock()
_data: dict = {}
_mtime = None


def _current() -> dict:
    """The file's contents, reloaded whenever it changes on disk."""
    global _data, _mtime
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if mtime != _mtime:
            try:
                _data = json.loads(PATH.read_text())
                _mtime = mtime
                log.info("Loaded %d overrides from %s", len(_data), PATH.name)
            except (OSError, ValueError) as e:
                log.error("Could not read %s: %s", PATH, e)
        return _data


def apply(imdb_id: str, result: rubric.Result) -> rubric.Result:
    """The result with the owner's level in place of the computed one, if this title has an override."""
    o = _current().get(imdb_id)
    if not isinstance(o, dict) or not isinstance(o.get("level"), int) or not 0 <= o["level"] <= 10:
        return result
    return replace(result, level=o["level"], star=False,
                   override={"level": o["level"], "note": o.get("note") or "", "ai_level": result.level,
                             "ai_shown": result.shown})
