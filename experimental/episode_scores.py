"""Experiment "episode_scores": a score for each episode that has its own IMDb parents guide.

IMDb returns a whole season's episodes, with their guide items, in one request. Most episodes have none; those
that do get scored (AI when available, keyword estimate meanwhile) and shown in the episode list and in that
episode's source row. Episodes without a guide keep the show-wide score.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.request
from typing import Dict, Optional, Tuple

from pg import classify as base_classify
from pg import imdb
from pg import rubric as base_rubric

from . import enabled, store
from .iscci import classify as iscci_classify
from .iscci import rubric as iscci_rubric
from .iscci.service import items_from

NAME = "episode_scores"
NS_GUIDE = "ep_guide"
NS_SCORE = "ep_score"
REFRESH_AFTER = 7 * 24 * 3600
log = logging.getLogger("experimental.episodes")

SEASONS = "query S($id: ID!){ title(id:$id){ episodes { displayableSeasons(first: 100) { edges { node { season } } } } } }"
EPISODES = """query E($id: ID!, $season: String!){ title(id:$id){ episodes { episodes(first: 250, filter:{includeSeasons:[$season]}) {
 edges { node { id series { episodeNumber { seasonNumber episodeNumber } }
 parentsGuide { guideItems(first: 100, filter:{categories:["NUDITY"]}) { edges { node { id isSpoiler text { plainText } } } } } } } } } } }"""


def is_on() -> bool:
    return enabled(NAME)


def _graphql(query: str, variables: dict) -> dict:
    req = urllib.request.Request(imdb.ENDPOINT, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Content-Type": "application/json", "x-imdb-client-name": "imdb-web-next-localized",
                                          "User-Agent": "Mozilla/5.0"})
    time.sleep(imdb.MIN_INTERVAL)  # be gentle with IMDb: one request at a time, a second apart
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    if not data.get("data"):
        raise RuntimeError(f"IMDb: {data.get('errors')}")
    return data["data"]


def _fetch(show_id: str) -> dict:
    seasons = [e["node"]["season"] for e in
               (_graphql(SEASONS, {"id": show_id})["title"]["episodes"]["displayableSeasons"]["edges"])]
    episodes = {}
    for season in seasons:
        if not season.isdigit():
            continue
        data = _graphql(EPISODES, {"id": show_id, "season": season})
        for edge in data["title"]["episodes"]["episodes"]["edges"]:
            node = edge["node"]
            number = (node.get("series") or {}).get("episodeNumber") or {}
            items = [{"id": e["node"]["id"], "text": e["node"]["text"]["plainText"], "is_spoiler": e["node"]["isSpoiler"]}
                     for e in ((node.get("parentsGuide") or {}).get("guideItems") or {}).get("edges", [])]
            if items and number.get("seasonNumber") is not None:
                episodes[f"{number['seasonNumber']}:{number['episodeNumber']}"] = {"id": node["id"], "items": items}
    return {"fetched_at": time.time(), "episodes": episodes}


def _hash(items: list) -> str:
    return hashlib.sha1(json.dumps([i["text"] for i in items]).encode()).hexdigest()[:12]


class Episodes:
    def __init__(self):
        self._jobs: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def guides(self, show_id: str, rubrics: Tuple[str, ...]) -> Optional[dict]:
        """The show's episode guides ({"S:E": {id, items}}), starting a background fetch and scoring if needed."""
        rec = store.kv().get(NS_GUIDE, show_id)
        if rec is None or time.time() - rec["fetched_at"] > REFRESH_AFTER or self._unscored(rec, rubrics):
            self._start(show_id, rubrics)
        return rec

    def is_working(self, show_id: str) -> bool:
        with self._lock:
            return show_id in self._jobs

    def _unscored(self, rec: dict, rubrics) -> bool:
        if not base_classify.ai_enabled():
            return False
        return any(self._saved(ep, r) is None for ep in rec["episodes"].values() for r in rubrics)

    def _saved(self, ep: dict, rubric_name: str) -> Optional[dict]:
        saved = store.kv().get(NS_SCORE, f"{rubric_name}:{ep['id']}")
        return saved if saved and saved.get("hash") == _hash(ep["items"]) else None

    def _start(self, show_id: str, rubrics) -> None:
        with self._lock:
            if show_id in self._jobs:
                return
            self._jobs[show_id] = threading.Event()
        threading.Thread(target=self._run, args=(show_id, rubrics), daemon=True).start()

    def _run(self, show_id: str, rubrics) -> None:
        try:
            rec = store.kv().get(NS_GUIDE, show_id)
            if rec is None or time.time() - rec["fetched_at"] > REFRESH_AFTER:
                rec = _fetch(show_id)
                store.kv().put(NS_GUIDE, show_id, rec)
                log.info("Episode guides for %s: %d episodes with items", show_id, len(rec["episodes"]))
            if not base_classify.ai_enabled():
                return
            for ep in rec["episodes"].values():
                for r in rubrics:
                    if self._saved(ep, r) is not None:
                        continue
                    texts = [i["text"] for i in ep["items"]]
                    if r == "iscci":
                        classified, classifier, summary = iscci_classify.classify(texts)
                    else:
                        classified, classifier, summary = base_classify.classify(texts)
                    if classifier == "keywords":
                        return  # AI paused or over its daily limit; try again on a later view
                    store.kv().put(NS_SCORE, f"{r}:{ep['id']}", {"hash": _hash(ep["items"]), "classified": classified,
                                                                 "classifier": classifier, "summary": summary})
        except Exception as e:  # an experiment must never take the server down
            log.warning("Episode scores for %s failed: %s", show_id, e)
        finally:
            with self._lock:
                self._jobs.pop(show_id, None)

    def result(self, rec: Optional[dict], season, episode, rubric_name: str):
        """(result, entry) for one episode under "v5.1" or "iscci", or None if it has no guide of its own."""
        ep = (rec or {}).get("episodes", {}).get(f"{season}:{episode}")
        if not ep:
            return None
        saved = self._saved(ep, rubric_name)
        texts = [i["text"] for i in ep["items"]]
        guide = {"items": ep["items"]}
        if rubric_name == "iscci":
            classified = saved["classified"] if saved else iscci_classify.keywords(texts)
            entry = {"guide": guide, "classified": classified, "classifier": saved["classifier"] if saved else "keywords",
                     "summary": saved.get("summary") if saved else None}
            items = items_from(ep["items"], classified)
            return iscci_rubric.summarize(items, 0, base_rubric.WARNINGS), entry
        classified = saved["classified"] if saved else base_classify.keywords(texts)
        entry = {"guide": guide, "classified": classified, "classifier": saved["classifier"] if saved else "keywords",
                 "summary": saved.get("summary") if saved else None}
        items = [base_rubric.ScoredItem(text=g["text"], is_spoiler=g["is_spoiler"], level=c["level"], is_scene=c["is_scene"],
                                        prolonged=c["prolonged"], warning=c.get("warning"))
                 for g, c in zip(ep["items"], classified)]
        return base_rubric.summarize(items, 0), entry


episodes = Episodes()
