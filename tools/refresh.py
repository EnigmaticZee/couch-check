"""Forces specific titles to be fetched from IMDb and scored again on their next view.

    python3 tools/refresh.py tt0120338 tt1442464 ...

Finished titles (ended shows, movies more than a year old) are otherwise kept permanently.
Run this on the machine the server runs on; it uses the same cache (PG_DB).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(ids: list) -> None:
    ids = [i for i in ids if re.fullmatch(r"tt\d+", i)]
    if not ids:
        sys.exit(__doc__)
    db = sqlite3.connect(os.environ.get("PG_DB", str(ROOT / "cache.sqlite3")))
    for imdb_id in ids:
        removed = db.execute("DELETE FROM titles WHERE imdb_id = ?", (imdb_id,)).rowcount
        print(f"{imdb_id}: {'will refresh on next view' if removed else 'not cached'}")
    db.commit()


if __name__ == "__main__":
    main(sys.argv[1:])
