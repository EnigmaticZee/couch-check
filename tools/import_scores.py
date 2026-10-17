"""Merges work/scored.json into data/scores.json after checking it against work/pending.json.

    .venv/bin/python tools/import_scores.py

data/scores.json is safe to publish: it holds only IMDb item IDs and our levels, no IMDb text.
A running server picks up the change automatically.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pg import rubric, scores  # noqa: E402


def dump(data: dict) -> str:
    """One line per item, so a score update reads as a clear diff."""
    titles = []
    for imdb_id, t in data["titles"].items():
        items = ",\n".join(f'   "{k}": {json.dumps(v)}' for k, v in t["items"].items())
        summary = f'"summary": {json.dumps(t["summary"], ensure_ascii=False)}, ' if t.get("summary") else ""
        titles.append(f'  "{imdb_id}": {{"title": {json.dumps(t["title"], ensure_ascii=False)}, '
                      f'"scored": "{t["scored"]}", {summary}"items": {{\n{items}\n  }}}}')
    return f'{{\n "rubric_version": {data["rubric_version"]},\n "titles": {{\n' + ",\n".join(titles) + "\n }\n}\n"


def main() -> None:
    pending = {p["imdb_id"]: p for p in json.loads((ROOT / "work" / "pending.json").read_text())}
    scored = json.loads((ROOT / "work" / "scored.json").read_text())

    path = scores.PATH
    data = json.loads(path.read_text()) if path.exists() else {"rubric_version": rubric.VERSION, "titles": {}}
    if data.get("rubric_version") != rubric.VERSION:
        sys.exit(f"{path.name} is rubric v{data.get('rubric_version')}, code is v{rubric.VERSION}; re-score everything first")

    problems, merged = [], 0
    for imdb_id, entry in scored.items():
        # Each title is {"summary": "...", "items": {...}}; a bare items map (no summary) is accepted too.
        items = entry.get("items", entry) if isinstance(entry, dict) else entry
        summary = entry.get("summary") if isinstance(entry, dict) and "items" in entry else None
        p = pending.get(imdb_id)
        if p is None:
            problems.append(f"{imdb_id}: not in pending.json")
            continue
        expected = {i["id"] for i in p["items"]}
        if set(items) != expected:
            problems.append(f"{imdb_id}: scored {len(set(items) & expected)}/{len(expected)} items, "
                            f"{len(set(items) - expected)} unknown")
            continue
        bad = [k for k, v in items.items() if not (isinstance(v, list) and len(v) == 3
               and isinstance(v[0], int) and 0 <= v[0] <= 10 and v[1] in (0, 1) and v[2] in (0, 1))]
        if bad:
            problems.append(f"{imdb_id}: malformed scores for {bad[:3]}")
            continue
        data["titles"][imdb_id] = {"title": p["title"], "scored": date.today().isoformat(), "items": items}
        if summary:
            data["titles"][imdb_id]["summary"] = " ".join(summary.split())
        merged += 1

    if problems:
        print("Not imported:\n  " + "\n  ".join(problems))
    path.parent.mkdir(exist_ok=True)
    data["titles"] = dict(sorted(data["titles"].items()))
    path.write_text(dump(data))
    print(f"Imported {merged} titles; {path.name} now has {len(data['titles'])}")


if __name__ == "__main__":
    main()
