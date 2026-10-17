"""SQLite cache: one row per title holding the IMDb data and the per-item classification."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date
from typing import Optional

REFRESH_AFTER = 21 * 24 * 3600  # how often ongoing shows and recent movies are re-fetched from IMDb
SERIES_TYPES = {"tvSeries", "tvMiniSeries"}


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS titles (
                    imdb_id TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    guide TEXT NOT NULL,        -- JSON from imdb.fetch(), or {"missing": true}
                    classified TEXT,            -- JSON list of {level, is_scene, prolonged}, aligned with guide items
                    classifier TEXT,            -- e.g. "ai:openai/gpt-oss-120b@3" or "keywords"
                    summary TEXT                -- the AI's overview of the title, if any
                )"""
            )
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(titles)")}
            if "summary" not in columns:  # caches created before summaries existed
                self._db.execute("ALTER TABLE titles ADD COLUMN summary TEXT")
            self._db.commit()

    def get(self, imdb_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT fetched_at, guide, classified, classifier, summary FROM titles WHERE imdb_id = ?", (imdb_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "fetched_at": row[0],
            "guide": json.loads(row[1]),
            "classified": json.loads(row[2]) if row[2] else None,
            "classifier": row[3],
            "summary": row[4],
        }

    def ai_scored(self, version: float) -> list:
        """Every cached title the AI scored under this rubric version, as entries with imdb_id."""
        with self._lock:
            rows = self._db.execute(
                "SELECT imdb_id, fetched_at, guide, classified, classifier, summary FROM titles"
                " WHERE classifier LIKE 'ai:%' AND classifier LIKE ?", (f"%@{version}",)
            ).fetchall()
        return [{"imdb_id": r[0], "fetched_at": r[1], "guide": json.loads(r[2]),
                 "classified": json.loads(r[3]) if r[3] else None, "classifier": r[4], "summary": r[5]}
                for r in rows]

    def put(self, imdb_id: str, guide: dict, classified: Optional[list], classifier: Optional[str],
            fetched_at: Optional[float] = None, summary: Optional[str] = None) -> None:
        """fetched_at: when the guide came from IMDb; defaults to now (pass the old value when only re-scoring)."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO titles (imdb_id, fetched_at, guide, classified, classifier, summary)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (imdb_id, fetched_at or time.time(), json.dumps(guide),
                 json.dumps(classified) if classified is not None else None, classifier, summary),
            )
            self._db.commit()


def is_finished(guide: dict) -> bool:
    """Shows that ended before this year and movies more than a year old: their guide rarely changes."""
    this_year = date.today().year
    if guide.get("type") in SERIES_TYPES:
        return bool(guide.get("end_year")) and guide["end_year"] < this_year
    return bool(guide.get("year")) and guide["year"] < this_year - 1


def is_stale(entry: dict) -> bool:
    """Finished titles are kept permanently; ongoing shows, recent movies and titles with no guide
    entries yet are re-fetched every REFRESH_AFTER. tools/refresh.py forces a re-fetch."""
    guide = entry["guide"]
    if guide.get("items") and is_finished(guide):
        return False
    return time.time() - entry["fetched_at"] > REFRESH_AFTER
