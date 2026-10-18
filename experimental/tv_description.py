"""Experiment "tv_description": show the score in the title description on Samsung TVs.

Samsung's Stremio app (Tizen) asks for meta and streams, but shows neither the Couch Check chip (a custom links
category) nor the stream row (link-only, and a TV has no browser). Every app shows the description, so for
Tizen requests the chip's text becomes its first line. Other apps get the meta unchanged.
"""
from __future__ import annotations

from . import enabled

NAME = "tv_description"
# Matched against the request's User-Agent, e.g. "Mozilla/5.0 (SMART-TV; LINUX; Tizen 9.0)".
TV_AGENTS = ("tizen",)


def is_on() -> bool:
    return enabled(NAME)


def is_tv(user_agent: str) -> bool:
    agent = (user_agent or "").lower()
    return any(name in agent for name in TV_AGENTS)


def apply(meta: dict, chip: str, user_agent: str) -> dict:
    """The meta with the chip's text as the first line of its description, for TV requests while switched on."""
    if not is_on() or not is_tv(user_agent):
        return meta
    line = f"Couch Check: {chip}"
    description = (meta.get("description") or "").strip()
    return {**meta, "description": f"{line}\n\n{description}" if description else line}
