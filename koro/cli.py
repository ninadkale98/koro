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
    emit(ctx, result, lambda x: (
        f"{'Already in your library' if x['existed'] else 'Added'}: {x['title']} ({x['entity_id']})"
        + (f"\nOutline: {x['nodes']} sections, {x['teachable_nodes']} teachable."
           f"\nNext: parse it with your coding agent (`/koro:ingest` in Claude Code), or `koro ingest next`."
           if "nodes" in x else "")))


def _source_text(ctx: Ctx, entity: str, node_key: str) -> Optional[str]:
    from . import pdf

    path = ctx.args.pdf if getattr(ctx.args, "pdf", None) else config.source_for(ctx.cfg, entity)
    if not path:
        return None
    toc = ctx.rpc("koro_toc", learner=ctx.learner, entity=entity)
    nodes = [{"key": n["key"], "title": n["title"], "page_start": n.get("page_start"), "depth": n.get("depth", 0)}
             for n in toc.get("nodes") or []]
    return pdf.node_texts(path, nodes).get(node_key)


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

    add("continue", cmd_continue, "pick up whatever you were last reading")
    sp = add("start", cmd_start, "start or resume a book, optionally at a section")
    sp.add_argument("node", nargs="?", help="section key from `koro toc`, e.g. 5.3")
    sp = add("next", cmd_next, "what to learn next (and why)")
    sp.add_argument("--node", help="limit to a section")
    sp = add("teach", cmd_teach, "show a concept's lesson and mark it seen")
    sp.add_argument("concept", help="concept key, e.g. 5.3.1/prefix-caching")
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
    return p


def main(argv: Optional[list] = None, client: Optional[Client] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        if args.command == "ingest":
            parser.parse_args(["ingest", "--help"])
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
