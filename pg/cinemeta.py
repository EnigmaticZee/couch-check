"""Fetches Stremio's default metadata (Cinemeta) so the addon can pass it through with the score added."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Dict, Optional, Tuple

BASE = "https://v3-cinemeta.strem.io"
TTL = 6 * 3600

_cache: Dict[Tuple[str, str], Tuple[float, dict]] = {}
_lock = threading.Lock()


def meta(kind: str, imdb_id: str) -> Optional[dict]:
    key = (kind, imdb_id)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    req = urllib.request.Request(f"{BASE}/meta/{kind}/{imdb_id}.json", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp).get("meta")
    except (OSError, ValueError):
        return hit[1] if hit else None
    if data:
        with _lock:
            _cache[key] = (time.time(), data)
    return data
