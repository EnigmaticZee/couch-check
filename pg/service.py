"""Looks up a title's result: serves from cache, fetches and classifies in the background when needed."""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
from typing import Dict, List, Optional, Tuple

from . import classify, imdb, rubric, scores
from .store import Store, is_stale

log = logging.getLogger("pg.service")


class Service:
    def __init__(self, store: Store):
        self.store = store
        self._jobs: Dict[str, threading.Event] = {}
        self._jobs_lock = threading.Lock()
        self.errors: Dict[str, str] = {}  # last fetch error per title, cleared on success

    def lookup(self, imdb_id: str, wait: float, routes: Optional[List[classify.Route]] = None) -> Optional[dict]:
        """The title's entry, waiting up to `wait` seconds for scoring. None only if IMDb hasn't answered yet.

        A first view gets the AI score when the AI answers within `wait`, otherwise the keyword
        estimate (saved as soon as IMDb's data arrives) while the AI finishes in the background.
        routes: the viewer's own AI key (public copies); the server's keys when None. Scores are shared: once any
        key has scored a title, every viewer sees that AI score.
        """
        routes = classify.server_routes() if routes is None else routes
        entry = self.store.get(imdb_id)
        if entry is not None:
            if self._needs_refresh(entry, imdb_id, routes):
                done = self._start(imdb_id, routes)
                if not classify.is_ai(entry["classifier"]):
                    done.wait(wait)  # an estimate may become an AI score within this view
                    entry = self.store.get(imdb_id) or entry
            return with_saved_scores(imdb_id, entry)
        self._start(imdb_id, routes).wait(wait)
        entry = self.store.get(imdb_id)
        return with_saved_scores(imdb_id, entry) if entry else None

    def lookup_estimate(self, imdb_id: str, wait: float) -> Optional[dict]:
        """The keyword estimate only, for viewers who turned AI scores off. Never waits for the AI."""
        entry = self.store.get(imdb_id)
        if entry is None:
            self._start(imdb_id, classify.server_routes())  # as before: the server may still score it for others
            deadline = time.monotonic() + wait
            while entry is None and time.monotonic() < deadline:
                time.sleep(0.1)  # the estimate is saved as soon as IMDb's data arrives
                entry = self.store.get(imdb_id)
            if entry is None:
                return None
        if entry["guide"].get("items"):
            entry = {**entry, "classifier": "keywords", "summary": None}  # score() recomputes keyword levels
        return entry

    def is_working(self, imdb_id: str) -> bool:
        with self._jobs_lock:
            return imdb_id in self._jobs

    def _needs_refresh(self, entry: dict, imdb_id: str, routes: List[classify.Route]) -> bool:
        if is_stale(entry):
            return True
        if not entry["guide"].get("items"):
            return False
        if imdb_id in scores.titles():
            return False  # has a saved score; nothing for the server's AI to do
        if entry["classifier"] == "keywords":
            return bool(routes)
        return not (entry["classifier"] or "").endswith(f"@{rubric.VERSION}")

    def _start(self, imdb_id: str, routes: List[classify.Route]) -> threading.Event:
        with self._jobs_lock:
            if imdb_id in self._jobs:
                return self._jobs[imdb_id]
            done = threading.Event()
            self._jobs[imdb_id] = done
        threading.Thread(target=self._run, args=(imdb_id, done, routes), daemon=True).start()
        return done

    def _run(self, imdb_id: str, done: threading.Event, routes: List[classify.Route]) -> None:
        try:
            cached = self.store.get(imdb_id)
            if cached and not is_stale(cached):
                # Only the scoring needs redoing (e.g. AI now available); IMDb's data is still fresh.
                guide, fetched_at = cached["guide"], cached["fetched_at"]
            else:
                try:
                    guide = imdb.fetch(imdb_id)
                except imdb.NotFound:
                    guide = {"missing": True}
                fetched_at = time.time()
                if cached is None or not classify.is_ai(cached["classifier"]):
                    # Save the instant keyword estimate first, so a view never has to wait for the AI.
                    texts = [i["text"] for i in guide.get("items", [])]
                    self.store.put(imdb_id, guide, classify.keywords(texts), "keywords" if texts else "none", fetched_at)
            self.errors.pop(imdb_id, None)

            texts = [i["text"] for i in guide.get("items", [])]
            if not texts or not routes or imdb_id in scores.titles():
                return
            classified, classifier, summary = classify.classify(texts, classify.context(guide), routes)
            if classifier == "keywords" and cached and classify.is_ai(cached["classifier"]):
                return  # AI unavailable right now; keep the existing AI score rather than downgrading it
            self.store.put(imdb_id, guide, classified, classifier, fetched_at, summary)
            log.info("Scored %s (%d items, %s)", imdb_id, len(texts), classifier)
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            self.errors[imdb_id] = str(e)
            log.warning("Could not score %s: %s", imdb_id, e)
        finally:
            with self._jobs_lock:
                self._jobs.pop(imdb_id, None)
            done.set()


def with_saved_scores(imdb_id: str, entry: dict) -> dict:
    """If data/scores.json has AI scores for this title, use them in place of the cached classification."""
    items = entry["guide"].get("items") or []
    saved = scores.lookup(imdb_id, items, classify.keywords_one)
    if saved is None:
        return entry
    summary = scores.titles().get(imdb_id, {}).get("summary")
    return {**entry, "classified": saved, "classifier": f"ai:saved@{rubric.VERSION}", "summary": summary}


def score(entry: dict) -> Tuple[rubric.Result, list]:
    """Rolls an entry's classified items up into a Result. Returns (result, scored items)."""
    guide = entry["guide"]
    votes = imdb.total_votes(guide.get("votes"))
    classified = entry["classified"] or []
    if entry["classifier"] == "keywords":
        # Keyword matching is instant, so always use the current rules rather than what was cached.
        classified = classify.keywords([i["text"] for i in guide.get("items", [])])
    items = [
        rubric.ScoredItem(
            text=item["text"],
            is_spoiler=item["is_spoiler"],
            level=c["level"],
            is_scene=c["is_scene"],
            prolonged=c["prolonged"],
            warning=c.get("warning"),
            nonsexual=c.get("nonsexual"),
            shown=c.get("shown") or "",
        )
        for item, c in zip(guide.get("items", []), classified)
    ]
    return rubric.summarize(items, votes), items
