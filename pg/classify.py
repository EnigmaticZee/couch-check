"""Assigns each IMDb Sex & Nudity item a level on the ladder.

An AI model reads the free-text items when one is configured: any OpenAI-compatible
API such as Groq's free tier (PG_AI_BASE_URL + PG_AI_API_KEY + PG_AI_MODEL).
Otherwise, or when the AI is unavailable or over its limits, a keyword matcher
gives a rougher estimate.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from typing import List, Optional, Tuple

from . import rubric

log = logging.getLogger("pg.classify")

# Any OpenAI-compatible chat API (e.g. Groq's free tier).
AI_BASE_URL = os.environ.get("PG_AI_BASE_URL", "").rstrip("/")
AI_API_KEY = os.environ.get("PG_AI_API_KEY", "")
AI_MODEL = os.environ.get("PG_AI_MODEL", "")
# Reasoning models (e.g. gpt-oss) think before answering, and that counts against token limits;
# light thinking is plenty for placing items on the ladder.
AI_REASONING_EFFORT = os.environ.get("PG_AI_REASONING_EFFORT") or ("low" if "gpt-oss" in AI_MODEL else "")


def provider() -> Optional[str]:
    if os.environ.get("PG_CLASSIFIER") == "keywords":
        return None
    if AI_BASE_URL and AI_API_KEY and AI_MODEL:
        return "openai"
    return None


def ai_enabled() -> bool:
    return provider() is not None


def ai_label() -> str:
    return f"{AI_MODEL} via {AI_BASE_URL}" if provider() else ""


def is_ai(classifier: Optional[str]) -> bool:
    return bool(classifier) and classifier.split(":", 1)[0] == "ai"


class Limiter:
    """Paces AI calls for free-tier rate limits, backs off when told to, and caps calls per day."""

    def __init__(self, min_interval: float, daily_limit: int):
        self.min_interval = min_interval
        self.daily_limit = daily_limit
        self._lock = threading.Lock()
        self._last = 0.0
        self._paused_until = 0.0
        self._day = date.today()
        self._count = 0

    def acquire(self) -> bool:
        """Waits for this call's turn. False if paused or over today's cap (the caller uses keywords)."""
        with self._lock:
            if date.today() != self._day:
                self._day, self._count = date.today(), 0
            if time.time() < self._paused_until:
                return False
            if self._count >= self.daily_limit:
                log.warning("Daily AI limit (%d) reached; using keyword matching until tomorrow", self.daily_limit)
                return False
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self._count += 1
            return True

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._paused_until = max(self._paused_until, time.time() + seconds)


limiter = Limiter(
    min_interval=float(os.environ.get("PG_AI_MIN_INTERVAL", "1.5")),
    daily_limit=int(os.environ.get("PG_AI_DAILY_LIMIT", "500")),
)

LADDER_TEXT = "\n".join(f"{lvl} — {name}: {desc}" for lvl, name, desc in rubric.LADDER)

RULES_TEXT = "\n".join(f"- {r}" for r in rubric.RULES)

SYSTEM = f"""You score movie and TV content for sexual content and nudity only, so a viewer can decide whether to watch something with their spouse or family.

You will receive numbered user-written items from a title's IMDb parents guide "Sex & Nudity" section. Score every item on this ladder:

{LADDER_TEXT}

Rules:
{RULES_TEXT}
- is_scene: true if the item describes a specific scene or moment; false if it is a general overview or opinion ("overall mild", "lots of sexual content throughout", "not as bad as other shows").
- prolonged: true if the item says the content is long, lingering, extended, repeated, throughout, or lasts more than a few seconds.

Return one entry per item, using the item's number as index.

Also write a summary: 2 or 3 short, plain sentences for someone deciding whether to watch this with family. Say what the most explicit content is and how often it happens, then what most of the rest is like. Be factual and specific (e.g. "brief breast nudity in the first episode"), use no character names, give away no plot, don't quote the items, don't mention level numbers, call clothing clothing (underwear is not nudity), and don't judge who it is suitable for (no age or audience advice): describe, and let the reader decide."""

# APIs vary in structured-output support, so ask for the shape in words and validate it.
JSON_INSTRUCTION = """

Respond with JSON only, in exactly this shape:
{"summary": "...", "items": [{"index": 0, "level": 0, "is_scene": true, "prolonged": false}, ...]}"""



def classify(texts: List[str]) -> Tuple[List[dict], str, Optional[str]]:
    """Returns ([{level, is_scene, prolonged}, ...] aligned with texts, classifier name, AI summary or None)."""
    if not texts:
        return [], "none", None
    if provider() and limiter.acquire():
        result = _with_openai(texts)
        if result is not None:
            return result[0], f"ai:{AI_MODEL}@{rubric.VERSION}", result[1]
    return keywords(texts), "keywords", None


def _summary(value) -> Optional[str]:
    """The model's summary, trimmed to something sensible for a page, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())[:600]


def _numbered(texts: List[str]) -> str:
    return "\n".join(f"{i}. {json.dumps(t, ensure_ascii=False)}" for i, t in enumerate(texts))


def _align(texts: List[str], entries: list) -> List[dict]:
    """Matches model output to items by index; anything missing or malformed falls back to keywords."""
    by_index = {}
    for e in entries if isinstance(entries, list) else []:
        if (isinstance(e, dict) and isinstance(e.get("index"), int) and isinstance(e.get("level"), int)
                and 0 <= e["level"] <= 10):
            by_index[e["index"]] = e
    out = []
    for i, t in enumerate(texts):
        e = by_index.get(i)
        out.append(
            {"level": e["level"], "is_scene": bool(e.get("is_scene", True)), "prolonged": bool(e.get("prolonged"))}
            if e else _with_keywords(t)
        )
    return out


def _with_openai(texts: List[str]) -> Optional[Tuple[List[dict], Optional[str]]]:
    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM + JSON_INSTRUCTION},
            {"role": "user", "content": f"Items:\n{_numbered(texts)}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    if AI_REASONING_EFFORT:
        body["reasoning_effort"] = AI_REASONING_EFFORT
    req = urllib.request.Request(
        f"{AI_BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        # Some providers (Groq, behind Cloudflare) reject Python's default User-Agent with a 403.
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AI_API_KEY}",
                 "User-Agent": "couch-check/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = e.headers.get("Retry-After")
            seconds = float(retry) if retry and retry.replace(".", "", 1).isdigit() else 60
            limiter.pause(seconds)
            log.warning("AI rate limited; pausing AI scoring for %ds", seconds)
        elif e.code in (401, 403):
            limiter.pause(3600)
            log.error("AI provider rejected the API key (HTTP %d); pausing AI scoring for an hour", e.code)
        else:
            log.warning("AI provider error HTTP %d; using keyword matching", e.code)
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("Could not reach AI provider (%s); using keyword matching", e)
        return None

    try:
        text = data["choices"][0]["message"]["content"] or ""
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        parsed = json.loads(text)
        return _align(texts, parsed.get("items")), _summary(parsed.get("summary"))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        log.warning("AI provider returned an unexpected response; using keyword matching")
        return None


# Highest level first; an item gets the first level whose pattern matches a non-negated sentence.
_KEYWORDS = [
    (10, r"oral sex|graphic sex|explicit sex|pornograph|unsimulated|erect|penetrat|orgy|orgies"),
    (9, r"full[- ]frontal|frontal nudity|genital|penis|vagina|pubic"),
    (8, r"(sex|intercourse).{0,80}(nud|naked|breast|topless|buttock)|(nud|naked|breast|topless|buttock).{0,80}(sex|intercourse)"),
    (7, r"breast|topless|nipple"),
    (6, r"sex scene|having sex|have sex|has sex|intercourse|thrust|moan"),
    (5, r"buttock|bare (back|bottom|rear|behind)|naked|nude|nudity|skinny[- ]dip"),
    (4, r"implied sex|sex is implied|after sex|in bed together|undress|sex off[- ]?screen"),
    (3, r"innuendo|sexual (joke|reference|talk|comment|humou?r)|underwear|bikini|lingerie|cleavage|strip"),
    (2, r"make out|making out|makes out|passionate"),
    (1, r"kiss|hug|flirt|romanc"),
]
# Items that say nothing is shown or it's only implied/censored cap at Implied, wherever that's said in the item.
_NOT_SHOWN = re.compile(r"\b(implied|implies|nothing (is )?(ever )?(shown|seen|visible)|not (actually )?(shown|seen|visible)|censored|blurred|pixelated|covered)\b", re.I)
_NEGATION = re.compile(r"\b(no|not|never|without|nothing|isn't|aren't|doesn't)\b", re.I)
# Sentences that talk about something rather than show it ("a reference to...", "jokes about...") cap at Suggestive.
_TALK = re.compile(r"\b(reference|refers|joke|jokes|innuendo|says|asks|talks?|mentions?|euphemism|threatens?)\b", re.I)
# Nudity only in artwork caps at Suggestive (rubric v3).
_ARTWORK = re.compile(r"\b(drawings?|sketch(es)?|paintings?|portraits?|posters?|statues?|sculptures?)\b", re.I)
_PROLONGED = re.compile(r"prolonged|lengthy|long|extended|lingering|several (minutes|seconds)", re.I)
_OVERVIEW = re.compile(r"^(overall|in general|generally|mostly|throughout|this (film|movie|show|series))", re.I)


def keywords_one(text: str) -> dict:
    return _with_keywords(text)


def keywords(texts: List[str]) -> List[dict]:
    return [_with_keywords(t) for t in texts]


def _with_keywords(text: str) -> dict:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if not _NEGATION.search(s)]
    level = 0
    for s in sentences:
        for lvl, pattern in _KEYWORDS:
            if re.search(pattern, s, re.I):
                level = max(level, min(lvl, 3) if (_TALK.search(s) or _ARTWORK.search(s)) else lvl)
                break
    if _NOT_SHOWN.search(text):
        level = min(level, 4)
    return {"level": level, "is_scene": not _OVERVIEW.search(text.strip()), "prolonged": bool(_PROLONGED.search(text))}
