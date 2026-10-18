"""The ISCCI rubric: each scene's peak level, plus extra points for how much there is.

A title's score is its worst sexual scene (the peak, 0–10) plus up to +2 extra points for how often or how long it
happens. Non-sexual nudity (medical, breastfeeding…) is scored at its body-part floor and marked with *, but never
earns extra points. The draft this follows is "ISCCI rubric draft.html" in the owner's Project Context folder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Bump when the meaning changes, so ISCCI scores are redone. Stored as "iscci<VERSION>" in classifier names.
VERSION = 1
TAG = f"iscci{VERSION}"

# (level, level name, what counts)
LADDER = [
    (0, "None", "No sexual content. Affection between family or friends, hugs. Non-sexual shirtless men or boys. Crude non-sexual words for body parts (insults, toilet humour). Nudity that's said to be absent."),
    (1, "Minimal", "Brief romantic kiss. Flirting and compliments (\"sexy\", \"hot\", \"gorgeous\"). Dating, a romantic relationship. Mild suggestive dialogue with no sexual detail. Making out for just a moment, easy to skip."),
    (2, "Very mild", "Passionate or long kissing. Making out, including heavy making out (a scene, not just a moment). Revealing clothing (cleavage, swimwear, underwear, lingerie, towels). Sexual jokes and innuendo. Suggestive dancing. Camera lingering on clothed bodies. Nudity only in stylised artwork."),
    (3, "Mild", "Sexual touching over clothing. Clothed couple kissing in bed. A clear lead-up that stops (pulled into the bedroom, door closes). Frank sexual talk that describes acts. Realistic artwork nudity that's lingered on."),
    (4, "Moderate", "Implied sex: fade to black, in bed under sheets afterwards, sex heard off-screen. Undressing that cuts away. Stripping to underwear in a sexual moment. Censored, blurred or strategically covered nudity."),
    (5, "Noticeable", "Brief partial nudity: bare buttocks (anyone), bare back, side views with no nipple. Sex under sheets or clothed, with movement, brief or limited. Skinny dipping seen from a distance."),
    (6, "Strong", "Bare female breasts, briefly. Simulated sex with movement and partial nudity, not graphic. Striptease with partial nudity."),
    (7, "Very strong", "Genitals visible briefly, any setting (full frontal). Simulated sex with breasts or buttocks visible through the scene."),
    (8, "Severe", "Graphic simulated intercourse (positions, nudity throughout). Genitals in a sexual context. Oral sex clearly simulated on screen."),
    (9, "Extreme", "Prolonged graphic sexual activity with explicit nudity. Near-pornographic scenes."),
    (10, "Pornographic", "Unsimulated sex, erect genitals, penetration shown, or content made mainly to arouse."),
]

# (lowest level, highest level, band name, Stremio emoji, page colour)
BANDS = [
    (0, 1, "Clear", "🟢", "green"),
    (2, 3, "Mild", "🟡", "yellow"),
    (4, 5, "Moderate", "🟠", "orange"),
    (6, 7, "Strong", "🔴", "red"),
    (8, 10, "Severe", "🟣", "purple"),
]

# Content tags: what the worst scene actually is. The chip shows these. (tag, the level it usually sits at)
TAGS = [
    ("None", 0), ("Flirting", 1), ("Kissing", 1), ("Making out", 2), ("Revealing clothes", 2), ("Innuendo", 2),
    ("Sexual touching", 3), ("Sexual talk", 3), ("Implied sex", 4), ("Covered nudity", 4), ("Partial nudity", 5),
    ("Covered sex", 5), ("Topless", 6), ("Sex scene", 6), ("Full frontal", 7), ("Nude sex scene", 7),
    ("Graphic sex", 8), ("Pornographic", 10),
]
TAG_NAMES = [t for t, _ in TAGS]

# Non-sexual contexts: scored at the body-part floor, marked *, never earn extra points.
NONSEXUAL = {
    "medical": "Medical",
    "childbirth": "Childbirth",
    "breastfeeding": "Breastfeeding",
    "bathing_child": "Bathing a child",
    "changing_room": "Changing room",
    "naturist": "Naturist",
    "corpse": "Corpse or autopsy",
    "art_model": "Art-class model",
    "strip_search": "Strip search or prison",
    "war_history": "War or historical",
    "cultural": "Cultural documentary",
}

FLOORS = "Body-part floors: censored or blurred anything is at least 4, buttocks at least 5, female breasts at least 6, genitals at least 7."

RULES = [
    "Score what is actually shown or heard on screen. Things said to be absent (\"no nudity is shown\") don't count.",
    FLOORS,
    "\"Topless\" means bare female breasts. \"Shirtless\" and \"bare-chested\" mean a man unless the item says otherwise; a man's bare chest is 0 outside sexual scenes. When it's unclear whether a topless person is a man or a woman, score it as a woman (6).",
    "Talk and sound, lowest to highest: crude non-sexual words 0, compliments and flirting 1, innuendo and jokes 2, detailed sexual talk 3, sex heard but not seen 4.",
    "Artwork: stylised 2; realistic and lingered on 3; photos or video of real people count as real nudity.",
    "Censored, blurred or covered nudity is 4, even when it's clear the person is naked (a logo, sheet or shower curtain hiding them, or another character seeing what the viewer doesn't).",
    "Clothing is never nudity: underwear (boxers, briefs, bras), swimwear, bikinis, lingerie and towels are 2, however often they appear. Pictures or posters of people in swimwear or underwear are 2 at most.",
    "Making out is 2 for a scene, 1 when it's only a moment that's easy to skip.",
    "Nudity in a plainly non-sexual context (medical, childbirth, breastfeeding, bathing a child, changing room, naturist, corpse or autopsy, art-class model, strip search or prison, war or historical, cultural documentary) is scored at its body-part floor and marked non-sexual. Comedic nudity, skinny dipping, saunas and \"artistic\" photo shoots are scored normally.",
    "Sexual assault scenes are scored by explicitness like any other scene.",
    "If an item fits two levels, use the higher one. If it describes several scenes, use the most explicit one for the level and mark how often it happens.",
    "Violence, profanity, drugs and frightening content are irrelevant: score them 0 unless they include sexual content or nudity.",
]

# Extra points for how much there is.
EXTRA_FREQUENT = 1   # 4+ scenes within one level of the peak, or "several", "multiple", "numerous"
EXTRA_PROLONGED = 1  # a peak scene described as long or lasting minutes
EXTRA_PERVASIVE = 2  # "throughout", most episodes, or central to the story
EXTRA_CAP = 2        # most extra a title can get in total
EXTRA_CEILING = 9    # extra points never reach 10
FREQUENT_SCENES = 4

SCENE_THRESHOLD = 4  # sexual scenes at or above this count toward "N scenes"
MIN_SCENE_ITEMS = 3
MIN_VOTES = 25

NOTICE = "ISCCI is an experimental rubric being tested alongside the main rubric, so scores may change as it's tuned."


def band(level: Optional[int]) -> Optional[tuple]:
    if level is None:
        return None
    return next(b for b in BANDS if b[0] <= level <= b[1])


def emoji(level: Optional[int]) -> str:
    return "❔" if level is None else band(level)[3]


def colour(level: Optional[int]) -> str:
    return "grey" if level is None else band(level)[4]


def band_name(level: Optional[int]) -> str:
    return "" if level is None else band(level)[2]


def name(level: int) -> str:
    return LADDER[level][1]


def meaning(level: int) -> str:
    return LADDER[level][2]


def default_tag(level: int) -> str:
    """The usual content tag for a level, for items without a usable tag."""
    best = [t for t, lvl in TAGS if lvl <= level]
    return best[-1] if best else "None"


@dataclass
class ScoredItem:
    text: str
    is_spoiler: bool
    level: int
    tag: str
    is_scene: bool
    prolonged: bool
    momentary: bool = False
    amount: str = "once"              # "once", "several" or "throughout"
    nonsexual: Optional[str] = None   # a NONSEXUAL key
    warning: Optional[str] = None     # a production rubric.WARNINGS key, only on level-0 items


@dataclass
class Result:
    level: Optional[int]        # the title's score, None when IMDb has no flagged items
    star: bool                  # the score comes from non-sexual nudity
    tag: str                    # content tag of the scene that set the score (or the non-sexual context)
    peak: int                   # worst sexual scene
    peak_tag: str
    extra: int                  # extra points actually added
    frequent: bool
    prolonged: bool
    pervasive: bool
    scenes: int                 # sexual scenes at SCENE_THRESHOLD or above
    low_confidence: bool
    votes: int
    warnings: List[str] = field(default_factory=list)

    @property
    def pips(self) -> str:
        if self.pervasive:
            return "●●●"
        if self.frequent or self.prolonged:
            return "●●○"
        return "●○○"

    @property
    def presence(self) -> str:
        parts = [p for p, on in (("Pervasive", self.pervasive), ("Frequent", self.frequent),
                                 ("Prolonged", self.prolonged)) if on]
        return " · ".join(parts) or "Once or a few times"

    @property
    def shown(self) -> str:
        """The score as shown: "7/10", or "6*/10" for non-sexual nudity."""
        return "?" if self.level is None else f"{self.level}{'*' if self.star else ''}/10"


def effective_level(item: ScoredItem) -> int:
    """The item's level after the momentary rule (making out only)."""
    if item.momentary and item.tag == "Making out" and item.level == 2:
        return 1
    return item.level


def summarize(items: List[ScoredItem], votes: int, warning_keys) -> Result:
    if not items:
        return Result(level=None, star=False, tag="", peak=0, peak_tag="None", extra=0, frequent=False,
                      prolonged=False, pervasive=False, scenes=0, low_confidence=True, votes=votes)
    warnings = [w for w in warning_keys if any(i.warning == w for i in items)]
    sexual = [i for i in items if not i.nonsexual and not i.warning]
    nonsexual = [i for i in items if i.nonsexual and not i.warning]

    peak = max((effective_level(i) for i in sexual), default=0)
    top = [i for i in sexual if effective_level(i) == peak]
    # Prefer a specific scene's tag over an overview's.
    peak_tag = (sorted(top, key=lambda i: not i.is_scene)[0].tag if top and peak else "None")
    near = [i for i in sexual if peak and effective_level(i) >= peak - 1]
    frequent = bool(peak) and (sum(1 for i in near if i.is_scene) >= FREQUENT_SCENES
                               or any(i.amount == "several" for i in near))
    prolonged = bool(peak) and any(i.prolonged for i in top)
    pervasive = bool(peak) and any(i.amount == "throughout" for i in near)
    extra = 0
    if peak:
        extra = min(EXTRA_CAP, EXTRA_FREQUENT * frequent + EXTRA_PROLONGED * prolonged + EXTRA_PERVASIVE * pervasive)
    sexual_score = peak if peak >= EXTRA_CEILING else min(peak + extra, EXTRA_CEILING)
    extra = sexual_score - peak

    ns_peak = max((i.level for i in nonsexual), default=0)
    star = ns_peak > sexual_score
    if star:
        ns_item = max(nonsexual, key=lambda i: i.level)
        level, tag = ns_peak, NONSEXUAL.get(ns_item.nonsexual, "Non-sexual")
    else:
        level, tag = sexual_score, peak_tag
    return Result(
        level=level, star=star, tag=tag, peak=peak, peak_tag=peak_tag, extra=extra, frequent=frequent,
        prolonged=prolonged, pervasive=pervasive,
        scenes=sum(1 for i in sexual if i.is_scene and effective_level(i) >= SCENE_THRESHOLD),
        low_confidence=len([i for i in items if i.is_scene]) < MIN_SCENE_ITEMS and votes < MIN_VOTES,
        votes=votes, warnings=warnings,
    )


def auto_summary(result: Result) -> str:
    """A plain overview for titles without an AI-written summary."""
    if result.level is None:
        return "IMDb has no flagged sex or nudity items for this title yet."
    text = f"The most explicit sexual content is {result.peak_tag.lower()} ({name(result.peak).lower()})."
    if result.extra:
        text += f" {result.presence}, so it scores {result.extra} higher."
    if result.star:
        text += f" The highest level is non-sexual nudity ({result.tag.lower()})."
    return text
