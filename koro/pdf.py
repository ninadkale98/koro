"""Turn a PDF into an outline of nodes, and find each node's own text.

Pure functions (``build_nodes``, ``segment``) do the work so they can be tested
without a PDF; ``read_pdf`` wraps pypdf.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

NON_TEACHABLE = re.compile(
    r"^(table of )?contents$|preface|foreword|acknowledg|glossary|^index$|bibliograph|references|"
    r"recommended reading|further reading|about the author|copyright|dedication|colophon", re.I)


@dataclass
class RawItem:
    depth: int
    title: str
    page: int  # 1-based


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_title(title: str) -> str:
    title = re.sub(r"(\w)- (\w)", r"\1-\2", title)
    return re.sub(r"\s+", " ", title).strip()


def _key_for(title: str, parent_key: Optional[str], used: set) -> str:
    m = re.match(r"^(\d+(?:\.\d+)*)\.?\s", title + " ")
    m_ch = re.match(r"^(?:chapter|part)\s+(\d+)\b", title, re.I)
    m_app = re.match(r"^appendix\s+([A-Z])\b", title, re.I)
    if m and "." in m.group(1) or (m and parent_key is None):
        key = m.group(1)
    elif m_ch:
        key = m_ch.group(1)
    elif m_app:
        key = "app" + m_app.group(1).upper()
    elif title.lower() == "preface":
        key = "preface"
    elif title.lower().startswith("acknowledg"):
        key = "ack"
    else:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "node"
        key = f"{parent_key}.{slug}" if parent_key else slug
    base, n = key, 2
    while key in used:
        key = f"{base}-{n}"
        n += 1
    used.add(key)
    return key


def build_nodes(raw: list[RawItem], page_count: int) -> list[dict]:
    """Outline items (depth, title, page) in reading order -> node dicts for koro_build_outline."""
    raw = [RawItem(r.depth, _clean_title(r.title), r.page) for r in raw if r.title.strip()]
    # A single wrapper entry ("Table of Contents") that holds everything is lifted away.
    tops = [r for r in raw if r.depth == 0]
    if len(tops) == 1 and len(raw) > 1 and re.match(r"^(table of )?contents$", tops[0].title, re.I):
        raw = [RawItem(r.depth - 1, r.title, r.page) for r in raw if r is not tops[0]]
    if not raw:
        return chunk_nodes(page_count)

    nodes: list[dict] = []
    stack: list[tuple[int, str, bool]] = []  # (depth, key, teachable)
    order: dict = {}
    used: set = set()
    for r in raw:
        while stack and stack[-1][0] >= r.depth:
            stack.pop()
        parent_key = stack[-1][1] if stack else None
        parent_teachable = stack[-1][2] if stack else True
        key = _key_for(r.title, parent_key, used)
        order[parent_key] = order.get(parent_key, 0) + 1
        if r.depth == 0:
            kind = "appendix" if r.title.lower().startswith("appendix") else "chapter"
        else:
            kind = "section" if r.depth == 1 else "subsection"
        teachable = parent_teachable and not NON_TEACHABLE.search(r.title)
        nodes.append({"key": key, "parent_key": parent_key, "kind": kind, "title": r.title,
                      "order_index": order[parent_key], "page_start": r.page, "depth": r.depth,
                      "teachable": teachable})
        stack.append((r.depth, key, teachable))

    # page_end: through the page where the next node starts (a section usually
    # ends partway down that page), then widened to cover the node's subtree.
    for i, n in enumerate(nodes):
        nxt = nodes[i + 1]["page_start"] if i + 1 < len(nodes) else page_count
        n["page_end"] = max(n["page_start"], nxt)
    for i, n in enumerate(nodes):
        j = i + 1
        while j < len(nodes) and nodes[j]["depth"] > n["depth"]:
            j += 1
        n["page_end"] = max(n["page_end"], nodes[j - 1]["page_end"])
    return nodes


def chunk_nodes(page_count: int, size: int = 10) -> list[dict]:
    """Fallback outline for PDFs without bookmarks: one section per `size` pages."""
    nodes = []
    for i, start in enumerate(range(1, page_count + 1, size), 1):
        end = min(page_count, start + size - 1)
        nodes.append({"key": f"p{start:03d}", "parent_key": None, "kind": "section",
                      "title": f"Pages {start}-{end}", "order_index": i, "page_start": start,
                      "page_end": end, "depth": 0, "teachable": True})
    return nodes


_HEADER_EVEN = re.compile(r"^\s*\d+\s+(Chapter \d+:|Appendix [A-Z]:|Preface|Acknowledgements)")
_HEADER_ODD = re.compile(r"^\s*.+\s\d+\s*$")


def clean_page(text: str) -> str:
    """Strip a running header line and common ligature artifacts."""
    lines = text.splitlines()
    if lines and (_HEADER_EVEN.match(lines[0]) or (_HEADER_ODD.match(lines[0]) and len(lines[0]) < 90)):
        lines = lines[1:]
    return re.sub(r"(f|fi|fl|ff)\s{2}(?=[a-z])", r"\1", "\n".join(lines))


def _locate(pages: list[str], nodes: list[dict]) -> tuple[str, list[int], list[int]]:
    """(full cleaned text, start offset of each page, start offset of each node's heading)."""
    texts = [clean_page(p or "") for p in pages]
    page_at, full_parts, pos_acc = [], [], 0
    for t in texts:
        page_at.append(pos_acc)
        full_parts.append(t + "\n")
        pos_acc += len(t) + 1
    full = "".join(full_parts)
    offsets = []
    pos = 0
    for n in nodes:
        page_idx = max(0, min(len(page_at) - 1, (n.get("page_start") or 1) - 1))
        page_off = page_at[page_idx] if page_at else 0
        start = max(pos, page_off - 50)
        m = None
        num = re.match(r"^(\d+(?:\.\d+)+)\s+(.*)$", n["title"])
        if num:
            flex = r"\s*\.\s*".join(re.escape(p) for p in num.group(1).split("."))
            m = re.compile(r"(?m)^\s*" + flex + r"\s+\S").search(full, start)
            limit = page_at[min(page_idx + 1, len(page_at) - 1)] + 4000 if page_at else 0
            if m is None or m.start() > limit:
                words = r"\W+".join(re.escape(w) for w in re.findall(r"\w+", num.group(2))[:4])
                m2 = re.compile(r"(?mi)^\s*" + words).search(full, start) if words else None
                if m2 and (m is None or m2.start() < m.start()):
                    m = m2
        off = m.start() if m else max(page_off, pos)
        offsets.append(off)
        pos = off
    return full, page_at, offsets


def segment(pages: list[str], nodes: list[dict]) -> dict:
    """Each node's own text: from its heading to the next node's heading."""
    full, _, offsets = _locate(pages, nodes)
    out = {}
    for i, n in enumerate(nodes):
        end = offsets[i + 1] if i + 1 < len(nodes) else len(full)
        out[n["key"]] = full[offsets[i]:end].strip()
    return out


def refine_page_ends(pages: list[str], nodes: list[dict]) -> list[dict]:
    """Set page_end from where each node's text actually stops, then widen to the subtree."""
    if not pages or not nodes:
        return nodes
    full, page_at, offsets = _locate(pages, nodes)

    def page_of(offset: int) -> int:
        lo = 0
        for i, start in enumerate(page_at):
            if start <= offset:
                lo = i
            else:
                break
        return lo + 1

    for i, n in enumerate(nodes):
        end = offsets[i + 1] if i + 1 < len(nodes) else len(full)
        n["page_end"] = max(n["page_start"], page_of(max(offsets[i], end - 1)))
    for i, n in enumerate(nodes):
        j = i + 1
        while j < len(nodes) and nodes[j]["depth"] > n["depth"]:
            j += 1
        n["page_end"] = max(n["page_end"], nodes[j - 1]["page_end"])
    return nodes


def read_pdf(path: str) -> tuple[list[RawItem], list[str], dict]:
    """(outline items, page texts, metadata) from a PDF."""
    from pypdf import PdfReader

    r = PdfReader(path)
    raw: list[RawItem] = []

    def walk(items, depth=0):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
            else:
                try:
                    page = r.get_destination_page_number(it) + 1
                except Exception:
                    continue
                raw.append(RawItem(depth, str(it.title), page))

    try:
        walk(r.outline)
    except Exception:
        raw = []
    pages = [(p.extract_text() or "") for p in r.pages]
    meta = {}
    if r.metadata:
        meta = {"title": r.metadata.get("/Title"), "author": r.metadata.get("/Author")}
    return raw, pages, meta


def canonical_id(pages: list[str], path: str) -> Optional[str]:
    head = "\n".join(pages[:12])
    m = re.search(r"ISBN(?:-1[03])?:?\s*([0-9][0-9\- ]{8,16}[0-9Xx])", head)
    if m:
        return "isbn:" + re.sub(r"[^0-9Xx]", "", m.group(1)).upper()
    m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", head)
    if m:
        return "arxiv:" + m.group(1)
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?\.pdf$", os.path.basename(path))
    if m:
        return "arxiv:" + m.group(1)
    m = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+)", head)
    if m:
        return "doi:" + m.group(1).rstrip(".,;").lower()
    return None


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "koro"


def node_texts(path: str, nodes: list[dict]) -> dict:
    """Segmented node texts for a PDF, cached by file hash so repeated ingest calls are fast."""
    digest = sha256_file(path)
    cache = cache_dir() / f"{digest}.json"
    keys = [n["key"] for n in nodes]
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("keys") == keys:
                return data["texts"]
        except (OSError, json.JSONDecodeError):
            pass
    _, pages, _ = read_pdf(path)
    texts = segment(pages, nodes)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"keys": keys, "texts": texts}), encoding="utf-8")
    except OSError:
        pass
    return texts
