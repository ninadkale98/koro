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
    "koro_create_connector_token": {"token_id": "t1", "token": "koro_" + "c" * 48, "label": "claude.ai"},
    "koro_list_connector_tokens": [{"token_id": "t1", "label": "claude.ai", "created_at": "2026-10-12T17:00:00Z",
                                    "last_used_at": None, "revoked_at": None}],
    "koro_revoke_connector_token": {"token_id": "t1", "revoked": True},
}


FIGURE = {"asset_id": "f1", "label": "2.13", "caption": "A roofline chart.", "page": 65, "bbox": [100, 67, 397, 298],
          "bucket": "koro-private", "storage_path": f"{E}/figures/fig-2.13.png", "uploaded": True,
          "width": 800, "height": 600, "concept_key": "2.4.1/arithmetic-intensity-roofline", "node_key": "2.4.1"}

RESPONSES.update({
    "koro_figure": dict(FIGURE, entity={"id": E, "title": "Inference Engineering"}),
    "koro_figures": [FIGURE],
    "koro_read_section": {"section": {"key": "2.4.1", "title": "2.4.1 Roofline", "page_start": 64, "page_end": 66},
                          "text": "Stored words.", "subsections": [], "figures": [FIGURE], "concepts": []},
    "koro_asset_status": {"entity": {"id": E, "title": "Inference Engineering", "source_hash": None},
                          "source": None, "sections": {"total": 2, "with_text": 0, "chars": 0},
                          "figures": [], "figures_uploaded": 0},
    "koro_register_source": {"asset_id": "s1"},
    "koro_save_node_sources": {"saved": 2, "unknown_keys": [], "sections_with_text": 2},
    "koro_register_figures": {"registered": 1, "pending_upload": 1},
    "koro_mark_uploaded": {"asset_id": "f1", "uploaded": True},
})


class Recorder:
    """Canned answers per function; a callable answer gets (body, call number) and returns the answer."""

    def __init__(self, overrides=None):
        self.calls = []
        self.responses = dict(RESPONSES, **(overrides or {}))

    def __call__(self, function, body):
        self.calls.append((function, body))
        if function not in self.responses:
            raise AssertionError(f"unexpected rpc {function}")
        r = self.responses[function]
        if callable(r):
            return r(body, sum(1 for c in self.calls if c[0] == function))
        return r

    def names(self):
        return [c[0] for c in self.calls]

    def last(self, function):
        for fn, body in reversed(self.calls):
            if fn == function:
                return body
        raise AssertionError(f"{function} was not called; calls: {[c[0] for c in self.calls]}")


PNG = b"\x89PNG\r\n\x1a\n-fake-image"


class FakeStorage:
    """Records Storage API calls: uploads, downloads, signed URLs."""

    def __init__(self, fail_download=False):
        self.calls = []
        self.fail_download = fail_download

    def __call__(self, method, path, body, headers):
        import json

        from koro.client import KoroError

        self.calls.append((method, path, body, headers))
        if path.startswith("object/sign/"):
            return json.dumps({"signedURL": "/" + path + "?token=tok"}).encode()
        if method == "GET":
            if self.fail_download:
                raise KoroError("storage GET: Object not found (HTTP 400)")
            return PNG
        return json.dumps({"Key": path}).encode()

    def uploads(self):
        return [(p, h.get("Content-Type"), len(b or b"")) for m, p, b, h in self.calls if m == "POST" and not p.startswith("object/sign/")]


def make_pdf(path, pages):
    """A tiny valid PDF. pages: [(content stream, media box)]; Helvetica is /F1."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages))),
                                                         len(pages))).encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, (content, box) in enumerate(pages):
        data = content.encode("latin-1")
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [%s] /Resources << /Font << /F1 3 0 R >> >> "
                     "/Contents %d 0 R >>" % (" ".join(str(v) for v in box), 5 + 2 * i)).encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return path


# Page 1: a heading and text. Page 2 (page box shifted to (28, 28), like real books): a blue
# box with its caption underneath, i.e. "Figure 1.1".
BOOK_PAGES = [
    ("BT /F1 14 Tf 50 550 Td (1 Intro) Tj ET BT /F1 11 Tf 50 520 Td (Some words about the main idea here.) Tj ET"
     + "".join(f" BT /F1 11 Tf 50 {500 - 16 * i} Td (Line {i}: serving models well takes care and measurement.) Tj ET"
               for i in range(5)),
     [0, 0, 400, 600]),
    ("BT /F1 11 Tf 78 590 Td (More words before the picture.) Tj ET 0 0 1 rg 128 328 200 150 re f "
     "BT /F1 10 Tf 128 308 Td (Figure 1.1: A blue box shows the idea.) Tj ET",
     [28, 28, 428, 628]),
]
BOOK_TOC = {"entity": {"id": E, "title": "Tiny Book"}, "current_node": None,
            "nodes": [{"key": "1", "title": "1 Intro", "depth": 0, "page_start": 1, "page_end": 2,
                       "teachable": True, "ingest_status": "parsed", "progress": None}]}
