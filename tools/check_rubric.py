"""Scores the reference titles in data/reference.json from scratch and shows which land on their target.

    python3 tools/check_rubric.py              # every reference title
    python3 tools/check_rubric.py tt0106179    # just these

Run it after any change to the rubric or the AI instructions, so fixing one title doesn't quietly break another.
It fetches IMDb live and uses the AI from .env (about two AI calls per title); it doesn't touch the server's cache.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env()

from pg import classify, imdb, rubric  # noqa: E402  (reads settings from the environment at import)
from pg.service import score  # noqa: E402


def main(ids: list) -> None:
    refs = json.loads((ROOT / "data" / "reference.json").read_text())
    if ids:
        refs = [r for r in refs if r["id"] in ids]
    if not classify.ai_enabled():
        print("No AI configured in .env: these are keyword estimates only.\n")
    print(f"Rubric v{rubric.VERSION}\n")
    on_target = 0
    for ref in refs:
        guide = imdb.fetch(ref["id"])
        texts = [i["text"] for i in guide.get("items", [])]
        for attempt in range(4):  # Groq's free tier limits tokens per minute; wait rather than fall back
            classified, classifier, _ = classify.classify(texts, classify.context(guide))
            if not texts or classify.is_ai(classifier) or not classify.ai_enabled():
                break
            time.sleep(30)
        result, _ = score({"guide": guide, "classified": classified, "classifier": classifier, "summary": None})
        level = result.level if result.level is not None else 0
        ok = ref["min"] <= level <= ref["max"]
        on_target += ok
        target = str(ref["min"]) if ref["min"] == ref["max"] else f"{ref['min']}–{ref['max']}"
        top = result.top
        why = (top.shown or top.text[:70]) if top else "no items"
        source = "AI" if classify.is_ai(classifier) else "Est."
        print(f"{'✓' if ok else '✗'} {ref['title']:<26} target {target:<5} got {result.shown:<6} {source:<4} "
              f"{rubric.name(level) if result.level is not None else ''}")
        print(f"    set by: {why}" + (f"   ({ref['note']})" if ref.get("note") else ""))
    print(f"\n{on_target} of {len(refs)} on target")


if __name__ == "__main__":
    main(sys.argv[1:])
