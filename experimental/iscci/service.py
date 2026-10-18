"""Looks up a title's ISCCI scores: IMDb data comes from production's cache, ISCCI scores from experimental.sqlite3.

A first view gets the keyword estimate at once; the AI scores in the background and later views say "AI".
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
from typing import Dict, Optional, Tuple

from pg import classify as base
from pg import imdb
from pg import rubric as base_rubric
from pg.store import is_stale

from .. import store
from . import classify, rubric

log = logging.getLogger("experimental.iscci")
NS = "iscci"


class IscciService:
    def __init__(self, service):
        self.service = service  # production's pg.service.Service, for its IMDb cache
        self._jobs: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def lookup(self, imdb_id: str, wait: float, use_ai: bool = True) -> Optional[dict]:
        """{guide, fetched_at, classified, classifier, summary}, or None while IMDb hasn't answered."""
        entry = self.service.store.get(imdb_id)
        if entry is None or is_stale(entry):
            done = self._start(imdb_id)
            if entry is None:
                done.wait(wait)
                entry = self.service.store.get(imdb_id)
                if entry is None:
                    return None
        guide = entry["guide"]
        texts = [i["text"] for i in guide.get("items") or []]
        estimate = {"guide": guide, "fetched_at": entry["fetched_at"], "classified": classify.keywords(texts),
                    "classifier": "keywords" if texts else "none", "summary": None}
        if not texts or not use_ai or not base.ai_enabled():
            return estimate
        saved = self._saved(imdb_id, entry)
        if saved is None:
            self._start(imdb_id).wait(wait)
            saved = self._saved(imdb_id, self.service.store.get(imdb_id) or entry)
        return saved or estimate

    def is_working(self, imdb_id: str) -> bool:
        with self._lock:
            return imdb_id in self._jobs

    def _saved(self, imdb_id: str, entry: dict) -> Optional[dict]:
        rec = store.kv().get(NS, imdb_id)
        if not rec or rec.get("fetched_at") != entry["fetched_at"] or not rec.get("classifier", "").endswith(rubric.TAG):
            return None
        return {"guide": entry["guide"], "fetched_at": entry["fetched_at"], "classified": rec["classified"],
                "classifier": rec["classifier"], "summary": rec.get("summary")}

    def _start(self, imdb_id: str) -> threading.Event:
        with self._lock:
            if imdb_id in self._jobs:
                return self._jobs[imdb_id]
            done = threading.Event()
            self._jobs[imdb_id] = done
        threading.Thread(target=self._run, args=(imdb_id, done), daemon=True).start()
        return done

    def _run(self, imdb_id: str, done: threading.Event) -> None:
        try:
            entry = self.service.store.get(imdb_id)
            if entry is None or is_stale(entry):
                # Fetch IMDb's data into production's cache, the way production does, without production's AI.
                try:
                    guide = imdb.fetch(imdb_id)
                except imdb.NotFound:
                    guide = {"missing": True}
                texts = [i["text"] for i in guide.get("items", [])]
                self.service.store.put(imdb_id, guide, base.keywords(texts), "keywords" if texts else "none", time.time())
                entry = self.service.store.get(imdb_id)
            texts = [i["text"] for i in entry["guide"].get("items") or []]
            if not texts or not base.ai_enabled() or self._saved(imdb_id, entry):
                return
            classified, classifier, summary = classify.classify(texts)
            if classifier == "keywords":
                return  # AI unavailable right now; the estimate is computed on each view
            store.kv().put(NS, imdb_id, {"fetched_at": entry["fetched_at"], "classified": classified,
                                         "classifier": classifier, "summary": summary})
            log.info("ISCCI scored %s (%d items)", imdb_id, len(texts))
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            log.warning("Could not ISCCI-score %s: %s", imdb_id, e)
        finally:
            with self._lock:
                self._jobs.pop(imdb_id, None)
            done.set()


def items_from(guide_items: list, classified: list) -> list:
    return [
        rubric.ScoredItem(text=g["text"], is_spoiler=g.get("is_spoiler", False), level=c["level"], tag=c["tag"],
                          is_scene=c["is_scene"], prolonged=c["prolonged"], momentary=c.get("momentary", False),
                          amount=c.get("amount", "once"), nonsexual=c.get("nonsexual"), warning=c.get("warning"))
        for g, c in zip(guide_items, classified)
    ]


def score(entry: dict) -> Tuple[rubric.Result, list]:
    guide = entry["guide"]
    items = items_from(guide.get("items") or [], entry["classified"] or [])
    return rubric.summarize(items, imdb.total_votes(guide.get("votes")), base_rubric.WARNINGS), items


def is_ai(entry: dict) -> bool:
    return base.is_ai(entry.get("classifier"))
