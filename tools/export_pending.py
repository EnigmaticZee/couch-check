"""Lists titles that need offline AI scores, with their IMDb text, for a scoring session.

    .venv/bin/python tools/export_pending.py                # every cached title without an AI score
    .venv/bin/python tools/export_pending.py tt0120338 ...  # specific titles (fetched from IMDb if needed)

Writes work/pending.json (git-ignored; it contains IMDb's text and must not be published) and
work/rubric.txt, the exact instructions the scorer follows. After scoring, write
work/scored.json and run tools/import_scores.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pg import classify, imdb, rubric, scores  # noqa: E402
from pg.store import Store  # noqa: E402

WORK = ROOT / "work"


def main(ids: list) -> None:
    store = Store(str(ROOT / "cache.sqlite3"))
    if not ids:
        ids = [r[0] for r in store._db.execute("SELECT imdb_id FROM titles ORDER BY imdb_id")]
    done = scores.titles()
    pending = []
    for imdb_id in ids:
        entry = store.get(imdb_id)
        if entry is None:
            try:
                guide = imdb.fetch(imdb_id)
            except imdb.NotFound:
                guide = {"missing": True}
            store.put(imdb_id, guide, None, None)
        else:
            guide = entry["guide"]
        items = guide.get("items") or []
        if not items:
            continue
        if imdb_id in done and scores.lookup(imdb_id, items, classify.keywords_one) is not None:
            continue  # already AI-scored and still covers IMDb's items
        pending.append({
            "imdb_id": imdb_id,
            "title": guide.get("title"),
            "year": guide.get("year"),
            "items": [{"id": i["id"], "text": i["text"]} for i in items],
        })

    WORK.mkdir(exist_ok=True)
    (WORK / "pending.json").write_text(json.dumps(pending, indent=1, ensure_ascii=False))
    (WORK / "rubric.txt").write_text(
        classify.SYSTEM
        + f"\n\nRubric version: {rubric.VERSION}\n\n"
        "Output: work/scored.json as {\"<imdb id>\": {\"summary\": \"...\", \"items\": "
        "{\"<item id>\": [level, is_scene (0/1), prolonged (0/1)] (add the warning key as a 4th value "
        "on level-0 warning items), ...}}, ...}\n"
    )
    total = sum(len(p["items"]) for p in pending)
    print(f"{len(pending)} titles, {total} items -> work/pending.json")


if __name__ == "__main__":
    main(sys.argv[1:])
