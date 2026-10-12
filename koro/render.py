"""Human-readable output. Agents use --json; people get these."""
from __future__ import annotations

import textwrap
from typing import Any

MARK = {"mastered": "✓", "learning": "◐", "seen": "·", "untouched": "○", "none": " "}


def _wrap(text: str, indent: str = "  ", width: int = 88) -> str:
    return "\n".join(textwrap.fill(p, width=width, initial_indent=indent, subsequent_indent=indent)
                     for p in str(text).split("\n") if p.strip()) or ""


def progress_line(r: dict) -> str:
    if not r or not r.get("concepts"):
        return "no concepts"
    return (f"{r.get('mastered', 0)}/{r['concepts']} mastered, {r.get('learning', 0)} learning, "
            f"{r.get('seen', 0)} seen, {r.get('untouched', 0)} untouched (mastery {r.get('mastery', 0)})")


def concept_card(c: dict, reason: str = "") -> str:
    if not c:
        return "Nothing left to teach here."
    pages = f"pp. {c.get('page_start')}-{c.get('page_end')}" if c.get("page_start") else ""
    head = f"{c['name']}  [{c['key']}]"
    why = {"resume": "resume (shown, not yet checked)", "gap": "review a gap", "fading": "review (fading)",
           "next": "next up", "prerequisite": "prerequisite first", "fallback": "next in reading order"}
    lines = [f"{'→ ' + why.get(reason, reason) + ': ' if reason else ''}{head}",
             f"  {c.get('node_title', '')} {pages} · {c.get('status', 'untouched')}"]
    if c.get("summary"):
        lines.append(_wrap(c["summary"]))
    return "\n".join(lines)


def library(items: list) -> str:
    if not items:
        return "Your library is empty. Add a source with: koro add <file.pdf>"
    out = []
    for e in items:
        enr = e.get("enrollment") or "not started"
        out.append(f"• {e['title']}" + (f" — {e['author']}" if e.get("author") else ""))
        out.append(f"    {enr} · {progress_line(e.get('progress') or {})} · id {e['entity_id']}")
    return "\n".join(out)


def start(r: dict) -> str:
    if r.get("none"):
        return "No book in progress.\n\n" + library(r.get("library") or [])
    st = r.get("status") or {}
    title = (st.get("entity") or {}).get("title", "")
    nxt = r.get("next") or {}
    out = [f"{title} — {progress_line(st.get('rollup') or {})}"]
    enr = st.get("enrollment") or {}
    if enr.get("knowledge_summary"):
        out.append(_wrap("Where you stand: " + enr["knowledge_summary"]))
    if enr.get("current_node"):
        out.append(f"  Focus: {enr['current_node']} {enr.get('current_node_title') or ''}")
    out.append("")
    if nxt.get("reason") == "complete":
        out.append("Everything in scope is mastered. 🎉")
    else:
        out.append(concept_card(nxt.get("concept"), nxt.get("reason", "")))
    return "\n".join(out)


def next_concept(r: dict) -> str:
    if r.get("reason") == "complete":
        return f"Everything in scope '{r.get('scope')}' is mastered."
    return concept_card(r.get("concept"), r.get("reason", ""))


def lesson(r: dict) -> str:
    c = r["concept"]
    tc = c.get("teaching_content") or {}
    node = r.get("node") or {}
    out = [f"{c['name']}  [{c['key']}]",
           f"{node.get('title', '')}  (pp. {node.get('page_start')}-{node.get('page_end')})", ""]
    if tc.get("hook"):
        out += [_wrap(tc["hook"], ""), ""]
    figs = {f.get("label"): f for f in r.get("figures") or []}
    shown = set()
    for i, b in enumerate(tc.get("beats") or [], 1):
        out.append(f"{i}. {b.get('title', '')}")
        out.append(_wrap(b.get("explain", "")))
        if b.get("diagram"):
            out.append("")
            out += ["    " + line for line in str(b["diagram"]).splitlines()]
        if b.get("example"):
            out.append(_wrap("Example: " + b["example"]))
        for label in _beat_figures(b):
            shown.add(label)
            out.append(figure_line(figs.get(label) or {"label": label}))
        out.append("")
    rest = [f for label, f in figs.items() if label not in shown]
    if rest:
        out += ["Figures:"] + [figure_line(f) for f in rest] + [""]
    if tc.get("takeaway"):
        out += [_wrap("Takeaway: " + tc["takeaway"], ""), ""]
    out.append("Objectives:")
    for o in r.get("objectives") or []:
        out.append(f"  {MARK.get({'strength': 'mastered', 'gap': 'learning'}.get(o['status'], 'untouched'))} "
                   f"{o['description']} [{o['key']}]")
        if o.get("misconception"):
            out.append(f"      misconception: {o['misconception']}")
    checks = tc.get("checks") or []
    if checks:
        out += ["", "Check yourself:"]
        for ch in checks:
            out.append("  - " + textwrap.fill(f"[{ch.get('objective')}] {ch.get('question')}", width=84,
                                              subsequent_indent="    "))
    ex = tc.get("exercise")
    if ex:
        out += ["", f"Exercise ({ex.get('language', '')}):", _wrap(ex.get("prompt", ""))]
    return "\n".join(out)


def _beat_figures(beat: dict) -> list:
    labels = beat.get("figures") or []
    if isinstance(beat.get("figure"), str):
        labels = list(labels) + [beat["figure"]]
    return [str(x) for x in labels]


def figure_line(f: dict) -> str:
    caption = f.get("caption") or ""
    page = f" (p.{f['page']})" if f.get("page") else ""
    return textwrap.fill(f"  ▣ Figure {f.get('label')}{page}: {caption}  → koro figure {f.get('label')} --open",
                         width=88, subsequent_indent="      ")


def figure(r: dict) -> str:
    out = [f"Figure {r['label']} (p.{r.get('page')}): {r.get('caption') or ''}", f"  {r['path']}"]
    if r.get("url"):
        out.append(f"  link (temporary): {r['url']}")
    if r.get("note"):
        out.append(f"  {r['note']}")
    return "\n".join(out)


def figure_list(items: list) -> str:
    if not items:
        return "No figures here."
    out = []
    for f in items:
        where = f.get("concept_key") or f.get("node_key") or ""
        mark = "✓" if f.get("uploaded") else "…"
        out.append(textwrap.fill(f"{mark} {f['label']:<6} p.{f.get('page')}  {f.get('caption') or ''}  [{where}]",
                                 width=100, subsequent_indent="           "))
    out.append("\n✓ stored  … not uploaded yet (koro ingest assets)")
    return "\n".join(out)


def section(r: dict) -> str:
    s = r.get("section") or {}
    out = [f"{s.get('key')} {s.get('title', '')}  (pp. {s.get('page_start')}-{s.get('page_end')})", ""]
    if r.get("text"):
        out += [_wrap(p, "", 92) + "\n" for p in str(r["text"]).split("\n\n") if p.strip()]
    else:
        out += ["(No text stored for this section yet: run `koro ingest assets --pdf <path>` once.)", ""]
    if r.get("figures"):
        out += ["Figures:"] + [figure_line(f) for f in r["figures"]]
    if r.get("subsections"):
        out += ["", "Subsections: " + ", ".join(f"{c['key']} {c['title']}" for c in r["subsections"])]
    if r.get("concepts"):
        out += ["Concepts: " + ", ".join(c["key"] for c in r["concepts"])]
    return "\n".join(out)


def assets(r: dict) -> str:
    src = r.get("source")
    secs = r.get("sections")
    figs = r.get("figures") or {}
    sec_line = secs if isinstance(secs, str) else f"{secs.get('saved')} of {secs.get('of')} saved"
    out = [f"{r.get('title') or 'Source'} — stored privately:",
           f"  original PDF: {src}",
           f"  section texts: {sec_line}",
           f"  figures: {figs.get('total', 0)} total, {figs.get('uploaded_now', 0)} uploaded now"
           + (f" ({r['figures_detected']} detected)" if "figures_detected" in r else "")]
    return "\n".join(out)


STATUS_MARK = {"inbox": "○", "planned": "◇", "learning": "◐", "done": "✓", "archived": "–"}


def _ago(stamp: str) -> str:
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - t).days
    return "today" if days < 1 else "yesterday" if days == 1 else f"{days}d ago" if days < 60 else t.strftime("%Y-%m-%d")


def saved_line(s: dict) -> str:
    tags = " ".join("#" + t for t in s.get("tags") or [])
    extra = s.get("url") or ((s.get("file") or {}).get("filename"))
    head = f"{STATUS_MARK.get(s.get('status'), ' ')} #{s['no']:<4} {s['title']}"
    page = s.get("page") or {}
    where = f"in {page.get('number')} {page.get('title')}" if page else ""
    tail = "  ".join(x for x in (f"[{s.get('kind')}]", tags, where, _ago(s.get("created_at", ""))) if x)
    out = [head, f"        {tail}"]
    if extra and extra != s["title"]:
        out.append(f"        {extra}")
    return "\n".join(out)


def saved_saved(s: dict) -> str:
    if s.get("saved") is False:
        return "Nothing saved."
    verb = "Already saved, updated" if s.get("existed") else "Saved"
    page = s.get("page") or {}
    where = f" on page {page['number']} {page['title']}" if page else " to your inbox"
    return (f"{verb} #{s['no']}{where}: {s['title']}"
            + (f"  ({' '.join('#' + t for t in s['tags'])})" if s.get("tags") else "")
            + ("\nFile it later with `koro file " + str(s["no"]) + " <page>`; see pages with `koro notebook`."
               if not page else ""))


def saved_list(r: dict) -> str:
    items = r.get("items") or []
    c = r.get("counts") or {}
    f = r.get("filter") or {}
    head = (f"Saved to learn: {c.get('inbox', 0)} inbox, {c.get('planned', 0)} planned, "
            f"{c.get('learning', 0)} learning, {c.get('done', 0)} done")
    if not items:
        return head + "\n\nNothing here" + (" for that filter." if f.get("tag") or f.get("query") else
                                            ". Save something with: koro save \"<topic, link or file>\" --note \"why\"")
    out = [head, ""] + [saved_line(s) for s in items]
    tags = r.get("tags") or []
    if tags and not f.get("tag"):
        out += ["", "Tags: " + "  ".join(f"#{t['tag']} ({t['n']})" for t in tags[:12])]
    out.append("\n○ inbox  ◇ planned  ◐ learning  ✓ done   ·  koro saved <n> to see one, koro saved done <n>")
    return "\n".join(out)


def saved_item(s: dict) -> str:
    out = [saved_line(s)]
    if s.get("body"):
        out += [""] + _verbatim(s["body"])
    ctx = {k: v for k, v in (s.get("context") or {}).items() if v}
    if ctx:
        out += ["", "  from: " + ", ".join(f"{k} {v}" for k, v in ctx.items())]
    if s.get("path"):
        out.append(f"  file: {s['path']}")
    if s.get("note"):
        out.append(f"  {s['note']}")
    return "\n".join(out)


def saved_items(r) -> str:
    return "\n\n".join(saved_item(s) for s in (r if isinstance(r, list) else [r]))


def assessed(r: dict) -> str:
    out = [f"{r.get('concept_key')}: {r.get('status')} (mastery {r.get('mastery')}, holds ~{r.get('stability_days')} days)"]
    for o in r.get("objectives") or []:
        mark = {"strength": "✓", "gap": "◐", "unseen": "○"}.get(o.get("status"), "?")
        out.append(f"  {mark} {o['description']}")
        if o.get("misconception"):
            out.append(f"      misconception: {o['misconception']}")
    return "\n".join(out)


def toc(r: dict) -> str:
    e = r.get("entity") or {}
    out = [f"{e.get('title', '')}" + (f" — {e['author']}" if e.get("author") else "") + f"  [{e.get('status', '')}]"]
    cur = r.get("current_node")
    for n in r.get("nodes") or []:
        pr = n.get("progress")
        if n.get("ingest_status") != "parsed":
            mark = "…"
        elif not n.get("teachable") or not pr:
            mark = "—"
        else:
            mark = MARK.get(pr.get("status"), " ")
        counts = f"  {pr['mastered']}/{pr['concepts']}" if pr and pr.get("concepts") else ""
        here = "  ← you are here" if cur and n["key"] == cur else ""
        out.append(f"{'  ' * n.get('depth', 0)}{mark} {n['title']}  (p.{n.get('page_start')}){counts}{here}")
    out.append("\n✓ mastered  ◐ learning  · seen  ○ untouched  — reference only  … not parsed yet")
    return "\n".join(out)


def status(r: dict) -> str:
    e = r.get("entity") or {}
    out = [f"{e.get('title', '')} [{r.get('scope')}] — {progress_line(r.get('rollup') or {})}"]
    enr = r.get("enrollment") or {}
    if enr:
        goal = []
        if enr.get("target_date"):
            goal.append(f"finish by {enr['target_date']}")
        if enr.get("daily_minutes"):
            goal.append(f"{enr['daily_minutes']} min/day")
        out.append(f"  {enr.get('status')}" + (f" · goal: {', '.join(goal)}" if goal else ""))
        if enr.get("knowledge_summary"):
            out.append(_wrap(enr["knowledge_summary"]))
    if r.get("gaps"):
        out.append("\nGaps:")
        for g in r["gaps"]:
            out.append(f"  ◐ {g['concept']} [{g['concept_key']}]: {g['objective']}")
            if g.get("misconception"):
                out.append(f"      misconception: {g['misconception']}")
    if r.get("fading"):
        out.append("\nFading (due for review):")
        for f in r["fading"]:
            out.append(f"  ↺ {f['concept']} [{f['concept_key']}] — {f['days_since']} days since last seen")
    if r.get("strengths"):
        out.append("\nRecently mastered:")
        out += [f"  ✓ {s['concept']}" for s in r["strengths"][:8]]
    week = r.get("this_week") or []
    if week:
        w = week[0]
        out.append(f"\nThis week: {w.get('minutes')} min over {w.get('sittings')} sittings")
    return "\n".join(out)


def search(r: dict) -> str:
    out = [f"Search: {r.get('query')}"]
    if r.get("nodes"):
        out.append("Sections:")
        out += [f"  {n['key']}  {n['title']}  (p.{n.get('page_start')})" for n in r["nodes"]]
    if r.get("concepts"):
        out.append("Concepts:")
        out += [f"  {MARK.get(c.get('status'), ' ')} {c['name']}  [{c['key']}]" for c in r["concepts"]]
    if len(out) == 1:
        out.append("  nothing found")
    return "\n".join(out)


def study_time(items: list) -> str:
    if not items:
        return "No study time recorded yet."
    out = []
    for t in items:
        line = f"• {t['title']}: {t['minutes']} min, {t['sittings']} sittings, {t['concepts_touched']} concepts"
        if t.get("goal_minutes_in_range"):
            line += f" (goal {t['goal_minutes_in_range']} min)"
        if t.get("days_left") is not None:
            line += f" · {t['concepts_remaining']} concepts left, {t['days_left']} days to target"
        out.append(line)
    return "\n".join(out)


def generic(value: Any) -> str:
    import json
    return json.dumps(value, indent=2, ensure_ascii=False)


def _verbatim(text: str, indent: str = "  ") -> list:
    """Text as written (code keeps its indentation), indented."""
    return [indent + line if line.strip() else "" for line in str(text).rstrip().split("\n")]


# ---------------------------------------------------------------------------
# Notebook
# ---------------------------------------------------------------------------
def _count(n, word="item") -> str:
    return f"{n} {word}{'' if n == 1 else 's'}" if n else ""


def notebook(r: dict) -> str:
    pages = r.get("pages") or []
    inbox = r.get("inbox") or {}
    out = []
    if r.get("scope") in (None, "inbox"):
        out.append(f"{'':<9}Inbox{'':<29}{_count(inbox.get('items', 0)) or 'empty'}")
        for it in inbox.get("item_list") or []:
            out.append(f"{'':<13}#{it['no']:<4} {it['title']}  [{it['kind']}]")
    if not pages and r.get("scope") != "inbox":
        out.append("\nNo pages yet. Make one: koro page new \"Signals\"   (and -p <parent> to nest it)")
    for pg in pages:
        num = pg["number"]
        name = ("  " * pg["depth"]) + pg["title"]
        mark = STATUS_MARK.get(pg["status"], " ")
        status = f"{mark} {pg['status']}" if pg["status"] != "inbox" else ""
        bits = "  ".join(x for x in (status, _count(pg.get("items", 0)), "✎" if pg.get("has_writeup") else "") if x)
        out.append(f"{num:<8} {name:<34} {bits}".rstrip())
        for it in pg.get("item_list") or []:
            out.append(f"{'':<9}{'  ' * pg['depth']}  #{it['no']:<4} {it['title']}  [{it['kind']}]")
    out.append("\n✎ has a write-up  ·  koro page <number> to open one  ·  koro save ... -p <number> to add to it")
    return "\n".join(out)


def _page_head(r: dict) -> str:
    path = " › ".join(f"{p['number']} {p['title']}" for p in r.get("path") or [])
    mark = STATUS_MARK.get(r.get("status"), " ")
    tags = " ".join("#" + t for t in r.get("tags") or [])
    head = f"{r.get('number')}  {r.get('title')}"
    meta = "  ·  ".join(x for x in (f"{mark} {r.get('status')}", tags, f"v{r.get('version')}") if x)
    return "\n".join(x for x in (path, head + "    " + meta, "─" * min(88, max(40, len(head) + len(meta) + 4))) if x)


def page(r: dict) -> str:
    out = [_page_head(r)]
    if r.get("body"):
        out += _verbatim(r["body"], "")
    else:
        out.append(f"(no write-up yet: koro page {r.get('number')} --edit, or --append \"...\")")
    heads = r.get("headings") or []
    if heads:
        out += ["", "Sections: " + " · ".join(h["heading"] for h in heads)]
    subs = r.get("subpages") or []
    if subs:
        out += ["", "Sub-pages:"] + [f"  {s['number']:<8} {s['title']}  {_count(s.get('items', 0))}".rstrip() for s in subs]
    items = r.get("item_list") or []
    if items:
        out += ["", "Items:"]
        for it in items:
            if "body" in it or "context" in it or it.get("id"):
                out.append(saved_item(it))
            else:
                extra = it.get("url") or it.get("file") or (f"{it['chars']} chars" if it.get("chars") else "")
                out.append(f"  #{it['no']:<4} {it['kind']:<8} {it['title']}" + (f"   {extra}" if extra else ""))
    return "\n".join(out)


def page_section(r: dict) -> str:
    pg = r.get("page") or {}
    out = [f"{pg.get('number')} {pg.get('title')}  ›  {r.get('heading')}   (page v{r.get('version')})", ""]
    out += _verbatim(r.get("text") or "(empty section)", "")
    return "\n".join(out)


def page_created(r: dict) -> str:
    if r.get("existed"):
        return f"Page {r['number']} {r['title']} already exists (nothing new created)."
    return f"Made page {r['number']} {r['title']}. Add to it with: koro save \"...\" -p {r['number']}"


def page_written(r: dict) -> str:
    if r.get("changed") is False:
        return f"No change to {r.get('number')} {r.get('title')}."
    return f"Saved {r.get('number')} {r.get('title')} (version {r.get('version')}). Undo with --history / --restore."


def page_history(r: dict) -> str:
    out = [f"{r.get('number')} {r.get('title')}: now version {r.get('version')}"]
    for h in r.get("history") or []:
        start = (h.get("starts") or "").replace("\n", " ")
        out.append(f"  v{h['version']:<4} {str(h.get('replaced_at', ''))[:16]}  {h.get('chars', 0)} chars  {start[:60]}")
    if len(out) == 1:
        out.append("  (no earlier versions)")
    else:
        out.append(f"\nBring one back: koro page {r.get('number')} --restore <version>")
    return "\n".join(out)


def filed(r: dict) -> str:
    pg = r.get("page")
    where = f"page {pg['number']} {pg['title']}" if pg else "the inbox"
    nums = ", ".join(f"#{i['no']}" for i in r.get("items") or [])
    return f"Moved {nums} to {where}."
