"""Fetches a title's Sex & Nudity parents guide from IMDb's (unofficial) GraphQL endpoint."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Optional

ENDPOINT = "https://caching.graphql.imdb.com/"
MIN_INTERVAL = 1.0  # seconds between requests to IMDb

QUERY = """
query PG($id: ID!) {
  title(id: $id) {
    titleText { text }
    releaseYear { year }
    titleType { id }
    parentsGuide {
      categories { category { id } totalSeverityVotes severityBreakdown { voteType votedFor } }
      guideItems(first: 250, filter: { categories: ["NUDITY"] }) {
        edges { node { id isSpoiler text { plainText } } }
      }
    }
  }
}
"""

_lock = threading.Lock()
_last_request = 0.0


class NotFound(Exception):
    pass


def fetch(imdb_id: str) -> dict:
    """Returns {title, year, type, votes: {NONE, MILD, MODERATE, SEVERE}, items: [{id, text, is_spoiler}]}."""
    global _last_request
    body = json.dumps({"query": QUERY, "variables": {"id": imdb_id}}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-imdb-client-name": "imdb-web-next-localized",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
        finally:
            _last_request = time.monotonic()

    if data.get("errors") and not data.get("data"):
        raise RuntimeError(f"IMDb error: {data['errors'][0].get('message')}")
    title = (data.get("data") or {}).get("title")
    if not title or not title.get("titleText"):
        raise NotFound(imdb_id)

    guide = title.get("parentsGuide") or {}
    votes = {"NONE": 0, "MILD": 0, "MODERATE": 0, "SEVERE": 0}
    for cat in guide.get("categories") or []:
        if cat["category"]["id"] == "NUDITY":
            for b in cat.get("severityBreakdown") or []:
                votes[b["voteType"]] = b["votedFor"]
    edges = ((guide.get("guideItems") or {}).get("edges")) or []
    items = [
        {"id": e["node"]["id"], "text": e["node"]["text"]["plainText"], "is_spoiler": bool(e["node"]["isSpoiler"])}
        for e in edges
        if e["node"].get("text")
    ]
    return {
        "title": title["titleText"]["text"],
        "year": (title.get("releaseYear") or {}).get("year"),
        "type": (title.get("titleType") or {}).get("id"),
        "votes": votes,
        "items": items,
    }


def total_votes(votes: Optional[dict]) -> int:
    return sum((votes or {}).values())
