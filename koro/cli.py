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
# Saved items: things to learn later, captured mid-work
# ---------------------------------------------------------------------------
URL = re.compile(r"^https?://\S+$", re.I)
MAX_SAVED_FILE = 50 * 1024 * 1024
SAVED_STATUSES = ("inbox", "planned", "learning", "done", "archived")


def _tags(values: Optional[list]) -> list:
    out: list = []
    for v in values or []:
        out += [t.strip() for t in v.split(",") if t.strip()]
    return out


def page_title(url: str, timeout: float = 4.0) -> Optional[str]:
    """Best-effort <title> of a web page (None if it can't be fetched quickly)."""
    import html
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "koro (+https://github.com/ninadkale98/koro)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if "html" not in (resp.headers.get("Content-Type") or ""):
                return None
            raw = resp.read(65536).decode(resp.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""
    return title[:200] or None


def _where_am_i(ctx: Ctx, here: bool) -> dict:
    """Where the item came up. Repo and folder only with --here (they may be work names)."""
    import subprocess

    out = {"device": ctx.cfg.device_id}
    if not here:
        return out
    out["cwd"] = os.getcwd()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3)
        if top.returncode == 0:
            out["repo"] = os.path.basename(top.stdout.strip())
            br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3)
            if br.returncode == 0:
                out["branch"] = br.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return out


SCISSORS = "# ------------------------ >8 ------------------------"


def open_in_editor(text: str, help_lines: list) -> Optional[str]:
    """Let the person edit text in $VISUAL/$EDITOR (like `git commit`). None if they emptied it.

    Everything below the scissors line is ignored, so code with # lines is safe to write."""
    import shlex
    import shutil
    import subprocess
    import tempfile

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or (
        "notepad" if os.name == "nt" else ("nano" if shutil.which("nano") else "vi"))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write((text or "").rstrip("\n") + "\n\n" + SCISSORS + "\n"
                 + "".join(f"# {line}\n" for line in help_lines + ["Everything from the line above down is ignored."]))
        path = fh.name
    try:
        try:
            subprocess.run(shlex.split(editor, posix=os.name != "nt") + [path], check=False)
        except OSError as exc:
            raise KoroError(f"couldn't start your editor ({editor}): {exc}. Set $EDITOR, or pass the text instead") from None
        result = Path(path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    result = result.split(SCISSORS, 1)[0].strip("\n")
    return result if result.strip() else None


def _title_and_body(text: str) -> tuple:
    lines = text.split("\n")
    title = lines[0].strip()
    return title, "\n".join(lines[1:]).strip("\n")


def _read_value(value: Optional[str]) -> Optional[str]:
    """`-` means read the text from stdin (for long text and code)."""
    if value == "-":
        text = sys.stdin.read()
        if not text.strip():
            raise KoroError("nothing on stdin")
        return text
    return value


def cmd_save(ctx: Ctx) -> None:
    import hashlib
    import mimetypes

    a = ctx.args
    what = " ".join(a.what).strip()
    title, body, url, path, kind = a.title, a.note, None, None, a.kind
    if body == "-" and what != "-":
        body = _read_value(body)
    if not what and not a.edit:
        raise KoroError("what should I save? e.g. koro save \"paged attention\" --note \"saw it in vLLM\" "
                        "(or koro save --edit to write it in your editor)")
    if what == "-":
        snippet = sys.stdin.read()
        if not snippet.strip():
            raise KoroError("nothing to save on stdin")
        first = next((l.strip() for l in snippet.splitlines() if l.strip()), "snippet")
        title = title or first[:80]
        body = f"{a.note}\n\n{snippet}" if a.note else snippet
        kind = kind or "snippet"
    elif URL.match(what):
        url = what
        kind = kind or ("pdf" if url.lower().split("?")[0].endswith(".pdf") else "url")
        title = title or (None if a.no_fetch else page_title(url)) or url
    elif os.path.isfile(os.path.expanduser(what)):
        path = os.path.abspath(os.path.expanduser(what))
        if os.path.getsize(path) > MAX_SAVED_FILE:
            raise KoroError(f"{what} is over 50 MB; save its link or title instead")
        kind = kind or ("pdf" if path.lower().endswith(".pdf") else "file")
        title = title or Path(path).name
    elif what:
        if len(what) > 200 and not title:  # a long thought: keep it whole as the note
            title, body = what[:80].rstrip() + "…", f"{what}\n\n{body}" if body else what
            kind = kind or "note"
        title = title or what

    if a.edit:  # write the title and a note of any length in your editor
        text = open_in_editor(f"{title or ''}\n\n{body or ''}", [
            "First line: the title. Below it: your note, as long as you like (paragraphs, examples, code).",
            "Save and close the editor to keep it; delete everything to cancel."])
        if text is None:
            emit(ctx, {"saved": False}, lambda r: "Nothing saved.")
            return
        title, body = _title_and_body(text)
        title = title or url or (Path(path).name if path else "")
        if not title:
            raise KoroError("the first line (the title) is empty")
        if not kind and "\n" in body.strip():
            kind = "note"

    r = ctx.rpc("koro_capture", learner=ctx.learner, title=title, kind=kind, body=body, url=url,
                tags=_tags(a.tag), context=_where_am_i(ctx, a.here), client=ctx.cfg.client, page=a.page)
    if path:
        data = Path(path).read_bytes()
        name = re.sub(r"[^\w.\-]+", "-", Path(path).name).strip("-") or "file"
        storage = f"saved/{ctx.learner}/{r['no']}-{name}"
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        _progress(ctx, f"Uploading {Path(path).name} ({len(data) / 1e6:.1f} MB) to your private storage…")
        ctx.client.upload(BUCKET, storage, data, mime)
        r = ctx.rpc("koro_attach_saved_file", learner=ctx.learner, ref=str(r["no"]), bucket=BUCKET,
                    storage_path=storage, filename=Path(path).name, mime=mime, size=len(data),
                    sha256=hashlib.sha256(data).hexdigest())
        r["existed"] = False
    emit(ctx, r, render.saved_saved)


def _saved_file(ctx: Ctx, item: dict) -> Path:
    from . import pdf

    f = item["file"]
    target = pdf.cache_dir() / "saved" / f"{item['no']}-{f['filename']}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ctx.client.download(f.get("bucket") or BUCKET, f["storage_path"]))
    return target


def cmd_saved(ctx: Ctx) -> None:
    a = ctx.args
    action, refs = a.action, list(a.refs)
    if action and re.fullmatch(r"#?\d+", action):  # `koro saved 12` = show #12
        action, refs = "show", [action] + refs
    if not action or action == "list":
        status = "all" if a.all else (a.status or "open")
        r = ctx.rpc("koro_saved_items", learner=ctx.learner, status=status,
                    tag=(_tags(a.tag) or [None])[0], query=a.search, limit=a.limit)
        emit(ctx, r, render.saved_list)
        return
    if not refs:
        raise KoroError(f"which item? e.g. koro saved {action} 12")
    results = []
    for ref in refs:
        if action == "show":
            results.append(ctx.rpc("koro_saved_item", learner=ctx.learner, ref=ref))
        elif action == "open":
            item = ctx.rpc("koro_saved_item", learner=ctx.learner, ref=ref)
            if item.get("file"):
                path = _saved_file(ctx, item)
                note = _open_file(path)
                item["path"] = str(path)
            elif item.get("url"):
                import webbrowser
                note = None if webbrowser.open(item["url"]) else f"open {item['url']} in your browser"
            else:
                note = "nothing to open: this item is a topic or note"
            if note:
                item["note"] = note
            results.append(item)
        elif action == "edit" and a.edit:  # rewrite the title and note in your editor
            item = ctx.rpc("koro_saved_item", learner=ctx.learner, ref=ref)
            text = open_in_editor(f"{item['title']}\n\n{item.get('body') or ''}", [
                f"Editing item #{item['no']}. First line: the title. Below it: the note.",
                "Save and close to keep your changes; delete everything to leave the item as it was."])
            if text is None or text == f"{item['title']}\n\n{item.get('body') or ''}".strip("\n"):
                item["note"] = "unchanged"
                results.append(item)
                continue
            new_title, new_body = _title_and_body(text)
            results.append(ctx.rpc("koro_set_saved_text", learner=ctx.learner, ref=ref, title=new_title, body=new_body))
            if a.tag or a.untag:
                results[-1] = ctx.rpc("koro_update_saved_item", learner=ctx.learner, ref=ref,
                                      add_tags=_tags(a.tag) or None, remove_tags=_tags(a.untag) or None)
        elif action in SAVED_STATUSES or action in ("edit", "reopen"):
            status = "inbox" if action == "reopen" else (action if action in SAVED_STATUSES else None)
            results.append(ctx.rpc("koro_update_saved_item", learner=ctx.learner, ref=ref, status=status,
                                   title=a.title, note=_read_value(a.note), add_tags=_tags(a.tag) or None,
                                   remove_tags=_tags(a.untag) or None))
        else:
            raise KoroError(f"unknown action {action!r}: use show, open, edit, planned, learning, done, "
                            "archived or reopen")
    emit(ctx, results[0] if len(results) == 1 else results, render.saved_items)


# ---------------------------------------------------------------------------
# The notebook: pages (nested, numbered like a book) holding your write-ups and saved items
# ---------------------------------------------------------------------------
def cmd_notebook(ctx: Ctx) -> None:
    a = ctx.args
    r = ctx.rpc("koro_notebook", learner=ctx.learner, ref=a.ref, items=a.items or None, all=a.all or None)
    emit(ctx, r, render.notebook)


def _page_edit_help(page: dict, section: Optional[str] = None) -> list:
    where = f"section \"{section}\" of " if section else ""
    return [f"Editing {where}page {page.get('number')} {page.get('title')} (version {page.get('version')}).",
            "Write freely: paragraphs, lists, examples, code blocks. Headings (## Name) become sections you can",
            "target later with --section / --under. Save and close to keep it; delete everything to cancel."]


def cmd_page(ctx: Ctx) -> None:
    a = ctx.args
    words = list(a.args)
    if not words:
        raise KoroError("which page? e.g. koro page 1.2, koro page new \"Signals\" -p 1, or see koro notebook")
    head = words[0]

    if head == "new":
        title = " ".join(words[1:]).strip() or a.title
        if not title:
            raise KoroError('koro page new "<title>" [-p <parent>] [--after <page>]')
        body = _read_value(a.write)
        r = ctx.rpc("koro_page_create", learner=ctx.learner, title=title, parent=a.parent, after=a.after,
                    body=body, tags=_tags(a.tag), client=ctx.cfg.client)
        if a.edit and not r.get("existed"):
            text = open_in_editor("", _page_edit_help(r))
            if text is not None:
                r = {**ctx.rpc("koro_page_write", learner=ctx.learner, ref=r["id"], op="replace", text=text,
                               if_version=r["version"], client=ctx.cfg.client), "existed": False}
        emit(ctx, r, render.page_created)
        return

    if head == "move":
        if len(words) < 2:
            raise KoroError("koro page move <page> (-p <new parent> | --after <page> | --top)")
        r = ctx.rpc("koro_page_move", learner=ctx.learner, ref=words[1], parent=a.parent, after=a.after,
                    top=a.top or None)
        emit(ctx, r, lambda x: f"Moved: now {x['number']} {x['title']}. See `koro notebook` for the new numbers.")
        return

    ref = " ".join(words)
    client = ctx.cfg.client
    if a.history:
        emit(ctx, ctx.rpc("koro_page_history", learner=ctx.learner, ref=ref), render.page_history)
        return
    if a.restore is not None:
        r = ctx.rpc("koro_page_restore", learner=ctx.learner, ref=ref, version=a.restore, client=client)
        emit(ctx, r, lambda x: f"Restored version {a.restore} of {x['number']} {x['title']} (now version {x['version']}).")
        return

    results = []
    if a.rename or a.status or a.tag or a.untag:
        results.append(ctx.rpc("koro_page_update", learner=ctx.learner, ref=ref, title=a.rename, status=a.status,
                               add_tags=_tags(a.tag) or None, remove_tags=_tags(a.untag) or None))
    write = None
    if a.edit:
        if a.section:
            cur = ctx.rpc("koro_page_section", learner=ctx.learner, ref=ref, section=a.section)
            text = open_in_editor(cur.get("text") or "", _page_edit_help(cur["page"], cur["heading"]))
            if text is not None and text.strip() != (cur.get("text") or "").strip():
                write = dict(op="replace_section", text=text, section=cur["heading"], if_version=cur["version"])
        else:
            cur = ctx.rpc("koro_page", learner=ctx.learner, ref=ref)
            text = open_in_editor(cur.get("body") or "", _page_edit_help(cur))
            if text is not None and text.strip() != (cur.get("body") or "").strip():
                write = dict(op="replace", text=text, if_version=cur["version"])
            elif text is None and (cur.get("body") or "").strip():
                raise KoroError("you emptied the editor, so nothing changed. To clear the write-up on purpose: "
                                f"koro page {ref} --write \"\"")
    elif a.append is not None:
        write = dict(op="append", text=_read_value(a.append), section=a.under, if_version=a.if_version)
    elif a.replace is not None:
        if a.with_ is None:
            raise KoroError('--replace needs --with: koro page 1.2 --replace "old words" --with "new words"')
        write = dict(op="replace_text", find=_read_value(a.replace), text=_read_value(a.with_), if_version=a.if_version)
    elif a.section and a.with_ is not None:
        write = dict(op="replace_section", section=a.section, text=_read_value(a.with_), if_version=a.if_version)
    elif a.write is not None:
        write = dict(op="replace", text=_read_value(a.write), if_version=a.if_version)
    elif a.section:
        emit(ctx, ctx.rpc("koro_page_section", learner=ctx.learner, ref=ref, section=a.section), render.page_section)
        return

    if write:
        results.append(ctx.rpc("koro_page_write", learner=ctx.learner, ref=ref, client=client, **write))
    if results:
        emit(ctx, results[-1], render.page_written)
        return
    if a.edit:
        emit(ctx, {"changed": False}, lambda r: "No changes.")
        return
    r = ctx.rpc("koro_page", learner=ctx.learner, ref=ref, full=a.full or None)
    if a.raw and not ctx.args.json:
        print(r.get("body") or "")
        return
    emit(ctx, r, render.page)


def cmd_file(ctx: Ctx) -> None:
    words = list(ctx.args.args)
    if len(words) < 2:
        raise KoroError("koro file <item numbers...> <page or inbox>, e.g. koro file 3 4 1.1.2")
    *items, page = words
    r = ctx.rpc("koro_file_items", learner=ctx.learner, items=items, page=page)
    emit(ctx, r, render.filed)


# ---------------------------------------------------------------------------
# Feature requests: things to build, captured from any tab, built later
# ---------------------------------------------------------------------------
FEATURE_STATUSES = ("idea", "planned", "building", "done", "dropped")
FEATURE_ACTIONS = {"idea": "idea", "reopen": "idea", "planned": "planned", "plan": "planned",
                   "building": "building", "start": "building", "done": "done", "drop": "dropped",
                   "dropped": "dropped"}


def _project(value: Optional[str]) -> Optional[str]:
    """-P . means the git repo you're in."""
    if value != ".":
        return value
    import subprocess

    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        top = None
    if not top or top.returncode != 0:
        raise KoroError("-P . means the git repo you're in, but this folder isn't in one: name the project")
    return os.path.basename(top.stdout.strip())


def cmd_feature(ctx: Ctx) -> None:
    a = ctx.args
    what = " ".join(a.what).strip()
    title, body = a.title, _read_value(a.note) if a.note == "-" and what != "-" else a.note
    if not what and not a.edit:
        raise KoroError('what should koro build? e.g. koro feature "save feature requests from any tab" -P koro '
                        '-n "details" (or koro feature --edit to write it in your editor)')
    if what == "-":  # first line = title, the rest = details
        if a.note == "-":
            raise KoroError("only one of the text and --note can come from stdin")
        text = sys.stdin.read()
        if not text.strip():
            raise KoroError("nothing on stdin")
        first, rest = _title_and_body(text.strip("\n"))
        title = title or first[:200]
        body = "\n\n".join(x for x in (body, rest if title == first[:200] else text.strip("\n")) if x)
    elif what:
        if len(what) > 200 and not title:  # a long request: keep it whole as the details
            title, body = what[:80].rstrip() + "…", f"{what}\n\n{body}" if body else what
        title = title or what
    if a.edit:
        text = open_in_editor(f"{title or ''}\n\n{body or ''}", [
            "First line: what to build, in a few words. Below it: the details, as long as you like",
            "(why, how it should work, examples). Save and close to keep it; delete everything to cancel."])
        if text is None:
            emit(ctx, {"saved": False}, render.feature_saved)
            return
        title, body = _title_and_body(text)
        if not title:
            raise KoroError("the first line (what to build) is empty")
    r = ctx.rpc("koro_add_feature", learner=ctx.learner, title=title, body=body, project=_project(a.project),
                priority=a.priority, tags=_tags(a.tag), context=_where_am_i(ctx, a.here), client=ctx.cfg.client)
    emit(ctx, r, render.feature_saved)


def cmd_features(ctx: Ctx) -> None:
    a = ctx.args
    action, refs = a.action, list(a.refs)
    if action and re.fullmatch(r"[#fF]?\d+", action):  # `koro features 12` / `F12` = show it
        action, refs = "show", [action] + refs
    if not action or action == "list":
        status = "all" if a.all else (a.status or "open")
        r = ctx.rpc("koro_features", learner=ctx.learner, status=status, project=_project(a.project),
                    query=a.search, limit=a.limit)
        emit(ctx, r, render.features_list)
        return
    if not refs:
        raise KoroError(f"which feature? e.g. koro features {action} F12")
    results = []
    for ref in refs:
        if action == "show":
            results.append(ctx.rpc("koro_feature", learner=ctx.learner, ref=ref))
        elif action == "edit" and a.edit:
            f = ctx.rpc("koro_feature", learner=ctx.learner, ref=ref)
            before = f"{f['title']}\n\n{f.get('body') or ''}"
            text = open_in_editor(before, [
                f"Editing F{f['no']}. First line: what to build. Below it: the details.",
                "Save and close to keep your changes; delete everything to leave it as it was."])
            if text is None or text == before.strip("\n"):
                f["note"] = "unchanged"
                results.append(f)
                continue
            new_title, new_body = _title_and_body(text)
            results.append(ctx.rpc("koro_update_feature", learner=ctx.learner, ref=ref, title=new_title,
                                   body=new_body, project=_project(a.project), priority=a.priority,
                                   add_tags=_tags(a.tag) or None, remove_tags=_tags(a.untag) or None))
        elif action in FEATURE_ACTIONS or action == "edit":
            status = FEATURE_ACTIONS.get(action)
            note = _read_value(a.note)
            results.append(ctx.rpc(
                "koro_update_feature", learner=ctx.learner, ref=ref, status=status, title=a.title,
                note=None if status == "done" else note, done_note=note if status == "done" else None,
                body=_read_value(a.details), project=_project(a.project), priority=a.priority,
                add_tags=_tags(a.tag) or None, remove_tags=_tags(a.untag) or None))
        else:
            raise KoroError(f"unknown action {action!r}: use show, edit, planned, building, done, drop or reopen")
    emit(ctx, results[0] if len(results) == 1 else results, render.feature_items)


# ---------------------------------------------------------------------------
# The guide: everything an agent (or a person) needs, from the CLI itself
# ---------------------------------------------------------------------------
def guide_sections() -> tuple:
    """(intro, [(topic, title, text)]) from data/guide.md, split on '## ' headings."""
    text = (DATA / "guide.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)
    intro, sections = parts[0].strip(), []
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        sections.append((head.strip().lower(), head.strip(), body.strip()))
    return intro, sections


def command_catalog(parser: Optional[argparse.ArgumentParser] = None) -> list:
    """Every command and option, read from the parser, so it can never drift from the CLI."""
    parser = parser or build_parser()

    def describe(sp: argparse.ArgumentParser, name: str, help_: str) -> dict:
        args, subs = [], []
        for act in sp._actions:
            if isinstance(act, argparse._HelpAction) or act.dest in ("json", "entity"):
                continue
            if isinstance(act, argparse._SubParsersAction):
                helps = {c.dest: c.help for c in act._choices_actions}
                subs += [describe(child, f"{name} {sub}", helps.get(sub, "")) for sub, child in act.choices.items()]
                continue
            args.append({k: v for k, v in {
                "flags": act.option_strings or None, "name": None if act.option_strings else act.dest,
                "help": act.help, "choices": list(act.choices) if act.choices else None,
                "required": act.required or None, "repeatable": isinstance(act, argparse._AppendAction) or None,
                "takes_value": None if act.nargs == 0 else True,
            }.items() if v is not None})
        out = {"command": f"koro {name}", "help": help_, "args": args}
        if sp.description and sp.description != help_:
            out["description"] = sp.description
        if sp.epilog:
            out["examples"] = [l.strip() for l in sp.epilog.splitlines()[1:] if l.strip()]
        if subs:
            out["subcommands"] = subs
        return out

    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {c.dest: c.help for c in action._choices_actions}
    return [describe(sp, name, helps.get(name, "")) for name, sp in action.choices.items()]


def _catalog_text(catalog: list) -> str:
    out = ["## commands", "",
           "Every command takes --json (machine-readable output) and -e/--entity <book> where a book applies.", ""]

    def walk(c: dict) -> None:
        out.append(f"{c['command']}  —  {c['help']}")
        for a in c["args"]:
            name = ", ".join(a["flags"]) if a.get("flags") else f"<{a['name']}>"
            extra = f" {{{','.join(map(str, a['choices']))}}}" if a.get("choices") else ""
            out.append(f"    {name}{extra}{' (repeatable)' if a.get('repeatable') else ''}  {a.get('help') or ''}".rstrip())
        for ex in c.get("examples") or []:
            out.append(f"    e.g. {ex}")
        out.append("")
        for sub in c.get("subcommands") or []:
            walk(sub)

    for c in catalog:
        walk(c)
    return "\n".join(out).rstrip()


def cmd_guide(ctx: Ctx) -> None:
    a = ctx.args
    intro, sections = guide_sections()
    topics = [t for t, _, _ in sections]
    catalog = command_catalog()
    topic = (a.topic or "").lower().strip()

    def section_text(t: str) -> str:
        if t == "commands":
            return _catalog_text(catalog)
        if t == "payload":
            return (DATA / "node-payload.md").read_text(encoding="utf-8").strip()
        title, body = next((ti, b) for tp, ti, b in sections if tp == t)
        return f"## {title}\n\n{body}"

    if a.list:
        emit(ctx, {"topics": topics}, lambda r: "Topics: " + ", ".join(r["topics"]) + "\nRead one: koro guide <topic>")
        return
    if topic:
        match = [t for t in topics if t == topic] or sorted((t for t in topics if t.startswith(topic)), key=len)
        if not match:
            raise KoroError(f"no guide topic {topic!r}. Topics: {', '.join(topics)}")
        t = match[0]
        if ctx.args.json:
            emit(ctx, {"topic": t, "text": section_text(t), **({"commands": catalog} if t == "commands" else {})})
        else:
            print(section_text(t))
        return
    full = "\n\n".join([intro] + [section_text(t) for t in topics])
    if ctx.args.json:
        emit(ctx, {"version": __version__, "topics": topics, "guide": full, "commands": catalog})
    else:
        print(full)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
SAVE_EXAMPLES = """examples:
  koro save "paged attention vs continuous batching" -n "saw it in the vLLM scheduler" -t gpu
  koro save "why is decode memory-bound?" -p 2.1          # straight onto notebook page 2.1
  koro save https://arxiv.org/abs/2309.06180 -t papers    # the page title is looked up
  koro save ~/Downloads/paper.pdf -n "the tiling trick"   # uploaded to your private storage
  koro save - --title "EINTR retry loop" -p signals < eintr.c   # long text or code from stdin
  koro save --edit -p signals                             # write title + note in your editor
"""

NOTEBOOK_EXAMPLES = """examples:
  koro notebook                 # every page with its number, status and item count, plus the inbox
  koro notebook 1.1 --items     # one branch, with the items on each page
  koro notebook inbox --items   # what's waiting to be filed
"""

FEATURE_EXAMPLES = """examples:
  koro feature "save feature requests from any tab" -P koro -n "so I can build them at night"
  koro feature "dark mode for the status page" -P . --priority low    # -P . = the repo you're in
  koro feature - -P rterm < idea.md          # first line = what to build, the rest = details
  koro feature --edit -P koro                # write it in your editor
"""

FEATURES_EXAMPLES = """examples:
  koro features                        # open requests by project: building, planned, then ideas
  koro features -P koro                # one project
  koro features F12                    # one request in full
  koro features building F12           # tonight's build (also: planned, done, drop, reopen)
  koro features done F12 -n "shipped in 0.5.0 (0b39786)"
  koro features edit F12 -n "one more detail" --priority high -P rterm
  koro features edit F12 --details - < spec.md    # replace the details
  koro features --status done          # what shipped
"""

PAGE_EXAMPLES = """examples:
  koro page 1.1                                  # read: write-up, its headings, sub-pages, items
  koro page signals --full                       # same page by name, every item in full
  koro page new "Signals" -p 1                   # new page under 1 (goes last: existing numbers keep)
  koro page new "Real-time signals" -p 1.1 --after 1.1.1
  koro page move 1.2 -p 1.1                      # make 1.2 a sub-page of 1.1 (or --top, or --after)
  koro page 1.1 --append "one more paragraph"    # add to the end, nothing else changes
  koro page 1.1 --append - --under EINTR < ex.c  # add under the "EINTR" heading
  koro page 1.1 --section EINTR                  # show one section
  koro page 1.1 --edit --section EINTR           # edit only that section in your editor
  koro page 1.1 --section EINTR --with "new text"           # replace one section
  koro page 1.1 --replace "any thread" --with "any one thread"
  koro page 1.1 --edit                           # whole write-up in your editor
  koro page 1.1 --rename "Unix signals" --status learning -t fastrpc
  koro page 1.1 --history ; koro page 1.1 --restore 3
"""
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-e", "--entity", help="book/paper/repo: uuid or title (default: most recent)")

    p = argparse.ArgumentParser(prog="koro", description=__doc__.split("\n")[0],
                                epilog="New here, or an agent? `koro guide` prints the whole manual; "
                                       "`koro guide <topic>` one part (e.g. notebook, teach, save).")
    p.add_argument("--version", action="version", version=f"koro {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, fn, help_, **kw):
        sp = sub.add_parser(name, help=help_, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("guide", cmd_guide, "the full manual: concepts, every command, workflows (start here, agents)")
    sp.add_argument("topic", nargs="?", help="one topic, e.g. notebook, teach, save, commands, payload")
    sp.add_argument("--list", action="store_true", help="list the topics")

    sp = add("init", cmd_init, "connect to your Supabase project and pick the learner")
    sp.add_argument("--url", help="your Supabase project URL, e.g. https://abc.supabase.co")
    sp.add_argument("--key", help="the project's secret key (omit it to be asked without echo)")
    sp.add_argument("--name", help="learner name (only needed when the project has several learners, or none)")
    sp.add_argument("--device", help="a name for this machine (default: its hostname)")
    add("whoami", cmd_whoami, "show configuration")
    sp = add("install-skill", cmd_skill, "install the /koro skill for Claude Code (user level)")
    sp.add_argument("--dir", help="target directory (default ~/.claude/skills/koro)")

    add("library", cmd_library, "list your books, papers and repos")
    sp = add("add", cmd_add, "add a PDF: dedupe, outline, ready to ingest")
    sp.add_argument("path", help="the PDF on this machine")
    sp.add_argument("--title", help="title (default: from the PDF)")
    sp.add_argument("--author", help="author (default: from the PDF)")
    sp.add_argument("--version", dest="version", help="edition or version, e.g. 2nd ed, v3")
    sp.add_argument("--type", default="book", choices=["book", "paper"], help="book (default) or paper")
    sp.add_argument("--canonical", help="override canonical id, e.g. isbn:978..., arxiv:2401.12345")
    sp.add_argument("--rebuild-outline", action="store_true", help="rebuild the outline of a book that exists")
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
    s.add_argument("file", help="the payload JSON file")
    s.add_argument("--node", help="section key, to check nothing is copied from its text")
    s.add_argument("--pdf", help="path to the source PDF on this machine")
    s = iadd("save", cmd_ingest_save, "validate and save a payload for a node")
    s.add_argument("node", help="section key, e.g. 2.4.1")
    s.add_argument("file", help="the payload JSON file")
    s.add_argument("--pdf", help="path to the source PDF on this machine")
    s.add_argument("--force", action="store_true", help="save even if validation fails")
    s = iadd("release", cmd_ingest_release, "give a claimed node back (or mark it failed)")
    s.add_argument("node", help="section key")
    s.add_argument("--error", help="why it failed (marks the section failed instead of pending)")
    s = iadd("outline", cmd_ingest_outline, "upload a hand-made outline JSON (repos, papers without bookmarks)")
    s.add_argument("file", help='JSON list of {"key","title","parent_key","kind","order_index","page_start","page_end"}')
    s = iadd("link", cmd_ingest_link, "add prerequisite links from a JSON file [{from,to,confidence}]")
    s.add_argument("file", help='JSON list of {"from": "<concept key>", "to": "<earlier key>", "confidence": 0.8}')
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
    sp.add_argument("concept", help="concept key, e.g. 2.4.1/arithmetic-intensity-roofline")
    sp.add_argument("--verdict", required=True, choices=["correct", "partial", "wrong"], help="your honest judgment")
    sp.add_argument("--objective", help="objective key (omit for the whole concept)")
    sp.add_argument("--question", help="the question you asked")
    sp.add_argument("--answer", help="the learner's answer, condensed")
    sp.add_argument("--feedback", help="what was right and what was missing")
    sp.add_argument("--misconception", help="a specific wrong belief the answer showed, if any")
    sp.add_argument("--kind", default="question", choices=["question", "exercise", "placement"],
                    help="question (default), exercise (hands-on), placement (a quick level check)")
    sp.add_argument("--weight", type=float, default=1.0, help="how much this evidence counts (default 1)")
    sp = add("dispute", cmd_dispute, "correct a verdict you disagree with")
    sp.add_argument("assessment_id", help="from an assess result, or recent_evidence in a lesson")
    sp.add_argument("--verdict", required=True, choices=["correct", "partial", "wrong"], help="the corrected verdict")
    sp.add_argument("--reason", required=True, help="why the original verdict was wrong")
    sp = add("known", cmd_known, "mark a section or concept as already known")
    sp.add_argument("ref", help="section key (e.g. 2.1) or concept key")
    sp.add_argument("--note", help="how they know it, e.g. 'use it at work'")
    sp = add("toc", cmd_toc, "table of contents with your progress")
    sp.add_argument("--depth", type=int, help="0 = chapters, 1 = sections, 2+ = subsections (default: all)")
    sp = add("status", cmd_status, "strengths, gaps, fading concepts and goals")
    sp.add_argument("--node", help="only this section, e.g. 2.4")
    sp = add("search", cmd_search, "find where a topic is covered")
    sp.add_argument("query", help="words to look for in section titles, summaries and lessons")
    add("pause", _set_status("paused"), "pause a book")
    add("resume", _set_status("active"), "make a paused/dropped book active again")
    add("done", _set_status("completed"), "mark a book completed")
    add("drop", _set_status("dropped"), "drop a book (history kept)")
    sp = add("restart", cmd_restart, "start a book over (history kept)")
    sp.add_argument("--yes", action="store_true", help="confirm (progress resets; history is kept)")
    sp = add("goal", cmd_goal, "set a target date and/or daily minutes")
    sp.add_argument("--by", help="YYYY-MM-DD"); sp.add_argument("--daily", type=int, help="minutes per day")
    sp = add("summary", cmd_summary, "save a short narrative of where you stand in a book")
    sp.add_argument("text", help="2-3 sentences: strengths, weak spots, where they are in the book")
    sp = add("time", cmd_time, "study time over the last N days")
    sp.add_argument("--days", type=int, default=7, help="how many days back (default 7)")

    sp = add("save", cmd_save, "save something to learn later: a topic, question, link, file or snippet",
             description="Save something to learn later. It goes to your inbox, or straight onto a notebook "
                         "page with -p. Notes can be as long as you like.",
             epilog=SAVE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("what", nargs="*", help='a topic or question ("paged attention"), a URL, a file path, '
                                            "or - to read a snippet from stdin")
    sp.add_argument("-n", "--note", help="why it caught your eye, where it came up (any length; - reads stdin)")
    sp.add_argument("-p", "--page", help="notebook page to put it on: number (1.2) or name (default: inbox)")
    sp.add_argument("--edit", action="store_true", help="write the title and note in your editor")
    sp.add_argument("-t", "--tag", action="append", help="tag (repeat, or comma-separate)")
    sp.add_argument("--title", help="a short title (default: the topic, the page title, or the file name)")
    sp.add_argument("--kind", choices=["topic", "question", "note", "url", "pdf", "file", "snippet"],
                    help="usually inferred")
    sp.add_argument("--here", action="store_true", help="also record the current repo, branch and folder")
    sp.add_argument("--no-fetch", action="store_true", help="don't look up a link's page title")
    sp = add("saved", cmd_saved, "things you saved to learn: list, show, open, or change their status")
    sp.add_argument("action", nargs="?", help="show | open | edit | planned | learning | done | archived | "
                                              "reopen (omit to list; a number alone shows that item)")
    sp.add_argument("refs", nargs="*", help="item numbers, e.g. 12 or #12")
    sp.add_argument("--status", choices=list(SAVED_STATUSES), help="list only this status (default: open items)")
    sp.add_argument("--all", action="store_true", help="list everything, including done and archived")
    sp.add_argument("-t", "--tag", action="append", help="list: filter by tag; edit: add tags")
    sp.add_argument("--untag", action="append", help="edit: remove tags")
    sp.add_argument("--search", help="list: search titles, notes, links and tags")
    sp.add_argument("-n", "--note", help="edit: append a note (- reads stdin)")
    sp.add_argument("--title", help="edit: new title")
    sp.add_argument("--edit", action="store_true", help="edit: rewrite the title and note in your editor")
    sp.add_argument("--limit", type=int, default=50, help="list: at most this many (default 50)")

    sp = add("notebook", cmd_notebook, "your notebook's table of contents: pages, numbers, item counts, inbox",
             epilog=NOTEBOOK_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("ref", nargs="?", help="only this page and what's under it (number or name), or inbox")
    sp.add_argument("--items", action="store_true", help="list the items on each page too")
    sp.add_argument("--all", action="store_true", help="include archived pages and items")

    sp = add("page", cmd_page, "read, create, move or edit a notebook page",
             description="Read a page, or change just the part you name. Pages are addressed by number from "
                         "`koro notebook` (1.2), by name, or by path (linux/signals).",
             epilog=PAGE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("args", nargs="*", help="<page> | new <title> | move <page>")
    g = sp.add_argument_group("reading")
    g.add_argument("--full", action="store_true", help="include every item in full")
    g.add_argument("--raw", action="store_true", help="print only the write-up (markdown)")
    g.add_argument("--section", help="a heading in the write-up: show it, edit it (--edit), or replace it (--with)")
    g = sp.add_argument_group("writing (only the part you name changes; every change is kept)")
    g.add_argument("--edit", action="store_true", help="edit the write-up (or --section) in your editor")
    g.add_argument("--append", help="add text at the end (or under --under); - reads stdin")
    g.add_argument("--under", help="with --append: the heading to add under")
    g.add_argument("--replace", help='exact text to replace (with --with), e.g. --replace "old" --with "new"')
    g.add_argument("--with", dest="with_", metavar="TEXT", help="the new text for --replace or --section; - reads stdin")
    g.add_argument("--write", help="replace the whole write-up (- reads stdin); with `new`, the first write-up")
    g.add_argument("--if-version", type=int, help="refuse the edit if the page changed since this version")
    g.add_argument("--history", action="store_true", help="earlier versions of the write-up")
    g.add_argument("--restore", type=int, metavar="VERSION", help="bring back an earlier version")
    g = sp.add_argument_group("organizing")
    g.add_argument("-p", "--parent", help="new/move: the page to put it under")
    g.add_argument("--after", help="new/move: place it right after this sibling page")
    g.add_argument("--top", action="store_true", help="move: to the top level")
    g.add_argument("--title", help="new: the title (instead of words after `new`)")
    g.add_argument("--rename", help="new title for the page")
    g.add_argument("--status", choices=list(SAVED_STATUSES), help="inbox, planned, learning, done or archived")
    g.add_argument("-t", "--tag", action="append", help="add tags")
    g.add_argument("--untag", action="append", help="remove tags")

    sp = add("file", cmd_file, "move saved items onto a notebook page (or back to the inbox)")
    sp.add_argument("args", nargs="+", help="<item numbers...> <page or inbox>, e.g. 3 4 1.1.2")

    sp = add("feature", cmd_feature, "add a feature request: something to build later, for any project",
             description="Add a feature request to build later. Same title in the same project, while open, "
                         "updates the existing request instead of adding a second one.",
             epilog=FEATURE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("what", nargs="*", help="what to build, in a few words (or - to read it from stdin: "
                                            "first line the title, the rest the details)")
    sp.add_argument("-n", "--note", help="the details: why, how it should work, examples (any length; - reads stdin)")
    sp.add_argument("-P", "--project", help="which project it's for, e.g. koro, rterm (. = the git repo you're in)")
    sp.add_argument("--priority", choices=["high", "normal", "low"], help="default normal")
    sp.add_argument("-t", "--tag", action="append", help="tag (repeat, or comma-separate)")
    sp.add_argument("--title", help="a short title, when the text is long")
    sp.add_argument("--edit", action="store_true", help="write the title and details in your editor")
    sp.add_argument("--here", action="store_true", help="also record the current repo, branch and folder")
    sp = add("features", cmd_features, "your feature requests: list, show, or move them along (idea → building → done)",
             epilog=FEATURES_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("action", nargs="?", help="show | edit | planned | building | done | drop | reopen "
                                              "(omit to list; a number alone, e.g. F12, shows that request)")
    sp.add_argument("refs", nargs="*", help="request numbers, e.g. F12, 12 or #12")
    sp.add_argument("--status", choices=list(FEATURE_STATUSES), help="list only this status (default: open requests)")
    sp.add_argument("--all", action="store_true", help="list everything, including done and dropped")
    sp.add_argument("-P", "--project", help="list: only this project; edit: move it to this project (. = this repo)")
    sp.add_argument("--search", help="list: search titles, details and tags")
    sp.add_argument("-n", "--note", help="append to the details; with done, what shipped (- reads stdin)")
    sp.add_argument("--details", help="edit: replace the whole details (- reads stdin)")
    sp.add_argument("--title", help="edit: new title")
    sp.add_argument("--priority", choices=["high", "normal", "low"], help="edit: change the priority")
    sp.add_argument("-t", "--tag", action="append", help="edit: add tags")
    sp.add_argument("--untag", action="append", help="edit: remove tags")
    sp.add_argument("--edit", action="store_true", help="edit: rewrite the title and details in your editor")
    sp.add_argument("--limit", type=int, default=100, help="list: at most this many (default 100)")

    con = add("connector", None, "manage the Claude connector URL (learn through claude.ai)")
    csub = con.add_subparsers(dest="connector_command", metavar="action")
    s = csub.add_parser("new", help="create a connector URL", parents=[common])
    s.add_argument("--label", default="claude.ai", help="a name for this URL, e.g. phone, work laptop")
    s.set_defaults(fn=cmd_connector_new)
    s = csub.add_parser("list", help="list connector URLs (tokens are never shown again)", parents=[common])
    s.set_defaults(fn=cmd_connector_list)
    s = csub.add_parser("revoke", help="revoke a connector URL", parents=[common])
    s.add_argument("token_id", help="from koro connector list")
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
