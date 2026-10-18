"""Saved AI scores in data/scores.json (written by tools/save_scores.py).

The file holds only our own derived data: for each title, IMDb's item IDs mapped to
[level, is_scene, prolonged, warning key or null, non-sexual key or null, what's shown], with trailing empty values
left off. IMDb's text is never stored in it; the server fetches
that live, one title at a time, and matches it to these scores by item ID.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
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
        out.append({"level": s[0], "is_scene": bool(s[1]), "prolonged": bool(s[2]),
                    "warning": s[3] if len(s) > 3 else None, "nonsexual": s[4] if len(s) > 4 else None,
                    "shown": s[5] if len(s) > 5 else ""} if s else fallback(i["text"]))
    return out


def titles() -> dict:
    return _current().get("titles", {})


def load_file() -> dict:
    """data/scores.json as a dict, or a fresh one for the current rubric version."""
    if PATH.exists():
        return json.loads(PATH.read_text())
    return {"rubric_version": rubric.VERSION, "titles": {}}


def save_file(data: dict) -> None:
    """Writes data/scores.json with one line per item, so an update reads as a clear diff."""
    titles = []
    for imdb_id, t in sorted(data["titles"].items()):
        items = ",\n".join(f'   "{k}": {json.dumps(v)}' for k, v in t["items"].items())
        summary = f'"summary": {json.dumps(t["summary"], ensure_ascii=False)}, ' if t.get("summary") else ""
        titles.append(f'  "{imdb_id}": {{"title": {json.dumps(t["title"], ensure_ascii=False)}, '
                      f'"scored": "{t["scored"]}", {summary}"items": {{\n{items}\n  }}}}')
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(f'{{\n "rubric_version": {data["rubric_version"]},\n "titles": {{\n'
                    + ",\n".join(titles) + "\n }\n}\n")


def _packed(c: dict) -> list:
    """One item's scores as a list, dropping empty values from the end."""
    values = [c["level"], int(bool(c["is_scene"])), int(bool(c["prolonged"])), c.get("warning"), c.get("nonsexual"),
              c.get("shown") or None]
    while len(values) > 3 and values[-1] is None:
        values.pop()
    return values


def from_cache(rows) -> dict:
    """Builds scores-file titles from cached AI scores: {imdb_id: {title, scored, summary, items}}.

    `rows` are store entries (with imdb_id) scored by the AI under the current rubric version.
    """
    out = {}
    for e in rows:
        guide = e["guide"]
        items = guide.get("items") or []
        if not items or not e["classified"] or len(e["classified"]) != len(items):
            continue
        out[e["imdb_id"]] = {
            "title": guide.get("title") or e["imdb_id"],
            "scored": date.fromtimestamp(e["fetched_at"]).isoformat(),
            "summary": e.get("summary"),
            "items": {i["id"]: _packed(c) for i, c in zip(items, e["classified"])},
        }
    return out
