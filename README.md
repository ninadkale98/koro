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
(Philip Kiely) ingested: 145 sections, 210 concepts, 435 objectives, 384 prerequisite links,
and all 85 figures mapped to the lesson beat that explains them. Your learner profile
("Ninad") exists. The one thing only your machine can do is upload your copy of the book
(step 6).

1. **Copy your secret key.** Supabase dashboard → your `koro` project → Project Settings →
   API Keys → a secret key (`sb_secret_...`). The legacy `service_role` key works too.
2. **Install the CLI** (Python 3.9+):
   ```bash
   pip install --upgrade koro-learn          # the command is still `koro`
   # or: pipx install koro-learn
   # or, straight from GitHub: pip install --upgrade git+https://github.com/ninadkale98/koro
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
6. **Store the complete book (once, from any one machine):**
   ```bash
   koro ingest assets --pdf ~/path/to/Inference_Engineering.pdf
   ```
   It checks the file is the same one the lessons were built from, then uploads the PDF,
   every section's text and all 85 figure images to a private Storage bucket (`koro-private`,
   readable only with your secret key). About a minute; re-running only uploads what's
   missing. After that, lessons show figures in the terminal (`koro figure 2.13 --open`) and in
   Claude chats, and Claude can check the book's exact wording when you ask.

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
`/koro:continue`. When a beat has a figure, Claude calls `show_figure`: it sees the book's
image, walks you through it, and gives you a link to open it (links last an hour). When you
ask what the book says exactly, it calls `read_section` and answers from the original text.
Say "save this to koro" in any chat and Claude calls `save_for_later` (see below).
Tools: `continue_learning`, `start_book`, `next_concept`, `get_lesson`, `show_figure`,
`read_section`, `record_answer`, `dispute_verdict`, `mark_known`, `table_of_contents`,
`progress_report`, `search_book`, `list_library`, `set_book_status`, `restart_book`,
`set_goal`, `save_learner_summary`, `study_time`, `save_for_later`, `list_saved`,
`update_saved`. Prompts: *Continue learning*, *Study a section*, *Quiz me*, *What did I
save?*, *How am I doing?*

Manage URLs with `koro connector list` and `koro connector revoke <id>`. Only a SHA-256 hash
of each token is stored. Opening the URL in a browser shows a small status page; add
`?selftest=1&figure=2.13` to check the tools and figure storage end to end.
Redeploying: `supabase functions deploy koro-mcp --no-verify-jwt` (the function checks the
token itself, so the platform JWT check must be off).

## Save things to learn later

Hit something intriguing while working? Save it in one line and keep going. It's there on
every machine and in every Claude chat.

```bash
koro save "paged attention vs continuous batching" -n "saw it in the vLLM scheduler" -t gpu
koro save https://arxiv.org/abs/2309.06180 -t papers        # the page title is looked up
koro save ~/Downloads/flash-attention.pdf -n "the tiling trick"   # uploaded to your private storage
pbpaste | koro save - --title "lock-free queue trick"      # a snippet from stdin
koro save "why is decode memory-bound?" --here               # also records repo, branch, folder
```

Then `koro saved` lists what's open (newest first, with tags), `koro saved 12` shows one,
`koro saved open 12` opens its link or file, and items move along with `koro saved planned 12`,
`learning`, `done`, `archived` or `reopen`. Filter with `--tag gpu`, `--search "kv"`, `--all`.
In Claude Code, say "save this to koro" (or `/koro:save ...`) and the agent writes the note for
you; "what did I save?" (or `/koro:saved`) gets a summary by theme. In a Claude chat with the
connector, the same phrases work. Saving the same link twice updates one item.

Coming next: turning saved items into a curriculum and teaching it.

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
| `koro teach <concept key>` | read a lesson (marks it seen); figures appear at their beat |
| `koro figure 2.13 --open` | open one of the book's figures (`--url` for a temporary link) |
| `koro figures [section or concept]` | list figures (default: the whole book) |
| `koro read 2.4.1` | the original text of a section, for your eyes only |
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
| `koro ingest assets --pdf <file>` | store the complete source privately (PDF, section texts, figures) |
| `koro save "<topic, link or file>" -n "why"` | save something to learn later (`-t` tags, `--here` for repo/branch) |
| `koro saved` / `saved 12` / `saved done 12` | what you saved; show one; move it along |

Add `--json` to any command for machine-readable output (agents always use it). With several
books, commands use the most recent one unless you pass `-e "<title or id>"`.

## Adding a new book or paper

```bash
koro add ~/papers/vllm.pdf          # dedupes by file hash and ISBN/arXiv/DOI, builds the outline,
                                    # and stores the PDF, section texts and figures privately
```
Then in Claude Code run `/koro:ingest` (or ask any agent to follow AGENTS.md → "Parsing a
source"). The agent claims one section at a time (`koro ingest next`), writes a lesson payload
in its own words, checks it (`koro ingest check`, which also rejects copied passages), saves
it (`koro ingest save`), and finishes with `koro ingest finalize`. Claims are leases, so you
can start on one laptop and finish on another, or run two machines at once. PDFs need real
text (not scans). For repos or PDFs without bookmarks, write an outline JSON and upload it
with `koro ingest outline`. Figures are found from their captions ("Figure 2.13: ...");
`koro ingest next` lists each section's figures, and the agent puts a figure's label in the
beat it illustrates, which links it to that concept.

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
- **Your copy stays yours:** lessons are teaching notes in the agent's own words (ingest
  rejects any 10-word run copied from the source) with page numbers pointing back to the
  book. The original itself (the PDF, each section's text, the figure images) is uploaded
  from your own copy into a private bucket and locked tables that only your secret key can
  read. It is used to show you figures and to ground answers, never shared, and links to a
  figure expire after an hour.
- **Figures:** each is an `assets` row (label, caption, page, crop box) linked to the concept
  and beat that explains it; the image lives at `koro-private/<book id>/figures/fig-<label>.png`.

## Repo layout

```
koro/                    Python CLI (pypdf, pdfplumber)
  cli.py client.py       commands → Supabase RPC and Storage
  pdf.py                 outline + per-section text from PDFs
  figures.py             find figures by caption, crop and render them
  payload.py             lesson payload validator (incl. copy check)
  data/                  the agent skill and payload spec shipped with the CLI
plugin/                  Claude Code plugin (skills: learn, continue, start, status, add, ingest)
.claude-plugin/          marketplace manifest
supabase/migrations/     schema + functions (applied to the koro project)
supabase/functions/koro-mcp/  the claude.ai connector (remote MCP server, Deno, no deps)
supabase/tests/          live end-to-end tests that roll themselves back
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

A curriculum built from saved items, the exercise runner (Fly.io containers), OAuth for the
connector, cross-book topic linking, and multi-user sharing. The schema already has room for all of them.

## Releasing

PyPI package `koro-learn` (the name `koro` was taken). Bump `version` in `pyproject.toml`
and `koro/__init__.py` and push to main (a `v*` tag works too).
`.github/workflows/publish.yml` runs the tests, builds, and publishes with PyPI trusted
publishing (no token stored); a version already on PyPI is skipped.
