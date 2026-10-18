"""Scores IMDb items on the ISCCI ladder: with the AI when it's available, otherwise a keyword estimate.

Uses production's AI settings, pacing and daily limit (pg.classify), so ISCCI and v5.1 share one quota.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from pg import classify as base
from pg import rubric as base_rubric

from . import rubric


LADDER_TEXT = "\n".join(f"{lvl} — {name}: {desc}" for lvl, name, desc in rubric.LADDER)
RULES_TEXT = "\n".join(f"- {r}" for r in rubric.RULES)
NONSEXUAL_TEXT = ", ".join(f'"{k}" ({v.lower()})' for k, v in rubric.NONSEXUAL.items())

SYSTEM = f"""You score movie and TV content for sexual content and nudity only, so a viewer can decide whether to watch something with their spouse or family.

You will receive numbered user-written items from a title's IMDb parents guide "Sex & Nudity" section. Score every item on this ladder by the most explicit thing actually shown or heard:

{LADDER_TEXT}

Rules:
{RULES_TEXT}

For each item also give:
- tag: what the item's most explicit content is, exactly one of: {", ".join(rubric.TAG_NAMES)}.
- nonsexual: for nudity in a plainly non-sexual context, one of {NONSEXUAL_TEXT}; otherwise null. Still give its full floor level.
- is_scene: true for a specific scene or moment; false for a general overview or opinion ("lots of sexual content throughout").
- prolonged: true if the content is described as long, lingering, extended or lasting more than a few seconds.
- momentary: true if it's only a moment, easy to skip.
- amount: "throughout" if the item says it happens throughout, in most episodes or is central to the story; "several" if it says several, multiple, numerous, often or repeatedly; otherwise "once".
- {base_rubric.WARNING_RULE}

Return one entry per item, using the item's number as index.

Also write a summary: 2 or 3 short, plain sentences for someone deciding whether to watch this with family. Say what the most explicit sexual content is and how often it happens, then what most of the rest is like; mention non-sexual nudity separately as non-sexual. Be factual and specific, use no character names, give away no plot, don't quote the items, don't mention level numbers, call clothing clothing (underwear is not nudity), and don't judge who it is suitable for: describe, and let the reader decide."""

JSON_INSTRUCTION = """

Respond with JSON only, in exactly this shape:
{"summary": "...", "items": [{"index": 0, "level": 0, "tag": "None", "nonsexual": null, "is_scene": true, "prolonged": false, "momentary": false, "amount": "once", "warning": null}, ...]}"""


def classifier_name(model: str = "") -> str:
    return f"ai:{model or base.AI_MODEL}@{rubric.TAG}"


def classify(texts: List[str]) -> Tuple[List[dict], str, Optional[str]]:
    """Returns (items aligned with texts, classifier name, AI summary or None)."""
    if not texts:
        return [], "none", None
    if base.provider() and base.limiter.acquire():
        result = _with_ai(texts)
        if result is not None:
            return result[0], classifier_name(result[2]), result[1]
    return keywords(texts), "keywords", None


def _clean(e: dict, text: str) -> dict:
    level = e["level"]
    tag = e.get("tag") if e.get("tag") in rubric.TAG_NAMES else rubric.default_tag(level)
    nonsexual = e.get("nonsexual") if e.get("nonsexual") in rubric.NONSEXUAL and level > 0 else None
    warning = e.get("warning") if level == 0 and e.get("warning") in base_rubric.WARNINGS else None
    amount = e.get("amount") if e.get("amount") in ("once", "several", "throughout") else "once"
    return {"level": level, "tag": tag, "nonsexual": nonsexual, "is_scene": bool(e.get("is_scene", True)),
            "prolonged": bool(e.get("prolonged")), "momentary": bool(e.get("momentary")), "amount": amount,
            "warning": warning}


def _align(texts: List[str], entries) -> List[dict]:
    by_index = {}
    for e in entries if isinstance(entries, list) else []:
        if (isinstance(e, dict) and isinstance(e.get("index"), int) and isinstance(e.get("level"), int)
                and 0 <= e["level"] <= 10):
            by_index[e["index"]] = e
    return [_clean(by_index[i], t) if i in by_index else keyword_one(t) for i, t in enumerate(texts)]


def _with_ai(texts: List[str]) -> Optional[Tuple[List[dict], Optional[str], str]]:
    """(items, summary, model), through production's AI routes (main, then any backups)."""
    numbered = "\n".join(f"{i}. {json.dumps(t, ensure_ascii=False)}" for i, t in enumerate(texts))
    answer = base._chat(SYSTEM + JSON_INSTRUCTION, f"Items:\n{numbered}")
    if answer is None or not isinstance(answer[0], dict):
        return None
    parsed, model = answer
    return _align(texts, parsed.get("items")), base._summary(parsed.get("summary")), model


# The keyword estimate reuses production's matcher and moves its levels onto the ISCCI ladder.
V51_TO_ISCCI = {0: 0, 1: 1, 2: 2, 3: 2, 4: 4, 5: 5, 6: 5, 7: 6, 8: 7, 9: 7, 10: 8}
_TAG_PATTERNS = [
    ("Graphic sex", r"oral sex|graphic sex|explicit sex|pornograph|unsimulated|penetrat"),
    ("Full frontal", r"full[- ]frontal|frontal nudity|genital|penis|vagina|pubic"),
    ("Topless", r"breast|topless|nipple"),
    ("Sex scene", r"sex scene|having sex|have sex|has sex|intercourse|thrust"),
    ("Partial nudity", r"buttock|bare (back|bottom|rear|behind)|naked|nude|nudity|skinny[- ]dip"),
    ("Implied sex", r"implied|after sex|in bed together|undress|off[- ]?screen"),
    ("Sexual talk", r"sexual (talk|comment|reference)|talks? about sex"),
    ("Innuendo", r"innuendo|sexual (joke|humou?r)"),
    ("Revealing clothes", r"underwear|bikini|lingerie|cleavage|swimsuit|bra\b|towel"),
    ("Making out", r"make out|making out|makes out"),
    ("Kissing", r"kiss"),
    ("Flirting", r"flirt|sexy|romanc|dating"),
]
_NONSEXUAL_PATTERNS = [
    ("breastfeeding", r"breast[- ]?feed|nursing (a|her|the) (baby|infant)"),
    ("childbirth", r"giv(es|ing) birth|childbirth|labou?r and delivery"),
    ("medical", r"\b(medical|hospital|doctor|surgery|exam(ination)?)\b"),
    ("corpse", r"\b(corpse|dead body|autopsy|morgue)\b"),
    ("strip_search", r"strip[- ]search|prison|inmates?"),
    ("war_history", r"concentration camp|holocaust|\bwar\b"),
    ("art_model", r"life drawing|art class|nude model"),
    ("changing_room", r"locker room|changing room"),
    ("naturist", r"nudist|naturist"),
]
_MOMENTARY = re.compile(r"\b(brief(ly)?|quick(ly)?|moment(ary|arily)?|split second)\b", re.I)
_SEVERAL = re.compile(r"\b(several|multiple|numerous|many|often|repeated(ly)?|frequent(ly)?)\b", re.I)
_THROUGHOUT = re.compile(r"\b(throughout|constant(ly)?|every episode|most episodes)\b", re.I)


def keyword_one(text: str) -> dict:
    v51 = base.keywords_one(text)
    level = V51_TO_ISCCI[v51["level"]]
    tag = next((t for t, p in _TAG_PATTERNS if re.search(p, text, re.I)), rubric.default_tag(level))
    if level == 0:
        tag = "None"
    nonsexual = None
    if level >= 4:
        nonsexual = next((k for k, p in _NONSEXUAL_PATTERNS if re.search(p, text, re.I)), None)
    amount = "throughout" if _THROUGHOUT.search(text) else "several" if _SEVERAL.search(text) else "once"
    return {"level": level, "tag": tag, "nonsexual": nonsexual, "is_scene": v51["is_scene"],
            "prolonged": v51["prolonged"], "momentary": bool(_MOMENTARY.search(text)), "amount": amount,
            "warning": v51.get("warning")}


def keywords(texts: List[str]) -> List[dict]:
    return [keyword_one(t) for t in texts]
