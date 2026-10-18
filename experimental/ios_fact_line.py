"""Experiment "ios_fact_line": show the score in the fact line (runtime · year · IMDb) on the iPhone and iPad app.

The Apple app (User-Agent "Stremio-Apple/…") asks Couch Check for a title's details but doesn't draw custom chips,
so the score never shows on its main screen. It draws the runtime text exactly as sent (the year it builds from the
release date, so the year text can't carry anything), so for Apple requests the score follows the runtime:
"44 min · 🟥 8 · 2011 · 7.9 IMDb". The details (level name, scenes, warnings) go in the description instead,
the same line Samsung TVs get (see tv_description.py). Other apps get the meta unchanged.

On by default, since it only changes answers to the Apple app. PG_IOS_FACT_LINE=off switches it off.
"""
from __future__ import annotations

import os
from typing import List

NAME = "ios_fact_line"
APPLE_AGENTS = ("stremio-apple",)


def is_on() -> bool:
    return os.environ.get("PG_IOS_FACT_LINE", "on").lower() != "off"


def is_apple(user_agent: str) -> bool:
    agent = (user_agent or "").lower()
    return any(name in agent for name in APPLE_AGENTS)


def apply(meta: dict, chips: List[str], user_agent: str) -> dict:
    """The meta with the main score ("🟥 8", no "/10") after the runtime, for Apple requests while switched on."""
    if not is_on() or not is_apple(user_agent) or not chips:
        return meta
    score = chips[0].split(" · ")[0]  # e.g. "🟥 8/10" from "🟥 8/10 · Sex Scene with Nudity · 8 Scenes · AI"
    if "/10" not in score:
        return meta  # still scoring, or no IMDb guide: leave the line alone
    score = score.replace("/10", "")  # the fact line is tight: "🟥 8", or "🟧 5*" for non-sexual nudity
    runtime = (meta.get("runtime") or "").strip()
    return {**meta, "runtime": f"{runtime} · {score}" if runtime else score}
