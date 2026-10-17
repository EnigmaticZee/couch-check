"""Couch Check: a Stremio addon that scores sex and nudity (0–10) from IMDb's parents guide.

Run:  .venv/bin/python server.py      then install http://localhost:7001/manifest.json in Stremio.
"""
from __future__ import annotations

import json
import logging
import os
import re
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

def _load_env_file(path: Path) -> None:
    """Reads KEY=value lines from .env (git-ignored) so keys never live in the code; real env vars win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(Path(__file__).parent / ".env")

from pg import cinemeta, classify, pages, rubric  # noqa: E402  (reads settings from the environment at import)
from pg.service import Service, score
from pg.store import Store

PORT = int(os.environ.get("PORT", "7001"))
PUBLIC_URL = os.environ.get("PG_PUBLIC_URL", "").rstrip("/")
# "off" shows scene counts per level with a link to IMDb instead of IMDb's scene descriptions
# (recommended for public instances; the descriptions are IMDb contributors' text).
SCENE_TEXT = os.environ.get("PG_SCENE_TEXT", "on").lower() != "off"
WAIT = float(os.environ.get("PG_WAIT", "8"))  # seconds a stream request waits for a first-time score
DB_PATH = os.environ.get("PG_DB", str(Path(__file__).parent / "cache.sqlite3"))

MANIFEST = {
    "id": "community.couchcheck",
    "version": "2.0.0",
    "name": "Couch Check",
    "description": "Beta. Know what to expect before watching with family: rates movies and shows 0–10 for sex and "
    "nudity using IMDb's parents guide. Shows a row in the stream list, and a chip on the main screen "
    "when placed above Cinemeta.",
    "resources": ["stream", "meta"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "sn:"],
    "catalogs": [],
    "behaviorHints": {"configurable": True},
}

STREAM_PATH = re.compile(r"^/stream/(movie|series)/(tt\d+)(?::\d+:\d+)?\.json$")
META_PATH = re.compile(r"^/meta/(movie|series)/(tt\d+)\.json$")
# The in-app breakdown is a virtual "movie" (so Stremio shows its stream list) with ID sn:<imdb id>.
BREAKDOWN_META_PATH = re.compile(r"^/meta/movie/sn:(tt\d+)\.json$")
BREAKDOWN_STREAM_PATH = re.compile(r"^/stream/movie/sn:(tt\d+)\.json$")
SERIES_TYPES = {"tvSeries", "tvMiniSeries"}
CHIP_CATEGORY = "Couch Check"  # header of the chip row on the main screen
ROW_WIDTH = 28  # characters per line in a breakdown row before Stremio truncates it
META_WAIT = 5.0  # the details screen waits on this for a first-time AI score; keep it short
META_CACHE = 300  # seconds Stremio may reuse metadata; short so score/rubric changes show up soon
GUIDE_PATH = re.compile(r"^/guide/(tt\d+)$")
STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/static/logo.png": (STATIC_DIR / "logo.png", "image/png"),
    "/static/logo.svg": (STATIC_DIR / "logo.svg", "image/svg+xml"),
    "/favicon.ico": (STATIC_DIR / "logo.png", "image/png"),
}
EST_SEGMENT = "/est"  # install-link setting: AI scores off, keyword estimates only
VERSION_PREFIX = re.compile(r"^/v\d+(?=/)")
HTTP_CACHE_MAX = 300  # Stremio's webview keeps responses on disk; cap how long it may reuse one

log = logging.getLogger("pg")
service = Service(Store(DB_PATH))


def stream_row(imdb_id: str, kind: str, base: str) -> tuple:
    """Returns (stream object, cacheMaxAge seconds)."""
    # Stremio ignores stremio:/// links on stream rows, so the row opens the web page; the main-screen chip goes in-app.
    url = web_url(base, imdb_id, kind == "series")
    entry = lookup(imdb_id, wait=WAIT)

    if entry is None:
        if imdb_id in service.errors and not service.is_working(imdb_id):
            return {"name": "⚠️ Couch Check", "description": "Couldn't reach IMDb.\nTry again later.", "externalUrl": url}, 0
        return {"name": "⏳ Couch Check", "description": "IMDb is slow to answer.\nReopen in a moment.", "externalUrl": url}, 0

    if entry["guide"].get("missing"):
        return {"name": "❔ Couch Check", "description": "No IMDb parents guide for this title.", "externalUrl": url}, 86400

    result, _ = score(entry)
    if result.level is None:
        lines = ["No scene details on IMDb"]
    else:
        lines = [f"{rubric.name(result.level)} · {'AI score' if source_label(entry) == 'AI' else 'Estimate'}"]
        detail = f"{result.scenes} scene{'s' if result.scenes != 1 else ''}"
        if result.prolonged:
            detail += f" · {result.prolonged} prolonged"
        lines.append(detail)
    extras = []
    if kind == "series":
        extras.append("Show-wide")
    if result.low_confidence:
        extras.append("Low confidence")
    if extras:
        lines.append(" · ".join(extras))
    lines.append("Tap: full list in browser ↗")
    level = "?" if result.level is None else str(result.level)
    return {
        "name": f"{rubric.emoji(result.level)} {level}/10",
        "description": "\n".join(lines),
        "externalUrl": url,
    }, 3600


def meta_with_score(imdb_id: str, kind: str, base: str) -> tuple:
    """Returns (Cinemeta's meta with a Couch Check chip added, cacheMaxAge), or (None, 0) if Cinemeta is unavailable."""
    meta = cinemeta.meta(kind, imdb_id)
    if not meta:
        return None, 0
    url = in_app_url(imdb_id)
    entry = lookup(imdb_id, wait=META_WAIT)
    if entry is None:
        label, max_age = "⏳ IMDb is slow, reopen in a moment", 0
    elif entry["guide"].get("missing"):
        label, max_age = "❔ No IMDb guide", 86400
    else:
        result, _ = score(entry)
        if result.level is None:
            label = "❔ No scene details"
        else:
            label = f"{rubric.emoji(result.level)} {result.level}/10 · {rubric.name(result.level)}"
            if result.scenes:
                label += f" · {result.scenes} scene{'s' if result.scenes != 1 else ''}"
            label += f" · {source_label(entry)}"
        max_age = META_CACHE

    chip = {"name": label, "category": CHIP_CATEGORY, "url": url}
    links = [l for l in meta.get("links") or [] if l.get("category") != CHIP_CATEGORY]
    # Right after the rating/share links, so it sits above Genres.
    at = next((i for i, l in enumerate(links) if l.get("category") not in ("imdb", "share")), len(links))
    return {**meta, "links": links[:at] + [chip] + links[at:]}, max_age


# Per-request setting from the install link: /est/ in the path means the viewer turned AI scores off.
REQUEST = threading.local()


def ai_wanted() -> bool:
    return getattr(REQUEST, "use_ai", True)


def lookup(imdb_id: str, wait: float):
    """The title's entry for this viewer: AI score if they want it, otherwise the keyword estimate."""
    if ai_wanted():
        return service.lookup(imdb_id, wait)
    return service.lookup_estimate(imdb_id, wait)


def source_label(entry: dict) -> str:
    """'AI' when an AI model scored the title, 'Est.' for the keyword estimate."""
    return "AI" if classify.is_ai(entry["classifier"]) else "Est."


def in_app_url(imdb_id: str) -> str:
    return f"stremio:///detail/movie/sn:{imdb_id}"


def web_url(base: str, imdb_id: str, is_series: bool) -> str:
    query = [q for q, on in (("series=1", is_series), ("est=1", not ai_wanted())) if on]
    return f"{base}/guide/{imdb_id}" + ("?" + "&".join(query) if query else "")


def breakdown_meta(imdb_id: str, base: str) -> tuple:
    """The in-app breakdown screen: the title's artwork, with the score as its summary. Returns (meta, cacheMaxAge)."""
    entry = lookup(imdb_id, wait=META_WAIT)
    guide = (entry or {}).get("guide") or {}
    is_series = guide.get("type") in SERIES_TYPES
    art = cinemeta.meta("series" if is_series else "movie", imdb_id) or {}
    title = guide.get("title") or art.get("name") or imdb_id
    meta = {
        "id": f"sn:{imdb_id}",
        "type": "movie",
        "name": f"{title} · Couch Check",
        "poster": art.get("poster"),
        "background": art.get("background"),
        "logo": art.get("logo"),
        "behaviorHints": {"defaultVideoId": f"sn:{imdb_id}"},
    }
    links = [{"name": "Open full page", "category": CHIP_CATEGORY, "url": web_url(base, imdb_id, is_series)}]

    if entry is None:
        meta["description"] = "Scoring… back out and reopen in a few seconds."
        meta["links"] = links
        return meta, 0
    if guide.get("missing"):
        meta["description"] = "IMDb has no parents guide for this title."
        return meta, 86400

    result, items = score(entry)
    if result.level is None:
        meta["releaseInfo"] = "❔ No scene details"
        meta["description"] = "IMDb has no flagged Sex & Nudity items for this title yet."
    else:
        meta["releaseInfo"] = f"{rubric.emoji(result.level)} {result.level}/10 · {rubric.name(result.level)} · {source_label(entry)}"
        facts = [f"{result.scenes} scene{'s' if result.scenes != 1 else ''} at level {rubric.SCENE_THRESHOLD}+"]
        if result.prolonged:
            facts.append(f"{result.prolonged} prolonged")
        if is_series:
            facts.append("show-wide")
        if result.low_confidence:
            facts.append("low confidence")
        facts.append("scored by AI" if source_label(entry) == "AI"
                     else "keyword estimate" + ("" if ai_wanted() else " (AI scores turned off)"))
        summary = entry.get("summary") if classify.is_ai(entry["classifier"]) else None
        meta["description"] = (
            f"{summary or rubric.auto_summary(result, items)} "
            f"{(lambda t: t[:1].upper() + t[1:])(' · '.join(facts))}. "
            + ("Every flagged scene is listed on the right, most explicit first. " if SCENE_TEXT
               else "Scene counts by level are on the right; the scene descriptions are on IMDb. ")
            + rubric.BETA_NOTICE
        )
    links.append({"name": f"Back to {title}", "category": CHIP_CATEGORY,
                  "url": f"stremio:///detail/{'series' if is_series else 'movie'}/{imdb_id}"})
    meta["links"] = links
    return meta, META_CACHE


def breakdown_rows(imdb_id: str, base: str) -> tuple:
    """Each flagged scene as a stream row, most explicit first. Returns (streams, cacheMaxAge)."""
    entry = lookup(imdb_id, wait=WAIT)
    if entry is None:
        return [{"name": "⏳", "description": "Scoring… back out and reopen in a few seconds.",
                 "externalUrl": web_url(base, imdb_id, False)}], 0
    guide = entry["guide"]
    if guide.get("missing"):
        return [], 86400
    url = web_url(base, imdb_id, guide.get("type") in SERIES_TYPES)
    _, items = score(entry)
    if not items:
        return [{"name": "❔", "description": "No flagged scenes on IMDb.", "externalUrl": url}], 3600
    if not SCENE_TEXT:
        return level_rows(imdb_id, items), 3600
    rows = []
    for i in sorted(items, key=lambda i: (-i.level, not i.is_scene)):
        tags = [t for t, on in (("Prolonged", i.prolonged), ("Overview", not i.is_scene), ("Spoiler", i.is_spoiler)) if on]
        rows.append({
            "name": f"{rubric.emoji(i.level)} {i.level}\n{rubric.name(i.level)}",
            # Stremio cuts each line of a row short, so wrap the text over several lines.
            "description": "\n".join(textwrap.wrap(i.text, ROW_WIDTH) + ([" · ".join(tags)] if tags else [])),
            "externalUrl": url,
        })
    return rows, 3600


def level_rows(imdb_id: str, items: list) -> list:
    """One row per level with its scene count, linking to IMDb for the descriptions (PG_SCENE_TEXT=off)."""
    rows = []
    for level in sorted({i.level for i in items}, reverse=True):
        in_level = [i for i in items if i.level == level]
        scenes = sum(1 for i in in_level if i.is_scene)
        prolonged = sum(1 for i in in_level if i.prolonged)
        counts = f"{scenes} scene{'s' if scenes != 1 else ''}" if scenes else "General comments"
        if prolonged:
            counts += f" · {prolonged} prolonged"
        rows.append({
            "name": f"{rubric.emoji(level)} {level}\n{rubric.name(level)}",
            "description": f"{counts}\nRead the scenes on IMDb ↗",
            "externalUrl": pages.imdb_url(imdb_id),
        })
    return rows


class Handler(BaseHTTPRequestHandler):
    def base_url(self) -> str:
        if PUBLIC_URL:
            return PUBLIC_URL
        proto = self.headers.get("X-Forwarded-Proto", "http")
        return f"{proto}://{self.headers.get('Host', f'localhost:{PORT}')}"

    def send(self, status: int, body, content_type: str, cache: int = 0) -> None:
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", f"max-age={min(cache, HTTP_CACHE_MAX)}" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj: dict, cache: int = 0) -> None:
        self.send(200, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", cache)

    def send_html(self, html: str, status: int = 200) -> None:
        self.send(status, html, "text/html; charset=utf-8")

    def do_OPTIONS(self) -> None:
        self.send(204, "", "text/plain")

    def do_GET(self) -> None:
        raw_path, _, query = self.path.partition("?")
        path = unquote(raw_path)  # Stremio sends episode IDs as tt123%3A1%3A2
        # Optional /vN prefix: installing from a new prefix sidesteps a manifest Stremio has cached.
        prefix_match = VERSION_PREFIX.match(path)
        prefix = prefix_match.group(0) if prefix_match else ""
        path = path[len(prefix):]
        REQUEST.use_ai = not (path.startswith(EST_SEGMENT + "/") or "est=1" in query)
        if path.startswith(EST_SEGMENT + "/"):
            path = path[len(EST_SEGMENT):]
        if path == "/manifest.json":
            return self.send_json({**MANIFEST, "logo": f"{self.base_url()}/static/logo.png"})
        if path in STATIC_FILES:
            file, content_type = STATIC_FILES[path]
            return self.send(200, file.read_bytes(), content_type, cache=86400)
        if path in ("/", "/configure"):
            return self.send_html(pages.configure(self.base_url() + prefix, MANIFEST["version"], ai_wanted()))

        m = BREAKDOWN_META_PATH.match(path)
        if m:
            meta, max_age = breakdown_meta(m.group(1), self.base_url())
            log.info("Breakdown %s -> %s", m.group(1), meta.get("releaseInfo", "pending"))
            return self.send_json({"meta": meta, "cacheMaxAge": max_age}, cache=max_age)
        m = BREAKDOWN_STREAM_PATH.match(path)
        if m:
            rows, max_age = breakdown_rows(m.group(1), self.base_url())
            return self.send_json({"streams": rows, "cacheMaxAge": max_age}, cache=max_age)

        m = STREAM_PATH.match(path)
        if m:
            kind, imdb_id = m.groups()
            row, max_age = stream_row(imdb_id, kind, self.base_url())
            log.info("Stream %s %s -> %s", kind, path.rsplit("/", 1)[-1], row["name"])
            return self.send_json({"streams": [row], "cacheMaxAge": max_age}, cache=max_age)
        m = META_PATH.match(path)
        if m:
            kind, imdb_id = m.groups()
            meta, max_age = meta_with_score(imdb_id, kind, self.base_url())
            if meta is None:
                return self.send(404, "Not found", "text/plain")
            chip = next(l["name"] for l in meta["links"] if l.get("category") == CHIP_CATEGORY)
            log.info("Meta %s %s -> %s", kind, imdb_id, chip)
            return self.send_json({"meta": meta, "cacheMaxAge": max_age}, cache=max_age)
        if path.startswith("/stream/"):
            return self.send_json({"streams": []}, cache=86400)
        if path.startswith("/meta/"):
            return self.send(404, "Not found", "text/plain")

        m = GUIDE_PATH.match(path)
        if m:
            return self.guide_page(m.group(1), is_series="series=1" in query, artwork="art=0" not in query)

        self.send(404, "Not found", "text/plain")

    def guide_page(self, imdb_id: str, is_series: bool, artwork: bool = True) -> None:
        entry = lookup(imdb_id, wait=0)
        if entry is None:
            if imdb_id in service.errors and not service.is_working(imdb_id):
                return self.send_html(pages.error(imdb_id, "Couldn't reach IMDb. Try again later."), 502)
            return self.send_html(pages.pending(imdb_id))
        if entry["guide"].get("missing"):
            return self.send_html(pages.missing(imdb_id), 404)
        result, items = score(entry)
        is_series = is_series or entry["guide"].get("type") in SERIES_TYPES
        art = (cinemeta.meta("series" if is_series else "movie", imdb_id) or {}) if artwork else {}
        self.send_html(pages.breakdown(imdb_id, entry, result, items, is_series, art.get("background"), SCENE_TEXT))

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", fmt % args, self.headers.get("User-Agent", "")[:40] if self.headers else "")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
    mode = classify.ai_label() if classify.ai_enabled() else "keyword matching (see README to enable AI scoring)"
    log.info("Classifier: %s", mode)
    log.info("Install in Stremio: http://localhost:%d/manifest.json", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
