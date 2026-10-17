"""AI scores made offline (see tools/), published in data/scores.json.

The file holds only our own derived data: for each title, IMDb's item IDs mapped to
[level, is_scene, prolonged]. IMDb's text is never stored in it; the server fetches
that live, one title at a time, and matches it to these scores by item ID.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

from . import rubric

log = logging.getLogger("pg.scores")

PATH = Path(os.environ.get("PG_SCORES", Path(__file__).parent.parent / "data" / "scores.json"))

# A title counts as AI-scored when at least this share of its current IMDb items has a score;
# below that (IMDb gained many new items since), it falls back to the estimate until re-scored.
MIN_COVERAGE = 0.8

_lock = threading.Lock()
_data: dict = {"rubric_version": rubric.VERSION, "titles": {}}
_mtime = None


def _current() -> dict:
    """The file's contents, reloaded whenever it changes on disk."""
    global _data, _mtime
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return _data
    with _lock:
        if mtime != _mtime:
            try:
                _data = json.loads(PATH.read_text())
                _mtime = mtime
                log.info("Loaded %d AI-scored titles from %s", len(_data.get("titles", {})), PATH.name)
            except (OSError, ValueError) as e:
                log.error("Could not read %s: %s", PATH, e)
        return _data


def lookup(imdb_id: str, items: List[dict], fallback) -> Optional[List[dict]]:
    """AI scores aligned with `items` (IMDb's current items), or None if this title isn't AI-scored.

    Items without a stored score (added to IMDb after scoring) use `fallback(text)`.
    """
    data = _current()
    if data.get("rubric_version") != rubric.VERSION:
        return None
    title = data.get("titles", {}).get(imdb_id)
    if not title or not items:
        return None
    stored = title.get("items", {})
    if sum(1 for i in items if i["id"] in stored) < MIN_COVERAGE * len(items):
        return None
    out = []
    for i in items:
        s = stored.get(i["id"])
        out.append({"level": s[0], "is_scene": bool(s[1]), "prolonged": bool(s[2])} if s else fallback(i["text"]))
    return out


def titles() -> dict:
    return _current().get("titles", {})
