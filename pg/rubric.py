"""The Sex/Nudity ladder and how per-scene levels roll up into a title result."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Bump when the ladder's meaning changes, so AI-scored titles get re-scored.
VERSION = 5.2

# (level, short name, what you'll see)
LADDER = [
    (0, "None", "Nothing sexual or revealing."),
    (1, "Affection", "Hugs, quick kisses, holding hands, flirting and compliments like \"sexy\" or \"hot\", sex mentioned or discussed in general without detail."),
    (2, "Romance", "Passionate or long kissing, making out fully clothed, couple lying in bed clothed."),
    (3, "Suggestive", "Sexual jokes and innuendo, sexual talk, revealing clothing (cleavage, lingerie, underwear including thongs, swimwear), camera lingering on bodies, nudity only in drawings or paintings."),
    (4, "Implied", "Undressing that cuts away, couple in bed under sheets afterwards, sounds from off-screen, nudity that is censored, blurred or strategically covered. Nothing actually visible."),
    (5, "Brief Partial Nudity", "Bare buttocks, bare back or side views actually visible, usually in a non-sexual setting."),
    (6, "Sex Scene, Covered", "Sex shown on screen with movement and sounds, but under sheets or clothed. No nudity."),
    (7, "Topless", "Bare female breasts visible, either outside a sex scene or briefly within one."),
    (8, "Sex Scene with Nudity", "Sex on screen with breasts or buttocks visible, not graphic."),
    (9, "Full Frontal", "Genitals visible, male or female, in any setting."),
    (10, "Graphic", "Long or graphic sex, genitals in a sexual context, oral sex shown, near-pornographic."),
]

# Shown wherever scores are explained while the rubric is still being tuned.
BETA_NOTICE = "Couch Check is in beta: the rubric is still being tuned, so a score can be off. The scene list shows exactly what was counted."

# Rules for edge cases the ladder alone leaves open. Shown to the AI scorer and on the pages.
RULES = [
    "Score what is actually shown or heard on screen. Things said to be absent (\"no nudity is shown\", \"it cuts away before anything happens\") do not count.",
    "Revealing clothing (cleavage, lingerie, any underwear including boxers, briefs, bras and thongs, swimwear, towels) is level 3 however long it lasts, including close-ups of breasts in a bra or of someone in underwear; mark it prolonged when it is lengthy or throughout. Score higher only when bare buttocks, breasts or genitals are actually shown.",
    "\"Naked\" or \"nude\" without saying what is visible is level 5. Breasts count (7) only when the item says they are visible, and genitals (9) only when it says they are visible.",
    "A bare chest on a man or boy (shirtless, bare-chested, topless men) is level 0 in a non-sexual setting such as sport, wrestling, swimming, work or getting dressed. Only when the scene is sexual is it scored on the ladder as usual. \"Shirtless\" and \"bare-chested\" mean a man unless the item says otherwise. Topless (level 7) is for bare female breasts; when an item doesn't make clear whether a topless person is a man or a woman, score it as a woman.",
    "Nudity that is censored, blurred, pixelated or covered (by objects, logos, sheets or camera angles) is level 4, even when the context makes clear the person is naked.",
    "Nudity seen only in artwork (drawings, sketches, paintings, sculptures, posters) is level 3, or 4 if the artwork is realistic and lingered on. Photos or video of real people count as real nudity.",
    "Crude or non-sexual mentions of body parts (threats, insults, toilet or bodily humour) are level 0. Compliments and flirting (calling someone \"sexy\", \"hot\" or \"gorgeous\", flirty remarks) are level 1, and so is sex mentioned or discussed in general without detail (\"sex is a common topic\", \"some sexual references\"). Actual sexual talk, jokes or innuendo are level 3.",
    "Talk about anything is scored as talk (level 1 or 3), never at the level of what is being talked about: dialogue about erections, nudity or sex acts is 3, not 9 or 10.",
    "General comments about the whole title (not a specific scene) that call it \"graphic\", \"explicit\" or \"steamy\" without saying what is shown are level 6 at most.",
    "Nudity in a plainly non-sexual setting (medical, childbirth, breastfeeding, bathing a child, changing room, naturist, a corpse or autopsy, an art-class model, a strip search or prison, war or historical scenes, a cultural documentary, or sci-fi and horror scenes where the nudity isn't sexual, like a creature, an experiment or someone waking up naked) is level 5 at most and marked non-sexual. In a sci-fi or horror title (see the title line), nudity that isn't part of a sexual or seductive scene counts as non-sexual even when the item doesn't name the setting. Comedic nudity, skinny dipping, saunas and \"artistic\" photo shoots are scored normally.",
    "If an item fits two levels, use the higher one. If it describes several scenes, use the most explicit one.",
    "Sexual assault scenes are scored by explicitness like any other scene.",
    "Violence, profanity, drugs and frightening content are irrelevant: score them 0 unless they include sexual content or nudity.",
]

# Non-sexual nudity: capped at NONSEXUAL_CAP and marked * (rubric v5.2).
NONSEXUAL = {
    "medical": "Medical",
    "childbirth": "Childbirth",
    "breastfeeding": "Breastfeeding",
    "bathing_child": "Bathing a Child",
    "changing_room": "Changing Room",
    "naturist": "Naturist",
    "corpse": "Corpse or Autopsy",
    "art_model": "Art-Class Model",
    "strip_search": "Strip Search or Prison",
    "war_history": "War or Historical",
    "cultural": "Cultural Documentary",
    "scifi_horror": "Sci-Fi or Horror",
}
NONSEXUAL_CAP = 5

# Serious themes flagged next to the score; they never change it.
# The first three are for items with nothing shown (level 0): shown in place of a level.
# ANY_LEVEL ones can sit on a scored scene too, which keeps its level (rubric v5.2, warnings added later).
WARNINGS = {
    "assault": "Sexual Assault",
    "minor": "Involves a Minor",
    "incest": "Incest",
    "exploitation": "Exploitation",
    "violence_talk": "Sexual Violence Discussed",
}
ANY_LEVEL = {"assault", "violence_talk"}
WARNING_RULE = (
    "warning: a key when the item involves one of these serious themes, otherwise null. "
    "\"assault\": sexual assault or abuse shown or clearly happening on screen, at any level (still give its level by what is shown). "
    "\"violence_talk\": sexual violence or abuse discussed at length or as a story topic, at any level. "
    "Only for items at level 0 (talked about or part of the story, not shown): \"minor\" (romantic or sexual involvement of "
    "someone under 18), \"incest\" (between family members), \"exploitation\" (trafficking, coercion or forced sex work). "
    "One key per item; if several fit, use the first in this order: assault, minor, exploitation, incest, violence_talk."
)


def allowed_warning(key, level: int):
    """The warning key if it may sit on an item at this level, else None."""
    if key in WARNINGS and (level == 0 or key in ANY_LEVEL):
        return key
    return None

# Scenes at or above this level count toward the "N scenes" frequency.
SCENE_THRESHOLD = 4

# Below these, the result is marked low confidence.
MIN_SCENE_ITEMS = 3
MIN_VOTES = 25


def name(level: int) -> str:
    return LADDER[level][1]


def meaning(level: int) -> str:
    return LADDER[level][2]


# One coloured square per level (owner's colour scale, 18 Oct 2026): red only for the most explicit (8–10).
SQUARES = ["🟩", "🟩", "🟨", "🟨", "🟧", "🟧", "🟪", "🟪", "🟥", "🟥", "🟥"]


def emoji(level: Optional[int]) -> str:
    return "❔" if level is None else SQUARES[level]


@dataclass
class ScoredItem:
    text: str
    is_spoiler: bool
    level: int
    is_scene: bool  # a specific scene, as opposed to a general/overview statement
    prolonged: bool
    warning: Optional[str] = None  # a WARNINGS key, only on level-0 items
    nonsexual: Optional[str] = None  # a NONSEXUAL key: non-sexual nudity, capped and marked *
    shown: str = ""  # the AI's short note of what is actually visible or heard

    @property
    def unscored(self) -> bool:
        """A warning item with nothing shown: listed as a warning instead of a level."""
        return bool(self.warning) and self.level == 0


@dataclass
class Result:
    level: Optional[int]  # None when IMDb has no flagged items
    scenes: int
    prolonged: int
    low_confidence: bool
    votes: int
    warnings: List[str] = field(default_factory=list)  # WARNINGS keys present in the title
    star: bool = False  # the level comes only from non-sexual nudity
    top: Optional[ScoredItem] = None  # the item that set the level ("why this score")
    override: Optional[dict] = None  # the owner's own level for this title: {level, note, ai_level}

    @property
    def shown(self) -> str:
        """The level as shown: "7/10", "5*/10" for non-sexual nudity, "?" without items."""
        return "?" if self.level is None else f"{self.level}{'*' if self.star else ''}/10"


def summarize(items: List[ScoredItem], votes: int) -> Result:
    if not items:
        return Result(level=None, scenes=0, prolonged=0, low_confidence=True, votes=votes)
    warnings = [w for w in WARNINGS if any(i.warning == w for i in items)]
    level = max(i.level for i in items)
    flagged = [i for i in items if i.is_scene and i.level >= SCENE_THRESHOLD]
    at_top = [i for i in items if i.level == level]
    top = sorted(at_top, key=lambda i: (bool(i.nonsexual), not i.is_scene))[0]
    return Result(
        level=level,
        scenes=len(flagged),
        prolonged=sum(1 for i in flagged if i.prolonged),
        low_confidence=len([i for i in items if i.is_scene]) < MIN_SCENE_ITEMS and votes < MIN_VOTES,
        votes=votes,
        warnings=warnings,
        star=level > 0 and all(i.nonsexual for i in at_top),
        top=top,
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
    text = f"The most explicit content is {name(result.level).lower()} ({result.shown}){where}."
    if result.star:
        text += " It's non-sexual nudity."
    if any(i.prolonged for i in top):
        text += " Some of it is described as prolonged."
    rest = [i.level for i in items if i.level < result.level]
    if rest:
        common = max(set(rest), key=lambda lvl: (rest.count(lvl), lvl))
        text += f" Most of the rest is {name(common).lower()}."
    return text
