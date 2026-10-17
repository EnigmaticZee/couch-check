"""Saves AI scores into data/scores.json, so they survive redeploys and cache resets.

    python3 tools/save_scores.py                        # from the local cache (cache.sqlite3)
    python3 tools/save_scores.py https://your-host      # from a running server

For a server, set the same PG_EXPORT_TOKEN as the server (in .env or the environment).
Titles already in the file are updated; others are kept. If the rubric version changed,
the file starts fresh, since older scores no longer apply.

Afterwards commit data/scores.json and redeploy: the addon loads it on start, so those
titles show their AI score straight away without asking the AI again.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pg import rubric, scores  # noqa: E402
from pg.store import Store  # noqa: E402


def env_value(name: str) -> str:
    """A setting from the environment, falling back to the project's .env file."""
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == name:
                return value.strip().strip("'\"")
    return ""


def from_server(url: str) -> dict:
    token = env_value("PG_EXPORT_TOKEN")
    if not token:
        sys.exit("Set PG_EXPORT_TOKEN (the same value as on the server) in .env or the environment.")
    req = urllib.request.Request(
        url.rstrip("/").removesuffix("/manifest.json") + "/export/scores.json",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "couch-check-tools"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit("The server refused: check that PG_EXPORT_TOKEN matches the server's." if e.code == 404
                 else f"The server answered HTTP {e.code}.")


def from_local() -> dict:
    store = Store(os.environ.get("PG_DB", str(ROOT / "cache.sqlite3")))
    return {"rubric_version": rubric.VERSION, "titles": scores.from_cache(store.ai_scored(rubric.VERSION))}


def main(args: list) -> None:
    incoming = from_server(args[0]) if args else from_local()
    if incoming.get("rubric_version") != rubric.VERSION:
        sys.exit(f"Those scores are rubric v{incoming.get('rubric_version')}; this code is v{rubric.VERSION}.")

    data = scores.load_file()
    if data.get("rubric_version") != rubric.VERSION:
        print(f"{scores.PATH.name} was rubric v{data.get('rubric_version')}; starting it fresh for v{rubric.VERSION}.")
        data = {"rubric_version": rubric.VERSION, "titles": {}}

    added = sum(1 for k in incoming["titles"] if k not in data["titles"])
    updated = len(incoming["titles"]) - added
    data["titles"].update(incoming["titles"])
    scores.save_file(data)
    print(f"Saved {len(incoming['titles'])} titles ({added} new, {updated} updated); "
          f"{scores.PATH.name} now has {len(data['titles'])}.")


if __name__ == "__main__":
    main(sys.argv[1:])
