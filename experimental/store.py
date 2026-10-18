"""A small SQLite key-value store for experiments, kept apart from production's cache.

It lives next to production's cache (PG_DB) as experimental.sqlite3, or at PG_EXPERIMENTAL_DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

_default = Path(os.environ.get("PG_DB", Path(__file__).parent.parent / "cache.sqlite3")).with_name("experimental.sqlite3")
PATH = os.environ.get("PG_EXPERIMENTAL_DB") or str(_default)


class KV:
    def __init__(self, path: str = PATH):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, key TEXT, value TEXT, updated REAL,"
                             " PRIMARY KEY (ns, key))")
            self._db.commit()

    def get(self, ns: str, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE ns = ? AND key = ?", (ns, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, ns: str, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv (ns, key, value, updated) VALUES (?, ?, ?, ?)",
                             (ns, key, json.dumps(value), time.time()))
            self._db.commit()


_kv: Optional[KV] = None
_kv_lock = threading.Lock()


def kv() -> KV:
    global _kv
    with _kv_lock:
        if _kv is None:
            _kv = KV()
        return _kv
