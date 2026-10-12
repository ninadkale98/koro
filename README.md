# koro

A headless teacher agent. Add a book, paper or repo once; then learn it from any terminal,
on any machine, with the coding agent you already use (Claude Code, Codex, ...) as the teacher.
Koro remembers what you've covered, what you've proved you know, and where you're weak.

- **No extra AI bill.** The `koro` CLI never calls a model. Your existing coding agent reads,
  teaches and judges; koro stores structure and evidence and decides what to teach next.
- **Start anywhere.** All state lives in Supabase. Close the terminal at work, run
  `koro continue` at home, and you're on the same concept.
- **Teaches, then checks.** One concept at a time, in short beats, then questions that make you
  reason. Every answer becomes evidence; strengths, gaps and misconceptions are calculated from it.

## Morning checklist (about 10 minutes)

Your Supabase project `koro` already has the full schema and **Inference Engineering**
(Philip Kiely) ingested: 145 sections, 210 concepts, 435 objectives, 384 prerequisite links.
Your learner profile ("Ninad") exists. Nothing else is needed on the database side.

1. **Copy your secret key.** Supabase dashboard → your `koro` project → Project Settings →
   API Keys → a secret key (`sb_secret_...`). The legacy `service_role` key works too.
2. **Install the CLI** (Python 3.9+):
   ```bash
   pip install git+https://github.com/ninadkale98/koro
   # or: pipx install git+https://github.com/ninadkale98/koro
   ```
3. **Connect:**
   ```bash
   koro init --url https://qrfgbkgbwtojxcylmhtl.supabase.co --key sb_secret_...
   ```
   It finds your learner automatically and saves `~/.config/koro/config.json` (mode 600).
   Repeat on each machine you use.
4. **Check it works:**
   ```bash
   koro continue
   ```
   You should see *Inference Engineering — 0/210 mastered* and the first concept,
   *The three layers of an inference platform*.
5. **Install the Claude Code plugin** (inside Claude Code):
   ```
   /plugin marketplace add ninadkale98/koro
   /plugin install koro@koro-marketplace
   ```
   Then `/koro:continue` starts a session. Prefer typing `/koro continue`? Run
   `koro install-skill` once instead (installs a user-level `/koro` skill).
6. **Optional:** tell koro where the PDF lives on this machine, so future re-parsing can read it:
   `koro add ~/path/to/Inference_Engineering.pdf` (it recognizes the existing book and won't
   duplicate it).

If `koro continue` fails with 401/403, the key is wrong or is the publishable key. If it fails
with 404, the schema isn't on that project.

## Learn by chatting with Claude (claude.ai connector)

Koro is also a remote MCP server, deployed as the Supabase Edge Function `koro-mcp`, so you
can learn in any Claude chat on claude.ai or Claude Desktop, with the same progress as the terminal.

1. Get your private connector URL: `koro connector new` (it is shown once; treat it like a password).
2. In Claude: **Settings → Connectors → Add custom connector**, give it a name ("Koro"),
   paste the URL, leave OAuth empty, and save. Custom connectors need a Pro, Max, Team or
   Enterprise plan.
3. In a chat, enable Koro from the tools menu and say "let's continue my book", or pick the
   *Continue learning* prompt from the connector's prompts.

Claude then teaches beat by beat, asks the checks, and records every answer, exactly like
`/koro:continue`. Tools: `continue_learning`, `start_book`, `next_concept`, `get_lesson`,
`record_answer`, `dispute_verdict`, `mark_known`, `table_of_contents`, `progress_report`,
`search_book`, `list_library`, `set_book_status`, `restart_book`, `set_goal`,
`save_learner_summary`, `study_time`. Prompts: *Continue learning*, *Study a section*,
*Quiz me*, *How am I doing?*

Manage URLs with `koro connector list` and `koro connector revoke <id>`. Only a SHA-256 hash
of each token is stored. Opening the URL in a browser shows a small status page.
Redeploying: `supabase functions deploy koro-mcp --no-verify-jwt` (the function checks the
token itself, so the platform JWT check must be off).

## Daily use

In Claude Code: `/koro:continue`, or just say "let's continue my book". The agent teaches a
concept, asks you questions, records how you did, and moves on. Things you can say:
"I already know this", "I think my answer was right", "jump to 5.3", "how am I doing?",
"where is speculative decoding covered?", "pause this book".

From a plain terminal:

| Command | What it does |
|---|---|
| `koro continue` | pick up whatever you were last reading |
| `koro start [section] -e "<book>"` | start a book, or focus on a section (e.g. `5.3`) |
| `koro toc` | table of contents with ✓ ◐ · ○ progress per section |
| `koro teach <concept key>` | read a lesson (marks it seen) |
| `koro next [--node 5.3]` | what to learn next, and why |
| `koro status` | strengths, gaps with misconceptions, fading concepts, time this week |
| `koro search "kv cache"` | where a topic is covered |
| `koro known 2.1` | "I already know this" (a section or a concept) |
| `koro dispute <id> --verdict correct --reason "..."` | correct a verdict |
| `koro pause` / `resume` / `done` / `drop` | manage a book |
| `koro goal --by 2026-12-01 --daily 20` | target date and daily minutes |
| `koro time --days 7` | study time vs goals |
| `koro restart --yes` | start a book over (history kept) |
| `koro library` | everything you've added |
| `koro connector new` / `list` / `revoke <id>` | manage the claude.ai connector URL |

Add `--json` to any command for machine-readable output (agents always use it). With several
books, commands use the most recent one unless you pass `-e "<title or id>"`.

## Adding a new book or paper

```bash
koro add ~/papers/vllm.pdf          # dedupes by file hash and ISBN/arXiv/DOI, builds the outline
```
Then in Claude Code run `/koro:ingest` (or ask any agent to follow AGENTS.md → "Parsing a
source"). The agent claims one section at a time (`koro ingest next`), writes a lesson payload
in its own words, checks it (`koro ingest check`, which also rejects copied passages), saves
it (`koro ingest save`), and finishes with `koro ingest finalize`. Claims are leases, so you
can start on one laptop and finish on another, or run two machines at once. PDFs need real
text (not scans). For repos or PDFs without bookmarks, write an outline JSON and upload it
with `koro ingest outline`.

## How it works

```
 Claude Code / Codex  ──(teaches, judges answers)──┐
        │ runs                                     │
        ▼                                          ▼
   koro CLI  ── one RPC per command ──►  Supabase Postgres functions
 (thin, no state)                        (all logic + all state)
```

- **Shared content:** `entities` (books/papers/repos) → `nodes` (outline tree, any depth) →
  `concepts` (lessons) → `objectives` (what knowing a concept means), `concept_edges`
  (prerequisites), `exercises`, `assets`, `topics` (cross-book, phase 2).
- **Your learning:** `assessments` is an append-only evidence log (answers, exercises,
  "I know this", disputes). `objective_progress` and `progress` are *calculated* from it by
  `koro_recompute_progress`, so gaps close cleanly, disputes never rewrite history, restarts
  keep it, and two devices writing at once can't lose anything. `enrollments` (one per book)
  hold status, focus section, goals and a narrative summary; `study_sessions` are sittings
  that close themselves after 20 idle minutes.
- **What to teach next** (`koro_next_concept`): resume a shown-but-unchecked concept; else
  review a gap from an earlier day or a fading concept (days since last seen > stability);
  else the earliest concept whose prerequisites you've got; else surface the missing
  prerequisite; else plain reading order. It never stalls.
- **Mastery:** each objective's mastery is a moving average of evidence (correct 1, partial
  0.5, wrong 0; "I know this" counts as 0.75 until checked). An objective is a strength at
  ≥ 0.7 with a correct last answer; a concept is mastered when all objectives are strengths.
  Stability (days before fading) grows ×2.5 per correct recall and shrinks on a miss.
- **Security:** row-level security is on for every table with no policies, and every
  function is revoked from `anon`/`authenticated`, so only the secret key can read or write.
  Single-user for now; per-user policies come with sharing.
- **Copyright:** the database never stores a source's raw text. Lessons are teaching notes
  in the agent's own words (ingest rejects any 10-word run copied from the source) with page
  numbers pointing back to your copy.

## Repo layout

```
koro/                    Python CLI (stdlib + pypdf)
  cli.py client.py       commands → Supabase RPC
  pdf.py                 outline + per-section text from PDFs
  payload.py             lesson payload validator (incl. copy check)
  data/                  the agent skill and payload spec shipped with the CLI
plugin/                  Claude Code plugin (skills: learn, continue, start, status, add, ingest)
.claude-plugin/          marketplace manifest
supabase/migrations/     schema + functions (applied to the koro project)
supabase/functions/koro-mcp/  the claude.ai connector (remote MCP server, Deno, no deps)
supabase/tests/          live end-to-end test that rolls itself back
docs/node-payload.md     the lesson payload format
AGENTS.md                instructions for Codex and other agents (generated)
tools/sync_docs.py       regenerates the plugin skill / AGENTS.md copies
tests/                   unit tests: python -m unittest discover -s tests -t .
                         connector: node --experimental-strip-types --test supabase/functions/koro-mcp/mcp.test.ts
```

## Setting up a fresh Supabase project

Apply `supabase/migrations/*.sql` in filename order (`supabase link --project-ref <ref>` then
`supabase db push`, or paste each file into the SQL editor). Then `koro init --name "<you>"`.

## Not built yet

The exercise runner (Fly.io containers), OAuth for the connector, cross-book topic linking,
publishing to PyPI, and multi-user sharing. The schema already has room for all of them.
