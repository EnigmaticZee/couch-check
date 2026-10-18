"""The only place production (server.py) calls into experiments.

With the experiments off (the default), every function here hands production's own answer straight back.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, List, Optional, Tuple

from pg import cinemeta
from pg.store import SERIES_TYPES

from . import ISCCI_MODE, enabled, episode_scores, iscci, tv_description
from .iscci import present
from .iscci import service as iscci_service

log = logging.getLogger("experimental")

ISCCI_ON = enabled(iscci.NAME)
CHIP_CATEGORY = "Couch Check"
META_CACHE = 300
PENDING_CACHE = 30  # while episode scores are still being worked out

_IN_APP_META = re.compile(r"^/meta/movie/si:(tt\d+)\.json$")
_IN_APP_STREAM = re.compile(r"^/stream/movie/si:(tt\d+)\.json$")
_PAGE = re.compile(r"^/iscci/guide/(tt\d+)$")
_EPISODE = re.compile(r"^/stream/series/(tt\d+):(\d+):(\d+)\.json$")


def init(production_service) -> None:
    if ISCCI_ON:
        iscci.init(production_service)
    if enabled_any():
        log.info("Experiments on: %s%s", ", ".join(sorted(_names())),
                 f" (ISCCI: {ISCCI_MODE})" if ISCCI_ON else "")


def _names():
    from . import ENABLED
    return ENABLED


def enabled_any() -> bool:
    return bool(_names())


def _rubrics() -> Tuple[str, ...]:
    """Which rubrics every request gets, set by PG_EXPERIMENTAL (off, both or only)."""
    if not ISCCI_ON:
        return ("v5.1",)
    return ("iscci",) if ISCCI_MODE == "only" else ("v5.1", "iscci")


# ---- manifest and configure page ------------------------------------------------------------------------------

def manifest(m: dict) -> dict:
    if not ISCCI_ON:
        return m
    return {**m, "idPrefixes": list(m.get("idPrefixes", [])) + [present.IN_APP_PREFIX]}


def configure(html: str) -> str:
    if not ISCCI_ON:
        return html
    what = ("scores with ISCCI, a new rubric being tested" if ISCCI_MODE == "only"
            else "also shows ISCCI, a new rubric being tested, next to the main score")
    note = (f'<div class="card notice"><b>Experimental rubric.</b> This server {what}. ISCCI scores the worst scene '
            f'plus extra points for how much there is.</div>')
    return html.replace('<div class="card"><h3>Install</h3>', note + '<div class="card"><h3>Install</h3>', 1)


# ---- ISCCI pieces ---------------------------------------------------------------------------------------------

def _web(base: str, imdb_id: str, is_series: bool, use_ai: bool) -> str:
    query = [q for q, on in (("series=1", is_series), ("est=1", not use_ai)) if on]
    return f"{base}/iscci/guide/{imdb_id}" + ("?" + "&".join(query) if query else "")


def _iscci_chip(imdb_id: str, wait: float, use_ai: bool, label: str) -> Tuple[str, int]:
    entry = iscci.get().lookup(imdb_id, wait, use_ai)
    if entry is None:
        return f"{label} ⏳ IMDb is slow, reopen in a moment".strip(), 0
    if entry["guide"].get("missing"):
        return f"{label} ❔ No IMDb guide".strip(), 86400
    result, _ = iscci_service.score(entry)
    return present.chip(result, entry, label), META_CACHE


def _insert_chips(meta: dict, chips: List[dict]) -> dict:
    links = [l for l in meta.get("links") or [] if l.get("category") != CHIP_CATEGORY]
    at = next((i for i, l in enumerate(links) if l.get("category") not in ("imdb", "share")), len(links))
    return {**meta, "links": links[:at] + chips + links[at:]}


def handle(handler, path: str, query: str, use_ai: bool, show_text: bool) -> bool:
    """Serves experiment-only routes (the ISCCI page and in-app breakdown). True if it answered."""
    if not ISCCI_ON:
        return False
    base = handler.base_url()
    m = _PAGE.match(path)
    if m:
        imdb_id = m.group(1)
        entry = iscci.get().lookup(imdb_id, 0, use_ai and "est=1" not in query)
        from pg import pages
        if entry is None:
            handler.send_html(pages.pending(imdb_id))
        elif entry["guide"].get("missing"):
            handler.send_html(pages.missing(imdb_id), 404)
        else:
            result, items = iscci_service.score(entry)
            is_series = "series=1" in query or entry["guide"].get("type") in SERIES_TYPES
            art = cinemeta.meta("series" if is_series else "movie", imdb_id) or {}
            handler.send_html(present.page(imdb_id, entry, result, items, is_series, art.get("background"), show_text))
        return True
    m = _IN_APP_META.match(path)
    if m:
        imdb_id = m.group(1)
        entry = iscci.get().lookup(imdb_id, 5.0, use_ai)
        guide = (entry or {}).get("guide") or {}
        is_series = guide.get("type") in SERIES_TYPES
        art = cinemeta.meta("series" if is_series else "movie", imdb_id) or {}
        result, items = iscci_service.score(entry) if entry and not guide.get("missing") else (None, [])
        meta = present.breakdown_meta(imdb_id, entry, result, items, art, guide.get("title") or art.get("name") or imdb_id,
                                      is_series, _web(base, imdb_id, is_series, use_ai), show_text)
        handler.send_json({"meta": meta, "cacheMaxAge": META_CACHE if entry else 0}, cache=META_CACHE if entry else 0)
        return True
    m = _IN_APP_STREAM.match(path)
    if m:
        imdb_id = m.group(1)
        entry = iscci.get().lookup(imdb_id, 8.0, use_ai)
        if entry is None or entry["guide"].get("missing"):
            handler.send_json({"streams": [], "cacheMaxAge": 0})
            return True
        result, items = iscci_service.score(entry)
        web = _web(base, imdb_id, entry["guide"].get("type") in SERIES_TYPES, use_ai)
        rows = present.breakdown_rows(imdb_id, entry, result, items, web, show_text) if items else []
        handler.send_json({"streams": rows, "cacheMaxAge": 3600}, cache=3600)
        return True
    return False


# ---- meta (main screen) ---------------------------------------------------------------------------------------

def meta(imdb_id: str, kind: str, base: str, use_ai: bool, user_agent: str,
         production: Callable[[], tuple]) -> Tuple[Optional[dict], int, Optional[dict]]:
    """(meta, cacheMaxAge, extra headers). Calls production only when v5.1 is wanted."""
    rubrics = _rubrics()
    if "v5.1" in rubrics:
        result, max_age = production()
    else:
        result, max_age = cinemeta.meta(kind, imdb_id), META_CACHE
    if result is None:
        return None, 0, None
    chips = [l["name"] for l in result.get("links") or [] if l.get("category") == CHIP_CATEGORY]
    if "iscci" in rubrics:
        label = "ISCCI" if len(rubrics) > 1 else ""
        text, age = _iscci_chip(imdb_id, 5.0, use_ai, label)
        chip = {"name": text, "category": CHIP_CATEGORY, "url": f"stremio:///detail/movie/{present.IN_APP_PREFIX}{imdb_id}"}
        existing = [l for l in result.get("links") or [] if l.get("category") == CHIP_CATEGORY]
        result = _insert_chips(result, existing + [chip])
        chips.append(text)
        max_age = min(max_age, age) if max_age else age
    if kind == "series" and episode_scores.is_on():
        result, pending = _annotate_episodes(imdb_id, result, rubrics)
        if pending:
            max_age = min(max_age, PENDING_CACHE)
    headers = None
    if tv_description.is_on():  # the answer differs by device, so caches must keep them apart
        result = tv_description.apply(result, chips, user_agent)
        headers = {"Vary": "User-Agent"}
    return result, max_age, headers


def _annotate_episodes(show_id: str, meta: dict, rubrics) -> Tuple[dict, bool]:
    rec = episode_scores.episodes.guides(show_id, rubrics)
    pending = rec is None or episode_scores.episodes.is_working(show_id)
    if not rec:
        return meta, pending
    videos = []
    for v in meta.get("videos") or []:
        labels, marks = [], []
        for r in rubrics:
            found = episode_scores.episodes.result(rec, v.get("season"), v.get("episode", v.get("number")), r)
            if not found or found[0].level is None:
                continue
            res, entry = found
            src = "AI" if entry["classifier"].startswith("ai:") else "Est."
            prefix = "ISCCI " if r == "iscci" and len(rubrics) > 1 else ""
            if r == "iscci":
                labels.append(f"{prefix}{present.short(res)} · {src}")
                marks.append(f"{iscci.rubric.emoji(res.level)}{res.shown.split('/')[0]}")
            else:
                from pg import rubric as base_rubric
                labels.append(f"{base_rubric.emoji(res.level)} {res.level}/10 · {base_rubric.name(res.level)} · {src}")
                marks.append(f"{base_rubric.emoji(res.level)}{res.level}")
        if labels:
            line = "Couch Check: " + " | ".join(labels)
            v = {**v, "name": f"{v.get('name') or v.get('title') or ''} · {marks[-1]}"}
            for field in ("overview", "description"):
                if v.get(field) or field == "overview":
                    v[field] = f"{line}\n\n{v.get(field) or ''}".strip()
        videos.append(v)
    return {**meta, "videos": videos}, pending


# ---- streams (source list) ------------------------------------------------------------------------------------

def streams(imdb_id: str, kind: str, path: str, base: str, use_ai: bool,
            production: Callable[[], tuple]) -> Tuple[List[dict], int]:
    """(rows, cacheMaxAge). Calls production only when v5.1 is wanted."""
    rubrics = _rubrics()
    episode = _EPISODE.match(path)
    rec = None
    if episode and episode_scores.is_on():
        rec = episode_scores.episodes.guides(imdb_id, rubrics)
    rows, max_age = [], 3600
    for r in rubrics:
        found = rec and episode_scores.episodes.result(rec, episode.group(2), episode.group(3), r)
        if found and found[0].level is not None:
            res, entry = found
            rows.append(_episode_row(r, res, entry, base, imdb_id, use_ai, len(rubrics) > 1))
            continue
        if r == "v5.1":
            row, age = production()
            rows.append(row)
            max_age = min(max_age, age)
        else:
            entry = iscci.get().lookup(imdb_id, 8.0, use_ai)
            web = _web(base, imdb_id, kind == "series", use_ai)
            if entry is None:
                rows.append({"name": "⏳ ISCCI", "description": "IMDb is slow to answer.\nReopen in a moment.", "externalUrl": web})
                max_age = 0
            elif entry["guide"].get("missing"):
                rows.append({"name": "❔ ISCCI", "description": "No IMDb parents guide for this title.", "externalUrl": web})
            else:
                res, _ = iscci_service.score(entry)
                rows.append(present.stream_row(res, entry, web, "Show-wide" if kind == "series" else "",
                                               "ISCCI" if len(rubrics) > 1 else ""))
    return rows, max_age


def _episode_row(r: str, res, entry: dict, base: str, show_id: str, use_ai: bool, both: bool) -> dict:
    if r == "iscci":
        return present.stream_row(res, entry, _web(base, show_id, True, use_ai), "This episode", "ISCCI" if both else "")
    from pg import rubric as base_rubric
    src = "AI score" if entry["classifier"].startswith("ai:") else "Estimate"
    lines = [f"{base_rubric.name(res.level)} · {src}", f"{res.scenes} scene{'s' if res.scenes != 1 else ''}",
             "This episode", "Tap: full list in browser ↗"]
    query = "?series=1" + ("" if use_ai else "&est=1")
    return {"name": f"{base_rubric.emoji(res.level)} {res.level}/10", "description": "\n".join(lines),
            "externalUrl": f"{base}/guide/{show_id}{query}"}
