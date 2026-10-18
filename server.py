"""Couch Check: a Stremio addon that scores sex and nudity (0–10) from IMDb's parents guide.

Run:  .venv/bin/python server.py      then install http://localhost:7001/manifest.json in Stremio.
"""
from __future__ import annotations

import hmac
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

from pg import cinemeta, classify, overrides, pages, rubric, scores  # noqa: E402  (reads settings from the environment at import)
from pg.service import Service, score
from pg.store import Store
from experimental import hooks as experiments  # off unless switched on in the environment; see experimental/

PORT = int(os.environ.get("PORT", "7001"))
PUBLIC_URL = os.environ.get("PG_PUBLIC_URL", "").rstrip("/")
# Off by default: breakdowns show scene counts per level with a link to IMDb, since the descriptions are IMDb
# contributors' text. "on" shows the descriptions, which is fine for a copy only you use.
SCENE_TEXT = os.environ.get("PG_SCENE_TEXT", "off").lower() == "on"
# Secret that lets tools/save_scores.py download this server's AI scores (GET /export/scores.json
# with "Authorization: Bearer <token>"). The endpoint doesn't exist while this is unset.
EXPORT_TOKEN = os.environ.get("PG_EXPORT_TOKEN", "")
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
HTTP_CACHE_MAX = 300  # Stremio's webview keeps responses on disk; cap how long it may reuse one

log = logging.getLogger("pg")
service = Service(Store(DB_PATH))
experiments.init(service)


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

    result, _ = scored(imdb_id, entry)
    if result.level is None:
        lines = ["No scene details on IMDb"]
    elif result.override:
        lines = [f"{rubric.name(result.level)} · Your Score", f"AI Said {result.override['ai_shown']}"]
    else:
        lines = [f"{rubric.name(result.level)} · {'AI Score' if source_label(entry) == 'AI' else 'Estimate'}"]
    if result.level is not None:
        detail = f"{result.scenes} Scene{'s' if result.scenes != 1 else ''}"
        if result.prolonged:
            detail += f" · {result.prolonged} Prolonged"
        lines.append(detail)
        if result.star:
            lines.append("* Non-Sexual Nudity")
    if result.warnings:
        lines.append(f"⚠️ {rubric.warning_text(result)}")
    extras = []
    if kind == "series":
        extras.append("Show-Wide")
    if result.low_confidence:
        extras.append("Low Confidence")
    if extras:
        lines.append(" · ".join(extras))
    lines.append("Tap: Full List in Browser ↗")
    return {
        "name": f"{rubric.emoji(result.level)} {result.shown}",
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
        result, _ = scored(imdb_id, entry)
        if result.level is None:
            label = "❔ No scene details"
        elif result.override:
            label = f"{rubric.emoji(result.level)} {result.level}/10 · {rubric.name(result.level)} · Your Score"
        else:
            label = f"{rubric.emoji(result.level)} {result.shown} · {rubric.name(result.level)}"
            if result.scenes:
                label += f" · {result.scenes} Scene{'s' if result.scenes != 1 else ''}"
            label += f" · {source_label(entry)}"
            if result.warnings:
                label += " · ⚠️"
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


def scored(imdb_id: str, entry: dict) -> tuple:
    """score() with the owner's override (data/overrides.json) applied. Returns (result, items)."""
    result, items = score(entry)
    return overrides.apply(imdb_id, result), items


def why_text(result: rubric.Result) -> str:
    """What set the level, for "why this score": the AI's note of what's shown, or the item itself."""
    top = result.top
    if top is None:
        return ""
    text = top.shown or (top.text if SCENE_TEXT else "")
    if top.nonsexual and text:
        text += f" (Non-Sexual: {rubric.NONSEXUAL[top.nonsexual]})"
    return text


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

    result, items = scored(imdb_id, entry)
    if result.level is None:
        meta["releaseInfo"] = "❔ No scene details"
        meta["description"] = "IMDb has no flagged Sex & Nudity items for this title yet."
    else:
        meta["releaseInfo"] = (f"{rubric.emoji(result.level)} {result.shown} · {rubric.name(result.level)} · "
                               + ("Your Score" if result.override else source_label(entry))
                               + (" · ⚠️" if result.warnings else ""))
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
        warning = (f"⚠️ Content warning: {rubric.warning_text(result)} (flagged for awareness; it doesn't change the score). "
                   if result.warnings else "")
        why = why_text(result)
        why = f"Score set by: {why}. " if why and not result.override else ""
        if result.override:
            why = (f"Your score: {result.level}/10" + (f" ({result.override['note']})" if result.override["note"] else "")
                   + f"; the AI said {result.override['ai_shown']}. ")
        meta["description"] = (
            warning + why + f"{summary or rubric.auto_summary(result, items)} "
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
    result, items = scored(imdb_id, entry)
    if not items:
        return [{"name": "❔", "description": "No flagged scenes on IMDb.", "externalUrl": url}], 3600
    summary = (entry.get("summary") if classify.is_ai(entry["classifier"]) else None) or rubric.auto_summary(result, items)
    # The summary first, so it's visible in the list without reading the left side.
    rows = [{"name": "📝\nIn short", "description": "\n".join(textwrap.wrap(summary, ROW_WIDTH)), "externalUrl": url}]
    if result.override:
        note = f"{result.override['note']}. " if result.override["note"] else ""
        rows.append({"name": f"✋\nYour Score", "externalUrl": url, "description": "\n".join(
            textwrap.wrap(f"{note}You set {result.level}/10; the AI said {result.override['ai_shown']}.", ROW_WIDTH))})
    elif why_text(result):
        rows.append({"name": f"🔎\nWhy {result.shown}", "externalUrl": url,
                     "description": "\n".join(textwrap.wrap(f"Score set by: {why_text(result)}", ROW_WIDTH))})
    if result.warnings:
        rows.append({"name": "⚠️\nWarning", "externalUrl": url, "description": "\n".join(
            textwrap.wrap(f"{rubric.warning_text(result)}: flagged for awareness; it doesn't change the score.", ROW_WIDTH))})
    if not SCENE_TEXT:
        return rows + level_rows(imdb_id, items), 3600
    # Warning items first (they're level 0, so they'd otherwise sink to the bottom).
    for i in sorted(items, key=lambda i: (not i.unscored, -i.level, not i.is_scene)):
        tags = [t for t, on in ((f"⚠️ {rubric.WARNINGS.get(i.warning, '')}", bool(i.warning) and not i.unscored),
                                (f"Non-Sexual: {rubric.NONSEXUAL.get(i.nonsexual, '')}", bool(i.nonsexual)),
                                ("Prolonged", i.prolonged), ("Overview", not i.is_scene), ("Spoiler", i.is_spoiler)) if on]
        star = "*" if i.nonsexual else ""
        rows.append({
            "name": f"⚠️\nWarning" if i.unscored else f"{rubric.emoji(i.level)} {i.level}{star}\n{rubric.name(i.level)}",
            # Stremio cuts each line of a row short, so wrap the text over several lines.
            "description": "\n".join(textwrap.wrap(i.text, ROW_WIDTH) + ([" · ".join(tags)] if tags else [])),
            "externalUrl": url,
        })
    return rows, 3600


def level_rows(imdb_id: str, items: list) -> list:
    """One row per level with its scene count, linking to IMDb for the descriptions (PG_SCENE_TEXT=off)."""
    rows = []
    warned = [i for i in items if i.unscored]
    if warned:
        rows.append({"name": "⚠️\nWarning", "externalUrl": pages.imdb_url(imdb_id),
                     "description": f"{len(warned)} Item{'s' if len(warned) != 1 else ''}\nRead Them on IMDb ↗"})
    for level in sorted({i.level for i in items if not i.unscored}, reverse=True):
        in_level = [i for i in items if i.level == level and not i.unscored]
        scenes = sum(1 for i in in_level if i.is_scene)
        prolonged = sum(1 for i in in_level if i.prolonged)
        counts = f"{scenes} Scene{'s' if scenes != 1 else ''}" if scenes else "General Comments"
        if prolonged:
            counts += f" · {prolonged} Prolonged"
        rows.append({
            "name": f"{rubric.emoji(level)} {level}\n{rubric.name(level)}",
            "description": f"{counts}\nRead the Scenes on IMDb ↗",
            "externalUrl": pages.imdb_url(imdb_id),
        })
    return rows


class Handler(BaseHTTPRequestHandler):
    def base_url(self) -> str:
        if PUBLIC_URL:
            return PUBLIC_URL
        host = self.headers.get("Host", f"localhost:{PORT}")
        # Hosts terminate HTTPS in front of the app and don't always say so; only local addresses are plain http.
        local = host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]") or host.startswith("192.168.")
        proto = self.headers.get("X-Forwarded-Proto") or ("http" if local else "https")
        return f"{proto}://{host}"

    def send(self, status: int, body, content_type: str, cache: int = 0, headers: dict = None) -> None:
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", f"max-age={min(cache, HTTP_CACHE_MAX)}" if cache else "no-cache")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":  # HEAD gets the same status and headers, without the body
            self.wfile.write(data)

    def send_json(self, obj: dict, cache: int = 0, headers: dict = None) -> None:
        self.send(200, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", cache, headers)

    def send_html(self, html: str, status: int = 200) -> None:
        self.send(status, html, "text/html; charset=utf-8")

    def do_HEAD(self) -> None:
        """Uptime monitors often check with HEAD; answer it like GET."""
        self.do_GET()

    def do_OPTIONS(self) -> None:
        self.send(204, "", "text/plain")

    def do_GET(self) -> None:
        raw_path, _, query = self.path.partition("?")
        path = unquote(raw_path)  # Stremio sends episode IDs as tt123%3A1%3A2
        REQUEST.use_ai = not (path.startswith(EST_SEGMENT + "/") or "est=1" in query)
        if path.startswith(EST_SEGMENT + "/"):
            path = path[len(EST_SEGMENT):]
        if path == "/export/scores.json":
            return self.export_scores()
        if path == "/manifest.json":
            return self.send_json(experiments.manifest({**MANIFEST, "logo": f"{self.base_url()}/static/logo.png"}))
        if path in STATIC_FILES:
            file, content_type = STATIC_FILES[path]
            return self.send(200, file.read_bytes(), content_type, cache=86400)
        if path in ("/", "/configure"):
            return self.send_html(experiments.configure(pages.configure(self.base_url(), MANIFEST["version"], ai_wanted())))
        if experiments.handle(self, path, query, ai_wanted(), SCENE_TEXT):
            return

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
            rows, max_age = experiments.streams(imdb_id, kind, path, self.base_url(), ai_wanted(),
                                                lambda: stream_row(imdb_id, kind, self.base_url()))
            log.info("Stream %s %s -> %s", kind, path.rsplit("/", 1)[-1], " | ".join(r["name"] for r in rows))
            return self.send_json({"streams": rows, "cacheMaxAge": max_age}, cache=max_age)
        m = META_PATH.match(path)
        if m:
            kind, imdb_id = m.groups()
            meta, max_age, headers = experiments.meta(imdb_id, kind, self.base_url(), ai_wanted(),
                                                      self.headers.get("User-Agent", ""),
                                                      lambda: meta_with_score(imdb_id, kind, self.base_url()))
            if meta is None:
                return self.send(404, "Not found", "text/plain")
            chips = [l["name"] for l in meta.get("links") or [] if l.get("category") == CHIP_CATEGORY]
            log.info("Meta %s %s -> %s", kind, imdb_id, " | ".join(chips))
            return self.send_json({"meta": meta, "cacheMaxAge": max_age}, cache=max_age, headers=headers)
        if path.startswith("/stream/"):
            return self.send_json({"streams": []}, cache=86400)
        if path.startswith("/meta/"):
            return self.send(404, "Not found", "text/plain")

        m = GUIDE_PATH.match(path)
        if m:
            return self.guide_page(m.group(1), is_series="series=1" in query)

        self.send(404, "Not found", "text/plain")

    def export_scores(self) -> None:
        """This server's AI scores in data/scores.json format (item levels and summaries, no IMDb text)."""
        sent = self.headers.get("Authorization", "")
        if not EXPORT_TOKEN or not hmac.compare_digest(sent.encode(), f"Bearer {EXPORT_TOKEN}".encode()):
            return self.send(404, "Not found", "text/plain")
        titles = scores.from_cache(service.store.ai_scored(rubric.VERSION))
        self.send_json({"rubric_version": rubric.VERSION, "titles": titles})

    def guide_page(self, imdb_id: str, is_series: bool) -> None:
        entry = lookup(imdb_id, wait=0)
        if entry is None:
            if imdb_id in service.errors and not service.is_working(imdb_id):
                return self.send_html(pages.error(imdb_id, "Couldn't reach IMDb. Try again later."), 502)
            return self.send_html(pages.pending(imdb_id))
        if entry["guide"].get("missing"):
            return self.send_html(pages.missing(imdb_id), 404)
        result, items = scored(imdb_id, entry)
        is_series = is_series or entry["guide"].get("type") in SERIES_TYPES
        art = cinemeta.meta("series" if is_series else "movie", imdb_id) or {}
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
