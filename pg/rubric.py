"""The Sex/Nudity ladder and how per-scene levels roll up into a title result."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Bump when the ladder's meaning changes, so AI-scored titles get re-scored.
VERSION = 5

# (level, short name, what you'll see)
LADDER = [
    (0, "None", "Nothing sexual or revealing."),
    (1, "Affection", "Hugs, quick kisses, holding hands."),
    (2, "Romance", "Passionate or long kissing, making out fully clothed, couple lying in bed clothed."),
    (3, "Suggestive", "Sexual jokes and innuendo, sexual talk, revealing clothing (cleavage, lingerie, underwear including thongs, swimwear), camera lingering on bodies, nudity only in drawings or paintings."),
    (4, "Implied", "Undressing that cuts away, couple in bed under sheets afterwards, sounds from off-screen, nudity that is censored, blurred or strategically covered. Nothing actually visible."),
    (5, "Brief partial nudity", "Bare buttocks, bare back or side views actually visible, usually in a non-sexual setting."),
    (6, "Sex scene, covered", "Sex shown on screen with movement and sounds, but under sheets or clothed. No nudity."),
    (7, "Topless", "Bare breasts visible, either outside a sex scene or briefly within one."),
    (8, "Sex scene with nudity", "Sex on screen with breasts or buttocks visible, not graphic."),
    (9, "Full frontal", "Genitals visible, male or female, in any setting."),
    (10, "Graphic", "Long or graphic sex, genitals in a sexual context, oral sex shown, near-pornographic."),
]

# Shown wherever scores are explained while the rubric is still being tuned.
BETA_NOTICE = "Couch Check is in beta: the rubric is still being tuned, so a score can be off. The scene list shows exactly what was counted."

# Rules for edge cases the ladder alone leaves open. Shown to the AI scorer and on the pages.
RULES = [
    "Score what is actually shown or heard on screen. Things said to be absent (\"no nudity is shown\", \"it cuts away before anything happens\") do not count.",
    "Revealing clothing (cleavage, lingerie, any underwear including boxers, briefs, bras and thongs, swimwear, towels) is level 3 however long it lasts; mark it prolonged when it is lengthy or throughout. Score higher only when bare buttocks, breasts or genitals are actually shown.",
    "Nudity that is censored, blurred, pixelated or covered (by objects, logos, sheets or camera angles) is level 4, even when the context makes clear the person is naked.",
    "Nudity seen only in artwork (drawings, sketches, paintings, sculptures, posters) is level 3, or 4 if the artwork is realistic and lingered on. Photos or video of real people count as real nudity.",
    "Crude or non-sexual mentions of body parts (threats, insults, toilet or bodily humour) are level 0. Only sexual talk, jokes or innuendo are level 3.",
    "If an item fits two levels, use the higher one. If it describes several scenes, use the most explicit one.",
    "Sexual assault scenes are scored by explicitness like any other scene.",
    "Violence, profanity, drugs and frightening content are irrelevant: score them 0 unless they include sexual content or nudity.",
]

# Serious themes that can't be scored because nothing is shown (the item would be level 0).
# They're shown as a warning in place of the score and never count toward the title's level.
# Anything actually shown on screen is scored on the ladder as usual instead.
WARNINGS = {
    "minor": "Involves a minor",
    "incest": "Incest",
    "exploitation": "Exploitation",
}
WARNING_RULE = (
    "warning: only for items at level 0. If such an item still involves one of these serious themes, "
    "talked about or part of the story but not shown, set warning to its key: "
    "\"minor\" (romantic or sexual involvement of someone under 18), \"incest\" (between family members), "
    "\"exploitation\" (trafficking, coercion or forced sex work). Otherwise, and for every item above level 0, use null."
)

# Scenes at or above this level count toward the "N scenes" frequency.
SCENE_THRESHOLD = 4

# Below these, the result is marked low confidence.
MIN_SCENE_ITEMS = 3
MIN_VOTES = 25


def name(level: int) -> str:
    return LADDER[level][1]


def meaning(level: int) -> str:
    return LADDER[level][2]


def emoji(level: Optional[int]) -> str:
    if level is None:
        return "❔"
    if level <= 2:
        return "🟢"
    if level <= 4:
        return "🟡"
    if level <= 6:
        return "🟠"
    return "🔴"


@dataclass
class ScoredItem:
    text: str
    is_spoiler: bool
    level: int
    is_scene: bool  # a specific scene, as opposed to a general/overview statement
    prolonged: bool
    warning: Optional[str] = None  # a WARNINGS key, only on level-0 items


@dataclass
class Result:
    level: Optional[int]  # None when IMDb has no flagged items
    scenes: int
    prolonged: int
    low_confidence: bool
    votes: int
    warnings: List[str] = field(default_factory=list)  # WARNINGS keys present in the title


def summarize(items: List[ScoredItem], votes: int) -> Result:
    if not items:
        return Result(level=None, scenes=0, prolonged=0, low_confidence=True, votes=votes)
    warnings = [w for w in WARNINGS if any(i.warning == w for i in items)]
    level = max(i.level for i in items)
    flagged = [i for i in items if i.is_scene and i.level >= SCENE_THRESHOLD]
    return Result(
        level=level,
        scenes=len(flagged),
        prolonged=sum(1 for i in flagged if i.prolonged),
        low_confidence=len([i for i in items if i.is_scene]) < MIN_SCENE_ITEMS and votes < MIN_VOTES,
        votes=votes,
        warnings=warnings,
    )


def warning_text(result: Result) -> str:
    """e.g. "Involves a minor" or "Involves a minor, Incest", for titles with warnings."""
    return ", ".join(WARNINGS[w] for w in result.warnings)


def auto_summary(result: Result, items: List[ScoredItem]) -> str:
    """A plain overview built from the counts, for titles without an AI-written summary."""
    if result.level is None:
        return "IMDb has no flagged sex or nudity items for this title yet."
    top = [i for i in items if i.level == result.level]
    scenes = sum(1 for i in top if i.is_scene)
    where = f", in {scenes} scene{'s' if scenes != 1 else ''}" if scenes > 1 else ""
    text = f"The most explicit content is {name(result.level).lower()} ({result.level}/10){where}."
    if any(i.prolonged for i in top):
        text += " Some of it is described as prolonged."
    rest = [i.level for i in items if i.level < result.level]
    if rest:
        common = max(set(rest), key=lambda lvl: (rest.count(lvl), lvl))
        text += f" Most of the rest is {name(common).lower()}."
    return text
