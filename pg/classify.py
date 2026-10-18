"""Assigns each IMDb Sex & Nudity item a level on the ladder.

An AI model reads the free-text items when one is configured: any OpenAI-compatible
API such as Groq's free tier (PG_AI_BASE_URL + PG_AI_API_KEY + PG_AI_MODEL), or, on a public copy, the viewer's
own key from their install link (pg/userkeys.py).
When the main key or model hits its limit, an optional backup key (PG_AI_FALLBACK_KEY) and/or backup
model (PG_AI_FALLBACK_MODEL) take over until it recovers. Otherwise, or when the AI is unavailable or over its limits, a keyword matcher
gives a rougher estimate.
"""
from __future__ import annotations

import hashlib
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
# Backups for when the main key or model hits its limit (e.g. Groq's tokens per day).
AI_FALLBACK_KEY = os.environ.get("PG_AI_FALLBACK_KEY", "")
AI_FALLBACK_MODEL = os.environ.get("PG_AI_FALLBACK_MODEL", "")


# Why the AI couldn't score, shown next to a keyword score so viewers know what happened (see PROBLEM_LABELS).
BUSY, LIMIT, KEY, MODEL, DOWN = "busy", "limit", "key", "model", "down"
PROBLEM_LABELS = {
    BUSY: "AI Busy",
    LIMIT: "AI Limit Reached",
    KEY: "AI Key Invalid",
    MODEL: "AI Model Not Found",
    DOWN: "AI Unavailable",
}
PROBLEM_TEXT = {
    BUSY: "The AI service asked Couch Check to slow down for a moment. AI scores resume within a minute or two.",
    LIMIT: "The AI key has used up its allowance for now (free plans have a daily limit). AI scores come back "
           "automatically when the limit resets, usually within a day.",
    KEY: "The AI service didn't accept the key. Check it on the configure page and reinstall.",
    MODEL: "The AI service doesn't offer that model any more. Pick another on the configure page and reinstall.",
    DOWN: "The AI service didn't answer. Couch Check tries again in a few minutes.",
}
# How long a route rests after each problem when the service doesn't say (seconds).
PROBLEM_PAUSE = {BUSY: 60, LIMIT: 900, KEY: 3600, MODEL: 3600, DOWN: 120}
_DAILY = re.compile(r"per day|daily|\bTPD\b|\bRPD\b|quota|credits|billing|insufficient", re.I)


class Route:
    """One key + model the AI can be reached through, paused on its own when it hits a limit.

    capped: the server's own keys (PG_AI_*), paced and capped by `limiter`. Viewers' own keys (pg/userkeys.py)
    aren't capped: their AI service enforces its own limits, and a pause only ever affects that one key.
    """

    def __init__(self, name: str, key: str, model: str, base_url: str = AI_BASE_URL, capped: bool = True):
        self.name, self.key, self.model, self.base_url, self.capped = name, key, model, base_url.rstrip("/"), capped
        self.paused_until = 0.0
        self.problem: Optional[str] = None

    def pause(self, seconds: float, problem: str) -> None:
        self.paused_until, self.problem = time.time() + seconds, problem

    def is_paused(self) -> bool:
        return time.time() < self.paused_until

    def reasoning_effort(self) -> str:
        # Reasoning models (e.g. gpt-oss) think before answering, and that counts against token limits;
        # light thinking is plenty for placing items on the ladder.
        return os.environ.get("PG_AI_REASONING_EFFORT") or ("low" if "gpt-oss" in self.model else "")


ROUTES = [Route("main", AI_API_KEY, AI_MODEL)]
if AI_FALLBACK_KEY:
    ROUTES.append(Route("backup key", AI_FALLBACK_KEY, AI_MODEL))
if AI_FALLBACK_MODEL:
    ROUTES.append(Route("backup model", AI_API_KEY, AI_FALLBACK_MODEL))
    if AI_FALLBACK_KEY:
        ROUTES.append(Route("backup key and model", AI_FALLBACK_KEY, AI_FALLBACK_MODEL))

_viewer_routes: dict = {}
_viewer_lock = threading.Lock()


def viewer_routes(base_url: str, key: str, models: List[str]) -> List[Route]:
    """The routes for a viewer's own key: one per model, tried in order (on Groq each model has its own free
    allowance). The same key always gets the same Routes, so their pauses carry over between requests without
    ever touching anyone else's."""
    ident = hashlib.sha256("\n".join([base_url, key] + models).encode()).hexdigest()
    with _viewer_lock:
        if ident not in _viewer_routes:
            if len(_viewer_routes) > 5000:  # forget old keys rather than grow forever
                _viewer_routes.clear()
            _viewer_routes[ident] = [Route("viewer key" + (f" ({m})" if i else ""), key, m, base_url, capped=False)
                                     for i, m in enumerate(models)]
        return _viewer_routes[ident]


def server_routes() -> List[Route]:
    """The server's own keys, or none when this copy has no AI key (or AI is switched off)."""
    return ROUTES if provider() else []


def problem(routes: List[Route]) -> Optional[str]:
    """Why these routes can't score right now (BUSY, LIMIT, …), or None when one of them can."""
    if not routes:
        return None
    if any(not r.is_paused() for r in routes):
        return LIMIT if routes[0].capped and limiter.over_cap() else None
    return routes[0].problem or LIMIT


def provider() -> Optional[str]:
    if os.environ.get("PG_CLASSIFIER") == "keywords":
        return None
    if AI_BASE_URL and AI_API_KEY and AI_MODEL:
        return "openai"
    return None


def ai_enabled() -> bool:
    return provider() is not None


def ai_label() -> str:
    if not provider():
        return ""
    backups = [r.name for r in ROUTES[1:]]
    return f"{AI_MODEL} via {AI_BASE_URL}" + (f" (backups: {', '.join(backups)})" if backups else "")


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

    def over_cap(self) -> bool:
        with self._lock:
            return date.today() == self._day and self._count >= self.daily_limit


def _acquire(routes: List[Route]) -> bool:
    """A viewer's own key goes straight through; the server's keys wait their turn under `limiter`."""
    return bool(routes) and (not routes[0].capped or limiter.acquire())


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


def classify(texts: List[str], about: str = "", routes: Optional[List[Route]] = None
             ) -> Tuple[List[dict], str, Optional[str]]:
    """Returns ([{level, is_scene, prolonged}, ...] aligned with texts, classifier name, AI summary or None).

    routes: a viewer's own key (viewer_routes); the server's keys when None.
    """
    if not texts:
        return [], "none", None
    routes = server_routes() if routes is None else routes
    if _acquire(routes):
        result = _with_openai(texts, about, routes)
        if result is not None:
            classified, summary, model = result
            return _recheck_top(texts, classified, about, routes), f"ai:{model}@{rubric.VERSION}", summary
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
        out.append(_warning_guard(t, _genre_nonsexual(t, _vague_cap(t, _clean(e)), about)))
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


_DISCUSSED = re.compile(r"\b(discuss\w*|discussion|talk\w*|mention\w*|references?|dialogue|story about)\b", re.I)
_DEPICTED = re.compile(r"\b(shown|depicted|seen|on screen|scene)\b", re.I)
_SURE_WARNINGS = {"assault", "incest"}  # keyword matches precise enough to add when the AI misses them


def _warning_guard(text: str, c: dict) -> dict:
    """Warnings the AI can get wrong: discussion is "discussed", not assault; plain mentions of assault or incest
    get their warning even if the AI left it out."""
    if c.get("warning") == "assault" and _DISCUSSED.search(text) and not _DEPICTED.search(text):
        return {**c, "warning": "violence_talk"}
    if not c.get("warning"):
        found = next((w for w, pattern in _WARNINGS if w in _SURE_WARNINGS and pattern.search(text)), None)
        if found and rubric.allowed_warning(found, c["level"]):
            return {**c, "warning": found}
    return c


def _genre_nonsexual(text: str, c: dict, about: str) -> dict:
    """In a Sci-Fi or Horror title, nudity with no sexual or romantic framing counts as non-sexual (rubric v5.2)."""
    if (_SCIFI_HORROR.search(about) and not c.get("nonsexual") and NAKED_CAP <= c["level"] <= 7
            and not _SEXUAL_SCENE.search(text) and c.get("warning") != "assault" and not _ASSAULT.search(text)):
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
    warning = rubric.allowed_warning(e.get("warning"), level)
    shown = " ".join(str(e.get("shown") or "").split())[:120]
    return {"level": level, "is_scene": bool(e.get("is_scene", True)), "prolonged": bool(e.get("prolonged")),
            "warning": warning, "nonsexual": nonsexual, "shown": shown}


def _recheck_top(texts: List[str], classified: List[dict], about: str = "",
                 routes: Optional[List[Route]] = None) -> List[dict]:
    """Asks again about the item that sets the title's level (if it's 4 or above) and uses that answer."""
    if not classified:
        return classified
    routes = server_routes() if routes is None else routes
    top_level = max(c["level"] for c in classified)
    if top_level < rubric.SCENE_THRESHOLD or not _acquire(routes):
        return classified
    i = sorted((i for i, c in enumerate(classified) if c["level"] == top_level),
               key=lambda i: not classified[i]["is_scene"])[0]
    answer = (_chat(RECHECK, f"{about}\nItem: {json.dumps(texts[i], ensure_ascii=False)}\nGiven level: {top_level}".strip(),
                    routes) or (None,))[0]
    if not isinstance(answer, dict) or not isinstance(answer.get("level"), int) or not 0 <= answer["level"] <= 10:
        return classified
    fixed = _genre_nonsexual(texts[i], _vague_cap(texts[i], _clean({
        **classified[i], "level": answer["level"], "nonsexual": answer.get("nonsexual"),
        "shown": answer.get("shown") or classified[i].get("shown")})), about)
    if fixed["level"] != top_level:
        log.info("Re-check moved an item from %d to %d", top_level, fixed["level"])
    fixed["warning"] = rubric.allowed_warning(classified[i].get("warning"), fixed["level"])
    return classified[:i] + [fixed] + classified[i + 1:]


def _with_openai(texts: List[str], about: str = "", routes: Optional[List[Route]] = None
                 ) -> Optional[Tuple[List[dict], Optional[str], str]]:
    """(items, summary, model that answered), or None."""
    answer = _chat(SYSTEM + JSON_INSTRUCTION, f"{about}\nItems:\n{_numbered(texts)}".strip(), routes)
    if answer is None or not isinstance(answer[0], dict):
        return None
    parsed, model = answer
    return _align(texts, parsed.get("items"), about), _summary(parsed.get("summary")), model


def _chat(system: str, user: str, routes: Optional[List[Route]] = None) -> Optional[Tuple[dict, str]]:
    """One JSON chat call, through the first route that isn't paused: (answer, model) or None (the caller falls back).

    A route that hits a limit is paused on its own and the next one is tried; only when every one of the server's
    routes is paused does the server's AI scoring pause as a whole.
    """
    routes = server_routes() if routes is None else routes
    for route in routes:
        if route.is_paused():
            continue
        outcome = _call(route, system, user)
        if outcome == "next":
            continue
        if outcome is None:
            return None
        return outcome, route.model
    waits = [r.paused_until - time.time() for r in routes]
    if waits and min(waits) > 0 and routes[0].capped:
        limiter.pause(min(waits))
    return None


def check(base_url: str, key: str, model: str) -> Optional[str]:
    """One tiny call to see whether a key and model work: None if they do, otherwise the problem (KEY, MODEL, …).
    Used by the configure page's Test Key button; it doesn't pause anything."""
    route = Route("key check", key, model, base_url, capped=False)
    body = {"model": model, "messages": [{"role": "user", "content": 'Reply with {"ok": true}'}], "max_tokens": 200}
    try:
        _post(route, body, timeout=30)
        return None
    except _Problem as p:
        return p.problem


class _Problem(Exception):
    def __init__(self, problem: str, seconds: float):
        super().__init__(problem)
        self.problem, self.seconds = problem, seconds


def _post(route: Route, body: dict, timeout: float = 90) -> dict:
    """POSTs a chat request through the route. Raises _Problem saying what went wrong."""
    req = urllib.request.Request(
        f"{route.base_url}/chat/completions",
        data=json.dumps(body).encode(),
        # Some providers (Groq, behind Cloudflare) reject Python's default User-Agent with a 403.
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {route.key}",
                 "User-Agent": "couch-check/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(2000).decode("utf-8", "replace")
        except OSError:
            detail = ""
        retry = e.headers.get("Retry-After") if e.headers else None
        wait = float(retry) if retry and retry.replace(".", "", 1).isdigit() else None
        if e.code == 429:
            # Per-minute limits clear on their own; per-day limits and empty credit mean "out for now".
            daily = (wait or 0) > 120 or bool(_DAILY.search(detail))
            problem = LIMIT if daily else BUSY
        elif e.code == 402:
            problem = LIMIT
        elif e.code in (401, 403):
            problem = KEY
        elif e.code == 404 or (e.code == 400 and re.search(r"model", detail, re.I)):
            problem = MODEL
        elif e.code >= 500:
            problem = DOWN
        else:
            log.warning("AI provider error HTTP %d for the %s: %s", e.code, route.name, detail[:200])
            raise _Problem(DOWN, 0)
        raise _Problem(problem, wait if wait is not None else PROBLEM_PAUSE[problem])
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("Could not reach the AI provider for the %s (%s)", route.name, e)
        raise _Problem(DOWN, PROBLEM_PAUSE[DOWN])


def _call(route: Route, system: str, user: str):
    """The parsed answer, None on a failure, or "next" when this route hit a limit (try the next one)."""
    body = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    if route.reasoning_effort():
        body["reasoning_effort"] = route.reasoning_effort()
    try:
        data = _post(route, body)
    except _Problem as p:
        if not p.seconds:
            return None  # an odd one-off error: use keywords this time, try again next time
        route.pause(p.seconds, p.problem)
        log.warning("AI %s (%s): %s; pausing it for %ds", route.name, route.model, PROBLEM_LABELS[p.problem], p.seconds)
        return None if p.problem == DOWN else "next"

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
    ("assault", re.compile(r"\b(rape[sd]?|raping|sexual(ly)? assault\w*|molest\w*|sexually abus\w*)\b", re.I)),
    ("minor", re.compile(r"(under-?age|minor|\b1[0-7]-year-old|\bteen(age|ager)?s?\b|child|student).{0,80}"
                         r"(sexual|relationship|affair|romance|romantic|seduc|groom)|(sexual|relationship|affair|romance|"
                         r"romantic|seduc|groom).{0,80}(under-?age|minor|\b1[0-7]-year-old|\bteen(age|ager)?s?\b|child|student)", re.I)),
    ("incest", re.compile(r"\bincest", re.I)),
    ("exploitation", re.compile(r"traffick|forced (into )?(prostitution|sex work)|sex slave|coerced into sex", re.I)),
    ("violence_talk", re.compile(r"(discuss\w*|talk\w*|mention\w*|references?).{0,40}(sex(ual)? (abuse|violence))|"
                                 r"(sex(ual)? (abuse|violence)).{0,40}(discuss\w*|talk\w*|mention\w*)", re.I)),
]
_ASSAULT = dict(_WARNINGS)["assault"]  # assault is never non-sexual, whatever the genre
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
    warning = next((w for w, pattern in _WARNINGS if pattern.search(text) and rubric.allowed_warning(w, level)), None)
    return {"level": level, "is_scene": not _OVERVIEW.search(text.strip()), "prolonged": bool(_PROLONGED.search(text)),
            "warning": warning, "nonsexual": nonsexual, "shown": ""}
