"""Experiment "tv_description": show the score in the title description on Samsung TVs.

Samsung's Stremio app (Tizen) asks for meta and streams, but shows neither the Couch Check chip (a custom links
category) nor the stream row (link-only, and a TV has no browser). Every app shows the description, so for
Tizen requests the chip's text becomes its first line. Other apps get the meta unchanged.

On by default, since it only changes answers to Samsung TVs. PG_TV_DESCRIPTION=off switches it off.
"""
from __future__ import annotations

import os
from typing import List, Union

NAME = "tv_description"
SCORE_MARKS = ("🟩", "🟨", "🟧", "🟪", "🟥", "❔", "⏳", "ISCCI")  # how a score line starts
# Matched against the request's User-Agent: Samsung TVs ("Mozilla/5.0 (SMART-TV; LINUX; Tizen 9.0)") and the
# iPhone and iPad app ("Stremio-Apple/0.5.1 (iPhone…)"), which also hides the chip but shows the description.
TV_AGENTS = ("tizen", "stremio-apple")


def is_on() -> bool:
    return os.environ.get("PG_TV_DESCRIPTION", "on").lower() != "off"


def is_tv(user_agent: str) -> bool:
    agent = (user_agent or "").lower()
    return any(name in agent for name in TV_AGENTS)


def apply(meta: dict, chips: Union[str, List[str]], user_agent: str) -> dict:
    """The meta with the chip text(s) as the first line of its description, for TV requests while switched on."""
    if not is_on() or not is_tv(user_agent):
        return meta
    chips = [chips] if isinstance(chips, str) else chips
    line = " | ".join(chips)  # starts with the coloured square, e.g. "🟥 8/10 · Sex Scene with Nudity · …"
    description = (meta.get("description") or "").strip()
    # Samsung's app drops line breaks, so " ||" marks where the score ends and the synopsis begins.
    meta = {**meta, "description": f"{line} ||\n\n{description}" if description else line}
    # With an episode selected, the TV shows that episode's summary instead, so each one gets the line too:
    # its own score if episode scores already added one, otherwise the show's score.
    if meta.get("videos"):
        videos = []
        for v in meta["videos"]:
            summary = (v.get("overview") or v.get("description") or "").strip()
            if not summary.startswith(SCORE_MARKS):  # episode scores may already have added the episode's own line
                summary = f"{line} ||\n\n{summary}" if summary else line
            videos.append({**v, "overview": summary, "description": summary})
        meta["videos"] = videos
    return meta
