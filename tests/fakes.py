"""A recording transport that answers koro RPCs with canned data."""
from __future__ import annotations

E = "d48ee9db-7099-48ec-a59e-3108bcd9434a"
L = "fea6d85b-2166-458c-ba61-6bed113c1819"

CARD = {"id": "c1", "key": "0/inference-stack-layers", "name": "The three layers of an inference platform",
        "summary": "Runtime, infrastructure and tooling.", "modality": "read", "node_key": "0",
        "node_title": "Chapter 0: Inference", "page_start": 17, "page_end": 24, "status": "untouched", "mastery": 0}
ROLLUP = {"concepts": 210, "untouched": 209, "seen": 1, "learning": 0, "mastered": 0, "mastery": 0, "status": "seen"}
STATUS = {"entity": {"id": E, "title": "Inference Engineering", "status": "ready"}, "scope": "all",
          "enrollment": {"status": "active", "current_node": None, "knowledge_summary": None,
                         "target_date": None, "daily_minutes": None},
          "rollup": ROLLUP, "gaps": [{"concept_key": "1.1/x", "concept": "X", "node_key": "1.1",
                                      "objective": "Explain X", "misconception": "thinks Y", "mastery": 0.4}],
          "strengths": [], "fading": [], "this_week": [{"minutes": 12.5, "sittings": 2}]}

RESPONSES = {
    "koro_learners": [{"learner_id": L, "display_name": "Ninad"}],
    "koro_ensure_learner": {"learner_id": L, "display_name": "Ninad"},
    "koro_library": [{"entity_id": E, "title": "Inference Engineering", "author": "Philip Kiely",
                      "source_type": "book", "ingest_status": "ready", "enrollment": "active",
                      "last_seen_at": None, "progress": ROLLUP}],
    "koro_resolve_entity": E,
    "koro_continue": {"study_session_id": "s1", "status": STATUS, "next": {"reason": "next", "scope": "all", "concept": CARD}},
    "koro_start": {"study_session_id": "s1", "status": STATUS, "next": {"reason": "next", "scope": "all", "concept": CARD}},
    "koro_next_concept": {"reason": "prerequisite", "scope": "5.3.2", "concept": CARD},
    "koro_teach": {"concept": {"id": "c1", "key": CARD["key"], "name": CARD["name"], "summary": "s", "modality": "read",
                               "teaching_content": {"hook": "Why?", "beats": [{"title": "B1", "explain": "Explain.",
                                                                              "diagram": "a -> b"}],
                                                    "checks": [{"objective": "o1", "question": "Q?", "answer_guide": "A"}],
                                                    "takeaway": "T"}},
                   "entity": {"id": E, "title": "Inference Engineering"},
                   "node": {"key": "0", "title": "Chapter 0: Inference", "page_start": 17, "page_end": 24},
                   "progress": {"status": "seen"}, "objectives": [{"key": "o1", "description": "Explain", "status": "unseen"}],
                   "prerequisites": [], "recent_evidence": []},
    "koro_assess": {"assessment_id": "a1", "concept_key": CARD["key"], "status": "learning", "mastery": 0.5,
                    "stability_days": 1.2, "objectives": [{"key": "o1", "description": "Explain", "status": "gap",
                                                           "mastery": 0.5, "misconception": None}]},
    "koro_dispute": {"dispute_id": "d1", "concept_key": CARD["key"], "status": "mastered", "mastery": 1,
                     "stability_days": 3, "objectives": []},
    "koro_mark_known": {"marked_known": 3, "ref": "2.1"},
    "koro_toc": {"entity": {"id": E, "title": "Inference Engineering", "author": "Philip Kiely", "status": "ready"},
                 "current_node": None,
                 "nodes": [{"key": "0", "title": "Chapter 0: Inference", "kind": "chapter", "depth": 0, "page_start": 17,
                            "page_end": 24, "teachable": True, "ingest_status": "parsed", "progress": ROLLUP},
                           {"key": "preface", "title": "Preface", "kind": "chapter", "depth": 0, "page_start": 11,
                            "page_end": 16, "teachable": False, "ingest_status": "parsed", "progress": None}]},
    "koro_status": STATUS,
    "koro_search": {"query": "kv cache", "nodes": [{"key": "5.3", "title": "5.3 Caching", "page_start": 138}],
                    "concepts": [CARD]},
    "koro_set_status": {"entity_id": E, "status": "paused"},
    "koro_restart": {"entity_id": E, "concepts_reset": 4},
    "koro_set_goal": {"entity_id": E, "target_date": "2026-12-01", "daily_minutes": 20},
    "koro_set_summary": {"entity_id": E, "knowledge_summary": "x"},
    "koro_study_time": [{"entity_id": E, "title": "Inference Engineering", "minutes": 30, "sittings": 3,
                         "concepts_touched": 5, "goal_minutes_in_range": 140, "days_left": 50,
                         "concepts_remaining": 200}],
    "koro_create_entity": {"entity_id": E, "existed": False, "title": "Inference Engineering", "status": "ingesting"},
    "koro_build_outline": {"nodes": 3, "inserted": 3, "updated": 0},
    "koro_claim_next_node": {"claimed": True, "node": {"key": "1.1", "title": "1.1 Scale", "page_start": 28,
                                                       "page_end": 29}},
    "koro_save_node": {"node_key": "1.1", "concepts": 1, "objectives": 1, "edge_refs": 0, "remaining": 3},
    "koro_release_node": {"node_key": "1.1", "status": "pending"},
    "koro_add_edge_refs": {"added": 2, "skipped": []},
    "koro_finalize_ingest": {"status": "ready"},
}


class Recorder:
    def __init__(self, overrides=None):
        self.calls = []
        self.responses = dict(RESPONSES, **(overrides or {}))

    def __call__(self, function, body):
        self.calls.append((function, body))
        if function not in self.responses:
            raise AssertionError(f"unexpected rpc {function}")
        return self.responses[function]

    def last(self, function):
        for fn, body in reversed(self.calls):
            if fn == function:
                return body
        raise AssertionError(f"{function} was not called; calls: {[c[0] for c in self.calls]}")
