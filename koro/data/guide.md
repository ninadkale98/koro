# koro guide

Koro is a personal teacher that lives in the learner's own Supabase database. It holds the
books, papers and repos they study (as lessons), every piece of evidence about what they know,
and a notebook of things they want to learn. Koro never calls an AI model: **you, the agent,
are the teacher.** You read lessons and progress with the `koro` CLI, teach, judge answers, and
write evidence back. Every command takes `--json`; agents always use it.

Topics (`koro guide <topic>`): setup, concepts, teach, commands-learning, books, payload,
figures, notebook, save, json, connector, rules, commands. `koro guide` prints everything.

## setup

```bash
pip install --upgrade koro-learn        # the command is `koro`; Python 3.9+
koro init --url https://<project>.supabase.co   # asks for the secret key (hidden)
koro whoami                              # what this machine is connected to
```

- Config lives in `~/.config/koro/config.json` (mode 600): project URL, secret key, learner,
  device name, and where each book's PDF is on this machine. Environment variables override
  it: `KORO_SUPABASE_URL`, `KORO_SUPABASE_KEY`, `KORO_LEARNER_ID`, `KORO_CONFIG`.
- Every machine runs `koro init` once. All state is in the database, so any machine continues
  where another stopped.
- No pip access (an office mirror without the package)? Install the wheel file directly:
  `pip install --user ./koro_learn-<version>-py3-none-any.whl`.
- Claude Code plugin: `/plugin marketplace add ninadkale98/koro` then
  `/plugin install koro@koro-marketplace`. Or `koro install-skill` for a user-level `/koro` skill.

## concepts

**Books and lessons** (shared content):
- An *entity* is a book, paper or repo. Its outline is a tree of *sections* with keys like
  `2`, `2.4`, `2.4.1` (also `preface`, `appA`). `koro toc` shows them.
- A section holds *concepts*: lessons with keys like `2.4.1/arithmetic-intensity-roofline`.
  A lesson is `teaching_content`: a hook, 2-4 *beats* (explanation, optional diagram, example,
  figures), *checks* (questions with answer guides), an optional exercise, and a takeaway.
- Each concept has 1-3 *objectives* (what knowing it means), each with key like `explain`.
- *Prerequisites* link concepts; koro teaches what you're ready for first.
- *Figures* are the book's images (label `2.13`), stored privately; *section text* is the
  book's original text per section, stored privately for grounding.

**The learner** (private):
- Every answer is *evidence* in an append-only log. Progress is calculated from it: an
  objective's mastery is a moving average (correct 1, partial 0.5, wrong 0, "I know this"
  0.75). An objective is a *strength* at mastery >= 0.7 with a correct last answer; otherwise a
  *gap* (often with a recorded *misconception*). A concept is `untouched`, `seen` (taught, not
  checked), `learning`, or `mastered` (all objectives strengths). Mastered concepts *fade*:
  each correct recall makes the review interval 2.5x longer.
- What to teach next (`reason`): `resume` (shown, never checked), `gap` (an earlier miss to
  revisit), `fading` (due for review), `next` (prerequisites met), `prerequisite` (teach this
  missing one first), `fallback` (reading order), `complete`.
- An *enrollment* per book holds its status (active, paused, completed, dropped), focus
  section, goals and a short narrative summary of where the learner stands.

**The notebook** (private): saved *items* (topics, questions, links, PDFs, snippets, notes;
numbered `#1`, `#2`, ...) organized into *pages* that nest (numbered `1`, `1.1`, `1.1.2`,
like a book). A page has its own *write-up* (markdown; its headings are sections you can
target) and holds items. Items without a page are in the *inbox*. Details: `koro guide notebook`.

## teach

The session loop. Follow it every time you teach.

1. **Orient.** `koro continue --json` (or `koro start [section] -e "<book>" --json`). Tell the
   learner in 2-3 lines: the book, mastered/total, their summary if any, what's next and why.
2. **Prerequisite gate.** If `reason` is `prerequisite`, name the missing concept and offer to
   learn it first (recommended) or go ahead anyway (`koro teach <the concept they wanted>`).
3. **Teach.** `koro teach <concept key> --json`. Open with the hook, then ONE beat at a time in
   your own words: short, concrete, about a minute of reading. Show a beat's `diagram` in a
   code block and use its `example`. Pause after each beat. Aim at objectives that are gaps
   or have a misconception. Point to `node.page_start`-`page_end`; never claim to quote the
   book (lessons are paraphrased notes).
   - A beat with `figures` (e.g. `["2.13"]`): `koro figure 2.13 --open --json` opens it on the
     learner's screen and returns a local `path`; read that PNG yourself and walk them through it.
   - Grounding: `koro read <section key> --json` returns the section's original text. Answer
     from it in your own words; quote at most a sentence or two, with pages.
4. **Check every objective.** Ask each check, one at a time; wait for the answer. Judge it
   against `answer_guide`: `correct`, `partial` or `wrong`. Honest, never rounded up. Record it
   right away:
   ```bash
   koro assess <concept key> --objective <objective key> --verdict <v> \
     --question "<what you asked>" --answer "<their answer, condensed>" \
     --feedback "<what was right / missing>" [--misconception "<specific wrong belief>"] --json
   ```
   Give a sentence or two of feedback. After `partial`/`wrong`, re-explain from another angle
   and ask a fresh variant (record that too). All objectives strong = concept mastered.
5. **Hands-on.** For `modality` `code` or `exercise`, offer the `exercise`, judge against
   `solution_sketch`, record with `--kind exercise`.
6. **Move on.** `koro next --json` and repeat. Every few concepts and at the end, save where
   they stand: `koro summary "Solid on X; shaky on Y; hasn't started chapter 4." --json`.

Learner says → you run:
- "I already know this" → `koro known <section or concept> --json`, then offer one confirm question.
- "My answer was right" → `koro dispute <assessment_id> --verdict correct --reason "..." --json`.
- "Jump to 5.3" → `koro start 5.3 --json`. "Where's X covered?" → `koro search "X" --json`.
- "How am I doing?" → `koro status --json`. "How much did I study?" → `koro time --days 7 --json`.
- "Pause / done / drop this book" → `koro pause` / `koro done` / `koro drop`.
- "Start over" → confirm first, then `koro restart --yes` (history is kept).
- "Finish by Dec 1, 20 min a day" → `koro goal --by 2026-12-01 --daily 20`.

## commands-learning

| Command | What it does | Key JSON fields |
|---|---|---|
| `koro library` | books, papers, repos with status and progress | `[{entity_id, title, enrollment, progress}]` |
| `koro continue` | resume the last book | `status.entity`, `status.rollup`, `status.enrollment`, `next.reason`, `next.concept` |
| `koro start [section] -e BOOK` | start/resume a book, optionally focus a section | same as continue |
| `koro next [--node 5.3]` | what to teach next, and why | `reason`, `concept{key,name,node_key,page_start,status}` |
| `koro teach KEY` | a lesson (marks it seen) | `concept.teaching_content`, `objectives[{key,status,mastery,misconception}]`, `figures`, `prerequisites`, `recent_evidence[{id,...}]`, `node{page_start,page_end}` |
| `koro assess KEY --verdict V [--objective O] ...` | record evidence | `assessment_id`, `status`, `mastery`, `objectives[]` |
| `koro dispute ID --verdict V --reason R` | correct a verdict (history kept) | `status`, `objectives[]` |
| `koro known REF [--note]` | "I already know this" | `marked_known` |
| `koro toc [--depth N]` | outline with progress | `nodes[{key,title,depth,page_start,progress}]`, `current_node` |
| `koro status [--node N]` | strengths, gaps, fading, goals, time | `rollup`, `gaps[{concept_key,objective,misconception}]`, `fading`, `strengths` |
| `koro search "Q"` | where a topic is covered | `nodes[]`, `concepts[]` |
| `koro goal --by DATE --daily MIN` | goals for a book | |
| `koro summary "TEXT"` | the learner's narrative for a book | |
| `koro time [--days 7]` | study time vs goals | `[{title, minutes, sittings, goal_minutes_in_range}]` |
| `koro pause` / `resume` / `done` / `drop` | book status | |
| `koro restart --yes` | reset progress (history kept) | `concepts_reset` |

Several books: commands use the most recent active one; pass `-e "<title or id>"` otherwise.

## books

Adding a source: `koro add <file.pdf> --json` deduplicates (same file, ISBN, arXiv id or DOI
returns the existing book), builds the outline from the PDF's bookmarks, remembers the file's
path on this machine, and stores the complete source privately: the PDF, each section's text,
every figure (`--no-assets` skips that; `koro ingest assets --pdf <path>` does it later and
uploads only what's missing).

Parsing it into lessons (you write them, one section at a time; resumable on any machine,
several machines can work at once):
1. `koro guide payload` (or the `payload_spec` path in `ingest next`) once: the lesson format.
2. Loop:
   - `koro ingest next --json` claims the next section (a 30-minute lease) and returns its
     `source_text` and `figures`.
   - Write the payload JSON to a temp file, in your own words. No run of 10 words may match the
     source; the check enforces it. Put each figure's label in the `figures` list of the beat it
     illustrates.
   - `koro ingest check <file> --node <key> --json`; fix errors; `koro ingest save <key> <file> --json`.
   - Can't finish? `koro ingest release <key>`.
   - Stop when `ingest next` returns `"done": true`.
3. Optional cross-chapter prerequisites: `koro ingest link <file>` with
   `[{"from": "<key>", "to": "<earlier key>", "confidence": 0.8}]`.
4. `koro ingest finalize --json`: resolves links, breaks cycles, marks the book ready.

Other: `koro ingest status`, `koro ingest outline <file>` (hand-made outline for repos or
PDFs without bookmarks).

## payload

The lesson format for `koro ingest save` is printed by `koro guide payload`.

## figures

- `koro figure <label> [--open] [--url] --json`: one figure; `path` is a local PNG (read it to
  see it), `--open` opens it on the learner's screen, `--url` adds a link valid for an hour.
- `koro figures [section or concept] --json`: figures with captions, pages, the concept they
  belong to, and whether they're stored.
- `koro read <section key> --json`: the section's original text (`text`), its figures,
  subsections and concepts. For grounding only: teach in your own words.

## notebook

The notebook is where the learner keeps things to learn: saved items organized into pages.

**Addressing a page** (any command that takes a page):
- its number from `koro notebook`: `1`, `1.1`, `1.1.2`;
- its name, or any unique part of it: `signals`, `"Unix signals"`;
- a slug path: `linux-internals/signals` (a trailing part of the path also works);
- `inbox` where an item's place is asked for, meaning "no page".
Numbers come from order. New pages go last under their parent, so existing numbers don't
change; only inserting with `--after`, moving or archiving renumbers (koro prints the new
number). If a name matches several pages, koro lists them; use the number.

**Reading**
```bash
koro notebook --json                  # TOC: pages[{number,depth,title,status,items,has_writeup}], inbox{items}
koro notebook 1.1 --items --json      # one branch, with item_list per page
koro notebook inbox --items --json    # what's waiting to be filed
koro page 1.1 --json                  # one page: body (markdown), version, headings[], subpages[], item_list[], path[]
koro page 1.1 --full --json           # items in full (notes, snippets, links)
koro page 1.1 --section EINTR --json  # one section of the write-up: heading, text, version
koro page 1.1 --history --json        # earlier versions
```

**Writing: change only the part you mean.** Every change bumps `version` and keeps the old
text (`--history`, `--restore N`). Long text and code: pass `-` and pipe it on stdin.
```bash
koro page 1.1 --append "one more paragraph" --json               # to the end; nothing else changes
koro page 1.1 --append - --under EINTR --json < example.md       # to the end of one section
koro page 1.1 --section EINTR --with - --json < new_section.md   # replace one section (heading kept)
koro page 1.1 --replace "any thread" --with "any one thread" --json   # exact text, must occur once
koro page 1.1 --write - --json < whole.md                        # replace the whole write-up
koro page 1.1 --restore 3 --json                                 # bring back version 3
```
Add `--if-version N` (the `version` from your last read) to any write: koro refuses if the
page changed since, e.g. the learner edited it on another machine. On that error, re-read and
redo the edit; never force over their change.

**Organizing**
```bash
koro page new "Signals" -p 1 --json                     # under page 1, placed last
koro page new "Real-time signals" -p 1.1 --after 1.1.1  # right after a sibling
koro page new "Signals" --write - < intro.md            # with a first write-up
koro page move 1.2 -p 1.1 | --after 1.1.1 | --top
koro page 1.1 --rename "Unix signals" --status learning -t fastrpc --untag linux
koro file 3 4 1.1.2                                      # move items #3 #4 onto page 1.1.2 (or: inbox)
koro save "..." -p 1.1                                   # save straight onto a page
```
Creating a page whose title already exists under the same parent returns the existing page
(`existed: true`). Pages and items are never deleted: set `--status archived` (hidden unless
`--all`). Page status (inbox, planned, learning, done, archived) is what the learner is doing
with the topic.

**For agents**
- Before adding, read `koro notebook --json` and pick the most fitting page; if none fits,
  propose a new page and where it goes (number and parent) before creating it, unless the
  learner already told you where.
- Make the smallest edit that does the job: `--append`/`--under` to add, `--section --with` or
  `--replace --with` to change; `--write` only when rewriting everything was asked for.
- Read before you change, pass `--if-version`, and on a conflict re-read rather than retry blind.
- `--edit` opens an interactive editor: that's for the learner at a terminal, never for you.
- Write in markdown. Use headings (`## EINTR`) for parts the learner may want to target later.

## save

`koro save` captures something to learn later, in one line, without breaking the learner's flow.

```bash
koro save "<topic or question>" -n "<why it came up>" -t <tag> [-p <page>] --json
koro save https://arxiv.org/abs/2309.06180 -t papers --json   # title looked up (--title to set it)
koro save ~/Downloads/paper.pdf -n "the tiling trick" --json  # file uploaded to private storage (max 50 MB)
koro save - --title "EINTR retry loop" -p signals --json < snippet.c   # long text or code from stdin
koro save "topic" -n - --json < long_note.md                  # a long note from stdin
koro save --edit                                              # learner only: write it in an editor
```
- Kinds are inferred: topic, question (ends with ?), note (long), url, pdf, file, snippet.
- Same link saved twice = one item (tags merged, new note appended); `existed: true`.
- `--here` also records repo, branch and folder: only if the learner wants that.
- Result: `{no, title, kind, body, url, tags, status, page{number,title}, existed}`. Confirm in
  one line with the number (`#12`) and the page or inbox, then get back to what they were doing.

The list and lifecycle:
```bash
koro saved --json                         # open items: items[], counts{}, tags[{tag,n}]
koro saved --tag gpu --search kv --all    # filters; --status done
koro saved 12 --json                      # one item in full
koro saved open 12                        # open its link or file
koro saved planned 12 13 | learning | done | archived | reopen
koro saved edit 12 -n "more detail" -t x --untag y --title "..."   # -n appends to the note
koro saved edit 12 --edit                 # learner only: rewrite title and note in an editor
```
"What did I save?": summarize by theme (or by page) rather than reading the list out.

Rules: koro is a personal database. Save the idea, a public link or a description in your own
words, never proprietary code or internal documents from the learner's job.

## json

- `--json` prints exactly one JSON value on stdout. Errors print `{"error": "<message>"}` and
  exit with status 1; the message says what to do (e.g. which page names matched).
- Numbers you pass back: concept keys (`2.4.1/slug`), objective keys, assessment ids, item
  numbers (`12` or `#12`), page numbers (`1.1.2`) or names, page `version` for `--if-version`.
- Human output (without `--json`) is for people and may change; don't parse it.

## connector

Koro is also a remote MCP server (Supabase Edge Function `koro-mcp`) for Claude chats on
claude.ai and Claude Desktop, with the same database. The learner gets a private URL with
`koro connector new` (shown once) and adds it under Settings > Connectors. Manage with
`koro connector list` / `revoke <id>`. Its tools mirror the CLI: continue_learning,
start_book, next_concept, get_lesson, show_figure, read_section, record_answer,
dispute_verdict, mark_known, table_of_contents, progress_report, search_book, list_library,
set_book_status, restart_book, set_goal, save_learner_summary, study_time, save_for_later,
list_saved, update_saved, and the notebook tools notebook_toc, read_page (whole page, one
section, or history), write_page (op append, replace_section, replace_text, replace or
restore, with if_version), create_page, update_page, move_page and file_items.
save_for_later takes an optional page. The connector is deployed separately from the CLI, so
right after an update it can lag behind until it is redeployed.

## rules

- Teach in your own words; lessons are paraphrased notes. Quote the book at most a sentence or
  two, with page numbers. The book's text and figures are private to the learner.
- Judge answers honestly; never round up. Record every answer right away.
- Nothing is deleted in koro: archive or change status instead.
- Read before you write; pass versions; on conflicts re-read.
- Save ideas and public links, never proprietary code or internal documents.
- Use `--json` and act on the fields; don't parse human output.

## commands

Every command with its options (generated from the CLI itself) is printed by
`koro guide commands`.
