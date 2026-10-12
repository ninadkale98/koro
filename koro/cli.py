"""koro: learn books, papers and repos from any terminal.

Every command maps to one database function. Add --json to any command for
machine-readable output (that is what coding agents should use).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__, config, render
from .client import Client, KoroError

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
DATA = Path(__file__).parent / "data"


class Ctx:
    def __init__(self, args: argparse.Namespace, client: Optional[Client] = None):
        self.args = args
        self.cfg = config.load()
        self._client = client

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = Client(self.cfg.supabase_url, self.cfg.supabase_key)
        return self._client

    @property
    def learner(self) -> str:
        if not self.cfg.learner_id:
            raise KoroError("no learner selected: run `koro init`")
        return self.cfg.learner_id

    def rpc(self, fn: str, **params: Any) -> Any:
        return self.client.rpc(fn, **params)

    def entity(self, ref: Optional[str] = None, required: bool = True) -> Optional[str]:
        """Resolve an entity id from a uuid, a title fragment, or default to the most recent one."""
        ref = ref or getattr(self.args, "entity", None)
        if ref:
            return ref if UUID.match(ref) else self.rpc("koro_resolve_entity", ref=ref)
        lib = self.rpc("koro_library", learner=self.learner) or []
        active = [e for e in lib if e.get("enrollment") == "active"]
        pick = (active or lib or [None])[0]
        if pick is None:
            if required:
                raise KoroError("your library is empty: add a source with `koro add <file.pdf>`")
            return None
        return pick["entity_id"]


def emit(ctx: Ctx, value: Any, human=None) -> None:
    if ctx.args.json or human is None:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(human(value))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def cmd_init(ctx: Ctx) -> None:
    a, cfg = ctx.args, ctx.cfg
    interactive = sys.stdin.isatty() and not a.json
    url = a.url or cfg.supabase_url or (input("Supabase project URL: ").strip() if interactive else "")
    key = a.key or cfg.supabase_key or (getpass.getpass("Supabase secret key (hidden): ").strip() if interactive else "")
    if not url or not key:
        raise KoroError("need --url and --key (or KORO_SUPABASE_URL / KORO_SUPABASE_KEY)")
    cfg.supabase_url, cfg.supabase_key = url.rstrip("/"), key
    if a.device:
        cfg.device_id = a.device
    client = ctx._client or Client(cfg.supabase_url, cfg.supabase_key)
    learners = client.rpc("koro_learners") or []
    if a.name:
        learner = client.rpc("koro_ensure_learner", display_name=a.name)
    elif len(learners) == 1:
        learner = learners[0]
    elif not learners:
        name = input("Your name: ").strip() if interactive else ""
        if not name:
            raise KoroError("no learners yet: pass --name")
        learner = client.rpc("koro_ensure_learner", display_name=name)
    else:
        names = ", ".join(l["display_name"] for l in learners)
        raise KoroError(f"several learners exist ({names}): pass --name")
    cfg.learner_id, cfg.learner_name = learner["learner_id"], learner["display_name"]
    path = config.save(cfg)
    emit(ctx, {"config": str(path), "learner_id": cfg.learner_id, "learner": cfg.learner_name,
               "device": cfg.device_id},
         lambda r: f"Connected as {r['learner']} on {r['device']}. Config saved to {r['config']} (mode 600).")


def cmd_whoami(ctx: Ctx) -> None:
    cfg = ctx.cfg
    key = cfg.supabase_key
    masked = (key[:10] + "…" + key[-4:]) if len(key) > 16 else ("set" if key else "missing")
    emit(ctx, {"supabase_url": cfg.supabase_url, "key": masked, "learner_id": cfg.learner_id,
               "learner": cfg.learner_name, "device": cfg.device_id, "config": str(config.config_path()),
               "sources": cfg.sources},
         lambda r: "\n".join(f"{k}: {v}" for k, v in r.items()))


def cmd_skill(ctx: Ctx) -> None:
    """Install the /koro skill for Claude Code at user level (short `/koro` invocation)."""
    target = Path(ctx.args.dir or os.path.join(os.path.expanduser("~"), ".claude", "skills", "koro"))
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text((DATA / "koro-skill.md").read_text(encoding="utf-8"), encoding="utf-8")
    (target / "node-payload.md").write_text((DATA / "node-payload.md").read_text(encoding="utf-8"), encoding="utf-8")
    emit(ctx, {"installed": str(target)},
         lambda r: f"Installed the koro skill to {r['installed']}. In Claude Code, type /koro continue.")


# ---------------------------------------------------------------------------
# Library and ingest
# ---------------------------------------------------------------------------
def cmd_library(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_library", learner=ctx.learner), render.library)


def cmd_add(ctx: Ctx) -> None:
    from . import pdf

    a = ctx.args
    path = os.path.abspath(os.path.expanduser(a.path))
    if not os.path.exists(path):
        raise KoroError(f"no such file: {a.path}")
    if not path.lower().endswith(".pdf"):
        raise KoroError("koro add reads PDFs; for repos and other sources create the outline yourself "
                        "and upload it with `koro ingest outline` (see AGENTS.md)")
    raw, pages, meta = pdf.read_pdf(path)
    if sum(len(p.strip()) for p in pages) < 200 * max(1, len(pages) // 10):
        raise KoroError("this PDF has almost no extractable text (scanned?); koro needs real text")
    digest = pdf.sha256_file(path)
    canonical = a.canonical or pdf.canonical_id(pages, path)
    title = a.title or meta.get("title") or Path(path).stem.replace("_", " ")
    r = ctx.rpc("koro_create_entity", title=title, source_type=a.type, source_ref=os.path.basename(path),
                canonical_id=canonical, source_hash=digest, source_version=a.version,
                author=a.author or meta.get("author"), page_count=len(pages), learner=ctx.learner)
    entity = r["entity_id"]
    ctx.cfg.sources[entity] = path
    config.save(ctx.cfg)
    result = {"entity_id": entity, "title": r["title"], "existed": r["existed"], "canonical_id": canonical}
    if not r["existed"] or a.rebuild_outline:
        nodes = pdf.refine_page_ends(pages, pdf.build_nodes(raw, len(pages)))
        payload = [{k: n[k] for k in ("key", "parent_key", "kind", "title", "order_index",
                                      "page_start", "page_end", "teachable")} for n in nodes]
        result["outline"] = ctx.rpc("koro_build_outline", entity=entity, nodes=payload)
        result["nodes"] = len(nodes)
        result["teachable_nodes"] = sum(1 for n in nodes if n["teachable"])
    if not a.no_assets:
        # The complete book, privately: original file, section texts, figures. Re-running uploads only what's missing.
        try:
            result["assets"] = ingest_assets(ctx, entity, path)
        except KoroError as exc:
            result["assets_error"] = f"{exc} (the book is added; fix this and run `koro ingest assets`)"
    emit(ctx, result, lambda x: (
        f"{'Already in your library' if x['existed'] else 'Added'}: {x['title']} ({x['entity_id']})"
        + (f"\nOutline: {x['nodes']} sections, {x['teachable_nodes']} teachable." if "nodes" in x else "")
        + (f"\n{render.assets(x['assets'])}" if x.get("assets") else "")
        + (f"\nNot stored: {x['assets_error']}" if x.get("assets_error") else "")
        + ("\nNext: parse it with your coding agent (`/koro:ingest` in Claude Code), or `koro ingest next`."
           if "nodes" in x else "")))


def _toc_nodes(ctx: Ctx, entity: str) -> list:
    toc = ctx.rpc("koro_toc", learner=ctx.learner, entity=entity)
    return [{"key": n["key"], "title": n["title"], "page_start": n.get("page_start"), "depth": n.get("depth", 0)}
            for n in toc.get("nodes") or []]


def _source_text(ctx: Ctx, entity: str, node_key: str) -> Optional[str]:
    from . import pdf

    path = ctx.args.pdf if getattr(ctx.args, "pdf", None) else config.source_for(ctx.cfg, entity)
    if not path:
        return None
    return pdf.node_texts(path, _toc_nodes(ctx, entity)).get(node_key)


def cmd_ingest_next(ctx: Ctx) -> None:
    a = ctx.args
    entity = ctx.entity()
    r = ctx.rpc("koro_claim_next_node", entity=entity, device=ctx.cfg.device_id,
                lease_minutes=a.lease, node_key=a.node)
    if r.get("claimed"):
        key = r["node"]["key"]
        text = _source_text(ctx, entity, key)
        r["source_text"] = text
        if text is None:
            r["source_text_note"] = ("Source file not found on this machine: pass --pdf <path>, or read pages "
                                     f"{r['node']['page_start']}-{r['node']['page_end']} yourself.")
        r["payload_spec"] = str(DATA / "node-payload.md")
        r["next_steps"] = [f"write the payload JSON (see payload_spec) to a file",
                           f"koro ingest check <file> --node {key}",
                           f"koro ingest save {key} <file>",
                           f"if you can't finish: koro ingest release {key}"]
    emit(ctx, r)


def cmd_ingest_check(ctx: Ctx) -> None:
    from .payload import validate

    a = ctx.args
    payload = json.loads(Path(a.file).read_text(encoding="utf-8"))
    source = None
    if a.node:
        source = _source_text(ctx, ctx.entity(), a.node)
    errors, warnings = validate(payload, source)
    result = {"ok": not errors, "errors": errors, "warnings": warnings, "copy_check": source is not None}
    emit(ctx, result, lambda r: "\n".join([f"WARN  {w}" for w in r["warnings"]] + [f"ERROR {e}" for e in r["errors"]]
                                          + [("OK" if r["ok"] else "FAIL") + ("" if r["copy_check"] else
                                             " (copy check skipped: source text not available)")]))
    if errors:
        sys.exit(1)


def cmd_ingest_save(ctx: Ctx) -> None:
    from .payload import validate

    a = ctx.args
    entity = ctx.entity()
    payload = json.loads(Path(a.file).read_text(encoding="utf-8"))
    errors, _ = validate(payload, _source_text(ctx, entity, a.node))
    if errors and not a.force:
        raise KoroError("payload has errors (run `koro ingest check`):\n  " + "\n  ".join(errors))
    emit(ctx, ctx.rpc("koro_save_node", entity=entity, node_key=a.node, payload=payload, device=ctx.cfg.device_id),
         lambda r: f"Saved {r['node_key']}: {r['concepts']} concepts, {r['objectives']} objectives. "
                   f"{r['remaining']} nodes left.")


def cmd_ingest_release(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_release_node", entity=ctx.entity(), node_key=ctx.args.node, error=ctx.args.error))


def cmd_ingest_outline(ctx: Ctx) -> None:
    nodes = json.loads(Path(ctx.args.file).read_text(encoding="utf-8"))
    emit(ctx, ctx.rpc("koro_build_outline", entity=ctx.entity(), nodes=nodes))


def cmd_ingest_link(ctx: Ctx) -> None:
    refs = json.loads(Path(ctx.args.file).read_text(encoding="utf-8"))
    emit(ctx, ctx.rpc("koro_add_edge_refs", entity=ctx.entity(), refs=refs))


def cmd_ingest_finalize(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_finalize_ingest", entity=ctx.entity()))


def cmd_ingest_status(ctx: Ctx) -> None:
    toc = ctx.rpc("koro_toc", learner=ctx.learner, entity=ctx.entity())
    counts: dict = {}
    for n in toc.get("nodes") or []:
        counts[n["ingest_status"]] = counts.get(n["ingest_status"], 0) + 1
    pending = [n["key"] for n in toc.get("nodes") or [] if n["ingest_status"] != "parsed"]
    emit(ctx, {"entity": toc.get("entity"), "counts": counts, "not_parsed": pending},
         lambda r: f"{r['entity']['title']} [{r['entity']['status']}]: "
                   + ", ".join(f"{v} {k}" for k, v in r["counts"].items())
                   + (f"\nNot parsed yet: {' '.join(r['not_parsed'][:30])}" if r["not_parsed"] else ""))


# ---------------------------------------------------------------------------
# Complete ingest: the original file, every section's text, every figure.
# All of it goes to a private bucket / locked tables, from your own copy.
# ---------------------------------------------------------------------------
BUCKET = "koro-private"
TEXT_BATCH_CHARS = 300_000


def _progress(ctx: Ctx, message: str) -> None:
    if not ctx.args.json:
        print(message, file=sys.stderr, flush=True)


def _local_pdf(ctx: Ctx, entity: str, required: bool = True) -> Optional[str]:
    given = getattr(ctx.args, "pdf", None)
    if given:
        path = os.path.abspath(os.path.expanduser(given))
        if not os.path.exists(path):
            raise KoroError(f"no such file: {given}")
        return path
    path = config.source_for(ctx.cfg, entity)
    if not path and required:
        raise KoroError("koro doesn't know where this book's PDF is on this machine: pass --pdf <path>")
    return path


def _figure_libs():
    try:
        from . import figures  # noqa: F401
        import pdfplumber  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError:
        raise KoroError("figures need pdfplumber: pip install --upgrade koro-learn (or pip install pdfplumber)") from None
    from . import figures
    return figures


def figure_cache_path(entity: str, label: str) -> Path:
    from . import figures, pdf
    return pdf.cache_dir() / "figures" / entity / Path(figures.figure_path(entity, label)).name


def ingest_assets(ctx: Ctx, entity: str, path: str, force: bool = False, redetect: bool = False,
                  dpi: int = 200) -> dict:
    """Upload whatever is missing: the PDF, section texts, figure images. Safe to re-run."""
    import hashlib

    from . import pdf

    st = ctx.rpc("koro_asset_status", entity=entity)
    ent = st.get("entity") or {}
    digest = pdf.sha256_file(path)
    if ent.get("source_hash") and ent["source_hash"] != digest and not force:
        raise KoroError(f"{path} is not the file '{ent.get('title')}' was built from (the SHA-256 differs). Use the "
                        "original PDF, or pass --force if it is the same edition with identical pages.")
    if ctx.cfg.sources.get(entity) != path:
        ctx.cfg.sources[entity] = path
        config.save(ctx.cfg)
    out: dict = {"entity_id": entity, "title": ent.get("title")}

    # 1. The original file.
    src = st.get("source") or {}
    if force or src.get("sha256") != digest:
        data = Path(path).read_bytes()
        _progress(ctx, f"Uploading the PDF ({len(data) / 1e6:.1f} MB) to your private bucket…")
        from .figures import source_path
        spath = source_path(entity)
        ctx.client.upload(BUCKET, spath, data, "application/pdf")
        ctx.rpc("koro_register_source", entity=entity, storage_path=spath, size=len(data), sha256=digest)
        out["source"] = "uploaded"
    else:
        out["source"] = "already stored"

    # 2. Each section's original text (for grounding answers).
    secs = st.get("sections") or {}
    if force or secs.get("with_text", 0) < secs.get("total", 0):
        _progress(ctx, "Extracting section texts…")
        texts = pdf.node_texts(path, _toc_nodes(ctx, entity))
        saved, batch, size, unknown = 0, {}, 0, []

        def flush():
            nonlocal saved, batch, size
            if batch:
                r = ctx.rpc("koro_save_node_sources", entity=entity, texts=batch)
                saved += r.get("saved", 0)
                unknown.extend(r.get("unknown_keys") or [])
                batch, size = {}, 0

        for key, text in texts.items():
            if not text or not text.strip():
                continue
            if size + len(text) > TEXT_BATCH_CHARS:
                flush()
            batch[key] = text
            size += len(text)
        flush()
        out["sections"] = {"saved": saved, "of": secs.get("total"), "unknown_keys": unknown}
        _progress(ctx, f"  {saved} sections saved")
    else:
        out["sections"] = "already stored"

    # 3. Figures: detect once (or with --redetect), then render and upload the missing ones.
    figs = st.get("figures") or []
    if not figs or redetect:
        figures = _figure_libs()
        _progress(ctx, "Finding figures…")
        found = figures.detect_figures(path)
        _, pages, _ = pdf.read_pdf(path)
        figures.assign_nodes(found, pages, _toc_nodes(ctx, entity))
        for f in found:
            f["storage_path"] = figures.figure_path(entity, f["label"])
        if found:
            ctx.rpc("koro_register_figures", entity=entity, figures=found)
            figs = ctx.rpc("koro_asset_status", entity=entity).get("figures") or []
        out["figures_detected"] = len(found)
    todo = figs if force else [f for f in figs if not f.get("uploaded")]
    if todo:
        figures = _figure_libs()
        _progress(ctx, f"Uploading {len(todo)} figure{'s' if len(todo) != 1 else ''}…")
    for i, f in enumerate(todo, 1):
        png, width, height = figures.render(path, f["page"], f["bbox"], dpi=dpi)
        ctx.client.upload(f.get("bucket") or BUCKET, f["storage_path"], png, "image/png")
        ctx.rpc("koro_mark_uploaded", asset=f["asset_id"], size=len(png), sha256=hashlib.sha256(png).hexdigest(),
                width=width, height=height)
        cache = figure_cache_path(entity, f["label"])
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(png)
        except OSError:
            pass
        if i % 10 == 0 or i == len(todo):
            _progress(ctx, f"  {i}/{len(todo)}")
    out["figures"] = {"total": len(figs), "uploaded_now": len(todo)}
    return out


def cmd_ingest_assets(ctx: Ctx) -> None:
    a = ctx.args
    entity = ctx.entity()
    r = ingest_assets(ctx, entity, _local_pdf(ctx, entity), force=a.force, redetect=a.redetect, dpi=a.dpi)
    emit(ctx, r, render.assets)


def _figure_bytes(ctx: Ctx, entity: str, f: dict) -> bytes:
    """Stored image if there is one, else a fresh render from the local PDF."""
    problem = None
    if f.get("uploaded"):
        try:
            return ctx.client.download(f.get("bucket") or BUCKET, f["storage_path"])
        except KoroError as exc:
            problem = str(exc)
    path = _local_pdf(ctx, entity, required=False)
    if path and f.get("bbox"):
        png, _, _ = _figure_libs().render(path, f["page"], f["bbox"])
        return png
    raise KoroError(problem or f"figure {f['label']} isn't stored yet: run `koro ingest assets --pdf <path>` once")


def _open_file(path: Path) -> Optional[str]:
    import subprocess

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    except (OSError, AttributeError) as exc:
        return f"couldn't open a viewer ({exc}); the image is at {path}"


def cmd_figure(ctx: Ctx) -> None:
    a = ctx.args
    entity = ctx.entity()
    f = ctx.rpc("koro_figure", entity=entity, label=a.label)
    target = figure_cache_path(entity, f["label"])
    if a.refresh or not target.exists():
        data = _figure_bytes(ctx, entity, f)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    result = {"label": f["label"], "caption": f.get("caption"), "page": f.get("page"),
              "concept_key": f.get("concept_key"), "section": f.get("node_key"), "path": str(target)}
    if a.url:
        if not f.get("uploaded"):
            raise KoroError(f"figure {f['label']} isn't stored yet: run `koro ingest assets --pdf <path>` once")
        result["url"] = ctx.client.sign(f.get("bucket") or BUCKET, f["storage_path"], a.expires)
    if a.open:
        note = _open_file(target)
        result["opened"] = note is None
        if note:
            result["note"] = note
    emit(ctx, result, render.figure)


def cmd_figures(ctx: Ctx) -> None:
    entity = ctx.entity()
    if ctx.args.ref:
        figs = ctx.rpc("koro_figures", entity=entity, ref=ctx.args.ref)
    else:
        figs = ctx.rpc("koro_asset_status", entity=entity).get("figures") or []
    emit(ctx, [{k: f.get(k) for k in ("label", "caption", "page", "concept_key", "node_key", "uploaded")} for f in figs],
         render.figure_list)


def cmd_read(ctx: Ctx) -> None:
    from . import pdf

    entity = ctx.entity()
    r = ctx.rpc("koro_read_section", entity=entity, node_key=ctx.args.section)
    if not r.get("text"):
        path = _local_pdf(ctx, entity, required=False)
        if path:
            r["text"] = pdf.node_texts(path, _toc_nodes(ctx, entity)).get(ctx.args.section)
            r["text_from"] = "local pdf"
    emit(ctx, r, render.section)


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------
def cmd_continue(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_continue", learner=ctx.learner, client=ctx.cfg.client), render.start)


def cmd_start(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_start", learner=ctx.learner, entity=ctx.entity(), node_key=ctx.args.node,
                      client=ctx.cfg.client), render.start)


def cmd_next(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_next_concept", learner=ctx.learner, entity=ctx.entity(), node_key=ctx.args.node),
         render.next_concept)


def cmd_teach(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_teach", learner=ctx.learner, concept_ref=ctx.args.concept,
                      entity=ctx.entity(required=False), client=ctx.cfg.client), render.lesson)


def cmd_assess(ctx: Ctx) -> None:
    a = ctx.args
    emit(ctx, ctx.rpc("koro_assess", learner=ctx.learner, concept_ref=a.concept, verdict=a.verdict,
                      objective_key=a.objective, question=a.question, answer=a.answer, feedback=a.feedback,
                      misconception=a.misconception, kind=a.kind, weight=a.weight,
                      entity=ctx.entity(required=False), client=ctx.cfg.client), render.assessed)


def cmd_dispute(ctx: Ctx) -> None:
    a = ctx.args
    emit(ctx, ctx.rpc("koro_dispute", learner=ctx.learner, assessment=a.assessment_id, verdict=a.verdict,
                      reason=a.reason), render.assessed)


def cmd_known(ctx: Ctx) -> None:
    a = ctx.args
    emit(ctx, ctx.rpc("koro_mark_known", learner=ctx.learner, entity=ctx.entity(), ref=a.ref, note=a.note,
                      client=ctx.cfg.client),
         lambda r: f"Marked {r['marked_known']} concept(s) under {r['ref']} as known (lower confidence until checked).")


def cmd_toc(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_toc", learner=ctx.learner, entity=ctx.entity(), max_depth=ctx.args.depth), render.toc)


def cmd_status(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_status", learner=ctx.learner, entity=ctx.entity(), node_key=ctx.args.node),
         render.status)


def cmd_search(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_search", learner=ctx.learner, query=ctx.args.query,
                      entity=ctx.entity(required=False) if ctx.args.entity else None), render.search)


def _set_status(status: str):
    def run(ctx: Ctx) -> None:
        emit(ctx, ctx.rpc("koro_set_status", learner=ctx.learner, entity=ctx.entity(), status=status),
             lambda r: f"Marked {status}.")
    return run


def cmd_restart(ctx: Ctx) -> None:
    if not ctx.args.yes:
        raise KoroError("restart resets your progress on this book (history is kept): re-run with --yes")
    emit(ctx, ctx.rpc("koro_restart", learner=ctx.learner, entity=ctx.entity()),
         lambda r: f"Restarted. {r['concepts_reset']} concepts reset; your history is kept.")


def cmd_goal(ctx: Ctx) -> None:
    a = ctx.args
    emit(ctx, ctx.rpc("koro_set_goal", learner=ctx.learner, entity=ctx.entity(), target_date=a.by,
                      daily_minutes=a.daily))


def cmd_summary(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_set_summary", learner=ctx.learner, entity=ctx.entity(), summary=ctx.args.text),
         lambda r: "Saved.")


def cmd_time(ctx: Ctx) -> None:
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=ctx.args.days)).isoformat()
    emit(ctx, ctx.rpc("koro_study_time", learner=ctx.learner, since=since), render.study_time)


# ---------------------------------------------------------------------------
# Claude connector (remote MCP server on Supabase Edge Functions)
# ---------------------------------------------------------------------------
def connector_url(cfg: config.Config, token: str) -> str:
    return f"{cfg.supabase_url.rstrip('/')}/functions/v1/koro-mcp/{token}"


def cmd_connector_new(ctx: Ctx) -> None:
    r = ctx.rpc("koro_create_connector_token", learner=ctx.learner, label=ctx.args.label)
    result = {"url": connector_url(ctx.cfg, r["token"]), "token_id": r["token_id"], "label": ctx.args.label}
    emit(ctx, result, lambda x: (
        f"Connector URL ({x['label']}):\n\n  {x['url']}\n\n"
        "Add it in Claude: Settings > Connectors > Add custom connector, paste the URL, no OAuth.\n"
        "Treat the URL like a password; it is shown only once. Revoke with "
        f"`koro connector revoke {x['token_id']}`."))


def cmd_connector_list(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_list_connector_tokens", learner=ctx.learner), lambda rows: "\n".join(
        f"{t['token_id']}  {t['label']:<12} created {t['created_at'][:10]}"
        f"  last used {(t.get('last_used_at') or 'never')[:16]}"
        + ("  REVOKED" if t.get("revoked_at") else "") for t in rows) or "No connector URLs yet: `koro connector new`.")


def cmd_connector_revoke(ctx: Ctx) -> None:
    emit(ctx, ctx.rpc("koro_revoke_connector_token", learner=ctx.learner, token_id=ctx.args.token_id),
         lambda r: f"Revoked {r['token_id']}. Claude can no longer use that URL.")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-e", "--entity", help="book/paper/repo: uuid or title (default: most recent)")

    p = argparse.ArgumentParser(prog="koro", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"koro {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, fn, help_, **kw):
        sp = sub.add_parser(name, help=help_, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "connect to your Supabase project and pick the learner")
    sp.add_argument("--url"); sp.add_argument("--key"); sp.add_argument("--name"); sp.add_argument("--device")
    add("whoami", cmd_whoami, "show configuration")
    sp = add("install-skill", cmd_skill, "install the /koro skill for Claude Code (user level)")
    sp.add_argument("--dir", help="target directory (default ~/.claude/skills/koro)")

    add("library", cmd_library, "list your books, papers and repos")
    sp = add("add", cmd_add, "add a PDF: dedupe, outline, ready to ingest")
    sp.add_argument("path")
    sp.add_argument("--title"); sp.add_argument("--author"); sp.add_argument("--version", dest="version")
    sp.add_argument("--type", default="book", choices=["book", "paper"])
    sp.add_argument("--canonical", help="override canonical id, e.g. isbn:978..., arxiv:2401.12345")
    sp.add_argument("--rebuild-outline", action="store_true")
    sp.add_argument("--no-assets", action="store_true",
                    help="don't store the PDF, section texts and figures (run `koro ingest assets` later)")

    ing = add("ingest", None, "parse a source into lessons (driven by your coding agent)")
    isub = ing.add_subparsers(dest="ingest_command", metavar="step")

    def iadd(name, fn, help_):
        s = isub.add_parser(name, help=help_, parents=[common])
        s.set_defaults(fn=fn)
        return s

    s = iadd("next", cmd_ingest_next, "claim the next unparsed section and print its text")
    s.add_argument("--node", help="claim this node instead of the next pending one")
    s.add_argument("--lease", type=int, default=30, help="minutes before an unfinished claim expires")
    s.add_argument("--pdf", help="path to the source PDF on this machine")
    s = iadd("check", cmd_ingest_check, "validate a payload file (add --node for the copy check)")
    s.add_argument("file"); s.add_argument("--node"); s.add_argument("--pdf")
    s = iadd("save", cmd_ingest_save, "validate and save a payload for a node")
    s.add_argument("node"); s.add_argument("file"); s.add_argument("--pdf")
    s.add_argument("--force", action="store_true", help="save even if validation fails")
    s = iadd("release", cmd_ingest_release, "give a claimed node back (or mark it failed)")
    s.add_argument("node"); s.add_argument("--error")
    s = iadd("outline", cmd_ingest_outline, "upload a hand-made outline JSON (repos, papers without bookmarks)")
    s.add_argument("file")
    s = iadd("link", cmd_ingest_link, "add prerequisite links from a JSON file [{from,to,confidence}]")
    s.add_argument("file")
    iadd("finalize", cmd_ingest_finalize, "resolve links, break cycles, mark the source ready")
    iadd("status", cmd_ingest_status, "show parsing progress")
    s = iadd("assets", cmd_ingest_assets,
             "store the complete source privately: the PDF, every section's text, every figure (only what's missing)")
    s.add_argument("--pdf", help="path to the source PDF on this machine (remembered afterwards)")
    s.add_argument("--force", action="store_true", help="re-upload everything, and accept a PDF with a different hash")
    s.add_argument("--redetect", action="store_true", help="find figures again (keeps existing lesson links)")
    s.add_argument("--dpi", type=int, default=200, help="figure resolution (default 200)")

    add("continue", cmd_continue, "pick up whatever you were last reading")
    sp = add("start", cmd_start, "start or resume a book, optionally at a section")
    sp.add_argument("node", nargs="?", help="section key from `koro toc`, e.g. 5.3")
    sp = add("next", cmd_next, "what to learn next (and why)")
    sp.add_argument("--node", help="limit to a section")
    sp = add("teach", cmd_teach, "show a concept's lesson and mark it seen")
    sp.add_argument("concept", help="concept key, e.g. 5.3.1/prefix-caching")
    sp = add("figure", cmd_figure, "get one of the book's figures as an image (and open it)")
    sp.add_argument("label", help="figure label, e.g. 2.13")
    sp.add_argument("--open", action="store_true", help="open it in your image viewer")
    sp.add_argument("--url", action="store_true", help="also print a temporary link")
    sp.add_argument("--expires", type=int, default=3600, help="link lifetime in seconds (default 3600)")
    sp.add_argument("--refresh", action="store_true", help="fetch again instead of using the local copy")
    sp.add_argument("--pdf", help="render from this PDF if the image isn't stored")
    sp = add("figures", cmd_figures, "list figures for a concept or section (default: the whole book)")
    sp.add_argument("ref", nargs="?", help="concept key or section key")
    sp = add("read", cmd_read, "the original text of a section (private to you)")
    sp.add_argument("section", help="section key, e.g. 2.4.1")
    sp.add_argument("--pdf", help="read from this PDF if the text isn't stored")
    sp = add("assess", cmd_assess, "record a judged answer (evidence) for a concept")
    sp.add_argument("concept")
    sp.add_argument("--verdict", required=True, choices=["correct", "partial", "wrong"])
    sp.add_argument("--objective", help="objective key (omit for the whole concept)")
    sp.add_argument("--question"); sp.add_argument("--answer"); sp.add_argument("--feedback")
    sp.add_argument("--misconception")
    sp.add_argument("--kind", default="question", choices=["question", "exercise", "placement"])
    sp.add_argument("--weight", type=float, default=1.0)
    sp = add("dispute", cmd_dispute, "correct a verdict you disagree with")
    sp.add_argument("assessment_id"); sp.add_argument("--verdict", required=True, choices=["correct", "partial", "wrong"])
    sp.add_argument("--reason", required=True)
    sp = add("known", cmd_known, "mark a section or concept as already known")
    sp.add_argument("ref", help="section key (e.g. 2.1) or concept key"); sp.add_argument("--note")
    sp = add("toc", cmd_toc, "table of contents with your progress")
    sp.add_argument("--depth", type=int)
    sp = add("status", cmd_status, "strengths, gaps, fading concepts and goals")
    sp.add_argument("--node")
    sp = add("search", cmd_search, "find where a topic is covered")
    sp.add_argument("query")
    add("pause", _set_status("paused"), "pause a book")
    add("resume", _set_status("active"), "make a paused/dropped book active again")
    add("done", _set_status("completed"), "mark a book completed")
    add("drop", _set_status("dropped"), "drop a book (history kept)")
    sp = add("restart", cmd_restart, "start a book over (history kept)")
    sp.add_argument("--yes", action="store_true")
    sp = add("goal", cmd_goal, "set a target date and/or daily minutes")
    sp.add_argument("--by", help="YYYY-MM-DD"); sp.add_argument("--daily", type=int, help="minutes per day")
    sp = add("summary", cmd_summary, "save a short narrative of where you stand in a book")
    sp.add_argument("text")
    sp = add("time", cmd_time, "study time over the last N days")
    sp.add_argument("--days", type=int, default=7)

    con = add("connector", None, "manage the Claude connector URL (learn through claude.ai)")
    csub = con.add_subparsers(dest="connector_command", metavar="action")
    s = csub.add_parser("new", help="create a connector URL", parents=[common])
    s.add_argument("--label", default="claude.ai")
    s.set_defaults(fn=cmd_connector_new)
    s = csub.add_parser("list", help="list connector URLs (tokens are never shown again)", parents=[common])
    s.set_defaults(fn=cmd_connector_list)
    s = csub.add_parser("revoke", help="revoke a connector URL", parents=[common])
    s.add_argument("token_id")
    s.set_defaults(fn=cmd_connector_revoke)
    return p


def main(argv: Optional[list] = None, client: Optional[Client] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        if args.command in ("ingest", "connector"):
            parser.parse_args([args.command, "--help"])
        parser.print_help()
        return 2
    if not hasattr(args, "json"):
        args.json = False
    ctx = Ctx(args, client)
    try:
        args.fn(ctx)
    except KoroError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"koro: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"koro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
