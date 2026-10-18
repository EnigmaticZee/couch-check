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
NONSEXUAL_TEXT = ", ".join(f'"{k}" ({v.lower()})' for k, v in rubric.NONSEXUAL.items())

SYSTEM = f"""You score movie and TV content for sexual content and nudity only, so a viewer can decide whether to watch something with their spouse or family.

You will receive numbered user-written items from a title's IMDb parents guide "Sex & Nudity" section. Score every item on this ladder:

{LADDER_TEXT}

Rules:
{RULES_TEXT}

For each item, first write shown: a few plain words on what is actually visible or heard on screen (e.g. "only talk", "woman in a bra", "bare breasts, brief", "nothing, cut away"). Then give the level from what's in shown, not from the words the item happens to use.
- nonsexual: for nudity in a plainly non-sexual setting, one of {NONSEXUAL_TEXT}; otherwise null.
- is_scene: true if the item describes a specific scene or moment; false if it is a general overview or opinion ("overall mild", "lots of sexual content throughout", "not as bad as other shows").
- prolonged: true if the item says the content is long, lingering, extended, repeated, throughout, or lasts more than a few seconds.
- {rubric.WARNING_RULE}

Return one entry per item, using the item's number as index.

Also write a summary: 2 or 3 short, plain sentences for someone deciding whether to watch this with family. Say what the most explicit content is and how often it happens, then what most of the rest is like. Be factual and specific (e.g. "brief breast nudity in the first episode"): describe only what the items say is visible, never assume genitals from "naked" or "nude", treat talk as talk, and call non-sexual nudity non-sexual. Use no character names, give away no plot, don't quote the items, don't mention level numbers, call clothing clothing (underwear is not nudity), and don't judge who it is suitable for (no age or audience advice): describe, and let the reader decide."""

# APIs vary in structured-output support, so ask for the shape in words and validate it.
JSON_INSTRUCTION = """

Respond with JSON only, in exactly this shape:
{"summary": "...", "items": [{"index": 0, "shown": "...", "level": 0, "nonsexual": null, "is_scene": true, "prolonged": false, "warning": null}, ...]}"""

# The item that sets a title's level gets a second, focused look (rubric v5.2): one extra call per title.
RECHECK = f"""You check one score from a sexual content and nudity rating. The ladder:

{LADDER_TEXT}

Rules:
{RULES_TEXT}

You get one IMDb parents guide item and the level it was given. Decide the right level from what is actually shown or heard. First write shown: a few plain words on what is visible or heard. Respond with JSON only: {{"shown": "...", "level": 0, "nonsexual": null}} where nonsexual is one of {NONSEXUAL_TEXT} for nudity in a plainly non-sexual setting, otherwise null."""



def context(guide: dict) -> str:
    """A line about the title for the AI, so it can judge settings (e.g. that a series is sci-fi)."""
    parts = [guide.get("title") or ""]
    if guide.get("year"):
        parts.append(f"({guide['year']})")
    kind = {"tvSeries": "TV series", "tvMiniSeries": "TV mini-series", "movie": "movie"}.get(guide.get("type") or "")
    details = ", ".join(x for x in [kind] + list(guide.get("genres") or []) if x)
    return f"Title: {' '.join(p for p in parts if p)}" + (f" · {details}" if details else "")


def classify(texts: List[str], about: str = "") -> Tuple[List[dict], str, Optional[str]]:
    """Returns ([{level, is_scene, prolonged}, ...] aligned with texts, classifier name, AI summary or None)."""
    if not texts:
        return [], "none", None
    if provider() and limiter.acquire():
        result = _with_openai(texts, about)
        if result is not None:
            return _recheck_top(texts, result[0], about), f"ai:{AI_MODEL}@{rubric.VERSION}", result[1]
    return keywords(texts), "keywords", None


def _summary(value) -> Optional[str]:
    """The model's summary, trimmed to something sensible for a page, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())[:600]


def _numbered(texts: List[str]) -> str:
    return "\n".join(f"{i}. {json.dumps(t, ensure_ascii=False)}" for i, t in enumerate(texts))


def _align(texts: List[str], entries: list, about: str = "") -> List[dict]:
    """Matches model output to items by index; anything missing or malformed falls back to keywords."""
    by_index = {}
    for e in entries if isinstance(entries, list) else []:
        if (isinstance(e, dict) and isinstance(e.get("index"), int) and isinstance(e.get("level"), int)
                and 0 <= e["level"] <= 10):
            by_index[e["index"]] = e
    out = []
    for i, t in enumerate(texts):
        e = by_index.get(i)
        if not e:
            out.append(_with_keywords(t))
            continue
        out.append(_genre_nonsexual(t, _vague_cap(t, _clean(e)), about))
    return out


# Words that call something explicit without saying what is shown, and words that do say (rubric v5.2).
_VAGUE = re.compile(r"\b(graphic|explicit|steamy|raunchy|racy|intense)\b", re.I)
_SPECIFIC = re.compile(r"breast|topless|nipple|genital|penis|vagina|pubic|frontal|buttock|naked|nud|oral|thrust|penetrat", re.I)
VAGUE_CAP = 6


_NAKED = re.compile(r"\b(naked|nude|nudity|undressed)\b", re.I)
_NAMED = re.compile(r"breast|topless|nipple|genital|penis|vagina|pubic|frontal|private parts|\bsex|intercourse", re.I)
NAKED_CAP = 5
_GENITAL = re.compile(r"genital|penis|vagina|pubic|frontal|private parts|crotch|erect|oral sex|penetrat|"
                      r"unsimulated|pornograph|orgy|orgies|graphic sex|explicit sex", re.I)
_SEX = re.compile(r"\bsex\b|sex scene|having sex|intercourse", re.I)


_SCIFI_HORROR = re.compile(r"\b(Sci-Fi|Horror)\b")
_SEXUAL_SCENE = re.compile(r"\bsex|intercourse|love scene|making love|make love|seduc|strip|lap dance|kiss|"
                           r"in bed|aroused|erotic|romantic|lover", re.I)


def _genre_nonsexual(text: str, c: dict, about: str) -> dict:
    """In a Sci-Fi or Horror title, nudity with no sexual or romantic framing counts as non-sexual (rubric v5.2)."""
    if (_SCIFI_HORROR.search(about) and not c.get("nonsexual") and NAKED_CAP <= c["level"] <= 7
            and not _SEXUAL_SCENE.search(text)):
        return {**c, "level": rubric.NONSEXUAL_CAP, "nonsexual": "scifi_horror",
                "shown": "nudity with no sexual framing, in a sci-fi or horror title"}
    return c


def _vague_cap(text: str, c: dict) -> dict:
    """Caps the AI can overshoot: general comments that call a title graphic without saying what's shown (VAGUE_CAP),
    and "naked" without saying what's visible or that it's sexual (NAKED_CAP)."""
    # When a cap applies, the AI's note overshot too, so it's replaced with what the item actually supports.
    if c["level"] > VAGUE_CAP and _VAGUE.search(text) and not _SPECIFIC.search(text):
        c = {**c, "level": VAGUE_CAP, "shown": "a general comment calling it graphic, nothing specific described"}
    if c["level"] > NAKED_CAP and _NAKED.search(text) and not _NAMED.search(text):
        c = {**c, "level": NAKED_CAP, "shown": "nudity, without saying what is visible"}
    # 9 (full frontal) and 10 (graphic) need the item to say genitals or graphic sex are shown.
    if c["level"] >= 9 and not _GENITAL.search(text):
        sex = bool(_SEX.search(text))
        c = {**c, "level": 8 if sex else 7,
             "shown": "sex with nudity, no genitals described" if sex else "breasts or buttocks, no genitals described"}
    return c


def _clean(e: dict) -> dict:
    """One model entry as {level, is_scene, prolonged, warning, nonsexual, shown}, with v5.2's caps applied."""
    level = e["level"]
    nonsexual = e.get("nonsexual") if e.get("nonsexual") in rubric.NONSEXUAL and level > 0 else None
    if nonsexual:
        level = min(level, rubric.NONSEXUAL_CAP)
    # Warnings are only for unscorable (level-0) items; anything shown is scored instead.
    warning = e.get("warning") if level == 0 and e.get("warning") in rubric.WARNINGS else None
    shown = " ".join(str(e.get("shown") or "").split())[:120]
    return {"level": level, "is_scene": bool(e.get("is_scene", True)), "prolonged": bool(e.get("prolonged")),
            "warning": warning, "nonsexual": nonsexual, "shown": shown}


def _recheck_top(texts: List[str], classified: List[dict], about: str = "") -> List[dict]:
    """Asks again about the item that sets the title's level (if it's 4 or above) and uses that answer."""
    if not classified:
        return classified
    top_level = max(c["level"] for c in classified)
    if top_level < rubric.SCENE_THRESHOLD or not limiter.acquire():
        return classified
    i = sorted((i for i, c in enumerate(classified) if c["level"] == top_level),
               key=lambda i: not classified[i]["is_scene"])[0]
    answer = _chat(RECHECK, f"{about}\nItem: {json.dumps(texts[i], ensure_ascii=False)}\nGiven level: {top_level}".strip())
    if not isinstance(answer, dict) or not isinstance(answer.get("level"), int) or not 0 <= answer["level"] <= 10:
        return classified
    fixed = _genre_nonsexual(texts[i], _vague_cap(texts[i], _clean({
        **classified[i], "level": answer["level"], "nonsexual": answer.get("nonsexual"),
        "shown": answer.get("shown") or classified[i].get("shown")})), about)
    if fixed["level"] != top_level:
        log.info("Re-check moved an item from %d to %d", top_level, fixed["level"])
    return classified[:i] + [{**fixed, "warning": classified[i].get("warning") if fixed["level"] == 0 else None}] + classified[i + 1:]


def _with_openai(texts: List[str], about: str = "") -> Optional[Tuple[List[dict], Optional[str]]]:
    parsed = _chat(SYSTEM + JSON_INSTRUCTION, f"{about}\nItems:\n{_numbered(texts)}".strip())
    if not isinstance(parsed, dict):
        return None
    return _align(texts, parsed.get("items"), about), _summary(parsed.get("summary"))


def _chat(system: str, user: str) -> Optional[dict]:
    """One JSON chat call to the AI provider; None if it fails (the caller falls back)."""
    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
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
        return json.loads(text)
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
    (1, r"kiss|hug|flirt|romanc|sexy"),
]
# Bare male chests are level 0 outside sexual scenes (rubric v5.1), so they're removed before matching.
_MALE_CHEST = re.compile(r"\b(shirtless|bare[- ]chested|topless (men|man|guys?|boys?|males?)|(men|man|guys?|boys?|males?) (are |is )?topless)\b", re.I)
# Items that say nothing is shown or it's only implied/censored cap at Implied, wherever that's said in the item.
_NOT_SHOWN = re.compile(r"\b(implied|implies|nothing (is )?(ever )?(shown|seen|visible)|not (actually )?(shown|seen|visible)|censored|blurred|pixelated|covered)\b", re.I)
_NEGATION = re.compile(r"\b(no|not|never|without|nothing|isn't|aren't|doesn't)\b", re.I)
# Sentences that talk about something rather than show it ("a reference to...", "jokes about...") cap at Suggestive.
_TALK = re.compile(r"\b(references?|refers|joke|jokes|innuendo|says|said|asks|talks?|talking|mentions?|mentioned|euphemism|"
                   r"threatens?|dialogue|lines|discuss(es|ed|ion)?|conversations?|topic|comments?|revolves? around|implants?)\b", re.I)
# Sex as a general topic, with no detail, is level 1 (rubric v5.2).
_TOPIC = re.compile(r"\b(sex|sexual)\w*\b.{0,60}\b(references?|topic|discuss\w*|mentioned|talked about|said)\b|"
                    r"\b(references?|topic|discuss\w*|mentions?)\b.{0,40}\b(sex|sexual)", re.I)
# Nudity in plainly non-sexual settings is capped and marked * (rubric v5.2).
_NONSEXUAL = [
    ("breastfeeding", r"breast[- ]?feed|nursing (a|her|the) (baby|infant)"),
    ("childbirth", r"giv(es|ing) birth|childbirth|labou?r and delivery"),
    ("medical", r"\b(medical|hospital|doctor|surgery|examination)\b"),
    ("corpse", r"\b(corpse|dead body|autopsy|morgue)\b"),
    ("strip_search", r"strip[- ]search|prison|inmates?"),
    ("war_history", r"concentration camp|holocaust"),
    ("art_model", r"life drawing|art class|nude model"),
    ("changing_room", r"locker room|changing room"),
    ("naturist", r"nudist|naturist"),
    ("scifi_horror", r"\b(alien|creature|experiment|monster|zombie|mutant)\b"),
]
# Nudity only in artwork caps at Suggestive (rubric v3).
_ARTWORK = re.compile(r"\b(drawings?|sketch(es)?|paintings?|portraits?|posters?|statues?|sculptures?)\b", re.I)
_PROLONGED = re.compile(r"prolonged|lengthy|long|extended|lingering|several (minutes|seconds)", re.I)
# Rough keyword checks for the warning themes (rubric.WARNINGS), used for estimates only.
_WARNINGS = [
    ("minor", re.compile(r"(under-?age|minor|\b1[0-7]-year-old|\bteen(age|ager)?s?\b|child|student).{0,80}"
                         r"(sexual|relationship|affair|romance|romantic|seduc|groom)|(sexual|relationship|affair|romance|"
                         r"romantic|seduc|groom).{0,80}(under-?age|minor|\b1[0-7]-year-old|\bteen(age|ager)?s?\b|child|student)", re.I)),
    ("incest", re.compile(r"\bincest", re.I)),
    ("exploitation", re.compile(r"traffick|forced (into )?(prostitution|sex work)|sex slave|coerced into sex", re.I)),
]
_OVERVIEW = re.compile(r"^(overall|in general|generally|mostly|throughout|this (film|movie|show|series))", re.I)


def keywords_one(text: str) -> dict:
    return _with_keywords(text)


def keywords(texts: List[str]) -> List[dict]:
    return [_with_keywords(t) for t in texts]


def _with_keywords(text: str) -> dict:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if not _NEGATION.search(s)]
    level = 0
    for s in sentences:
        s = _MALE_CHEST.sub("", s)
        for lvl, pattern in _KEYWORDS:
            if re.search(pattern, s, re.I):
                level = max(level, min(lvl, 3) if (_TALK.search(s) or _ARTWORK.search(s)) else lvl)
                break
    if _NOT_SHOWN.search(text):
        level = min(level, 4)
    if level == 0 and _TOPIC.search(text):
        level = 1
    nonsexual = None
    if level > rubric.NONSEXUAL_CAP:
        nonsexual = next((k for k, pattern in _NONSEXUAL if re.search(pattern, text, re.I)), None)
        if nonsexual:
            level = rubric.NONSEXUAL_CAP
    warning = next((w for w, pattern in _WARNINGS if pattern.search(text)), None) if level == 0 else None
    return {"level": level, "is_scene": not _OVERVIEW.search(text.strip()), "prolonged": bool(_PROLONGED.search(text)),
            "warning": warning, "nonsexual": nonsexual, "shown": ""}
