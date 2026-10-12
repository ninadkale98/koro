---
name: koro
description: Teach the user a book, paper or repo with koro, their headless teacher agent. Use when the user says /koro, wants to learn/study/continue/resume a book, asks what they were reading, wants a lesson, quiz or progress report, wants to add or parse a new source for learning, or wants to save something to learn later ("save this to koro", "remind me to learn X") or see what they saved.
argument-hint: "[continue | start <book> [section] | status | toc | search <topic> | save <thing> | saved | add <file.pdf> | ingest]"
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---

# Koro: teaching sessions

Koro stores what the learner is studying and everything they've shown they know. It never
calls a model: **you** are the teacher. You read lessons and progress from the `koro` CLI,
teach, judge answers, and write evidence back. Always pass `--json` and read the JSON.

Arguments: $ARGUMENTS

## Route the request

- empty, `continue`, `resume` → `koro continue --json`
- `start <book> [section]` → `koro start [section] -e "<book>" --json`
- `status` → `koro status --json` · `toc` → `koro toc --json` · `search <topic>` → `koro search "<topic>" --json`
- `save <thing>`, "save this to koro", "I want to learn X later" → see "Saving things to learn later"
- `saved`, "what did I save?" → `koro saved --json`
- `add <file>` → see "Adding a source" · `ingest` → see "Parsing a source"
- anything else ("teach me attention", "quiz me on chapter 3"): find it with `koro search`, then teach it.

If `koro` is missing, install it: `pip install koro-learn` (the command is `koro`) then
`koro init`. If it is installed but can't reach the database, see "Fallback" at the end.

## The session loop

1. **Orient.** From `continue`/`start`, tell the learner in two or three lines where they stand:
   the book, mastered/total, their `knowledge_summary` if any, and what's next and why
   (`reason`). Reasons: `resume` (shown last time, never checked), `gap` (an earlier miss
   to revisit), `fading` (due for review), `next`, `prerequisite`, `fallback`, `complete`.
2. **Prerequisite gate.** If `reason` is `prerequisite`, say which concept they're missing and
   offer: learn it first (recommended), or go ahead anyway (`koro teach <wanted key>`).
3. **Teach.** `koro teach <concept key> --json` returns `teaching_content` (hook, beats,
   checks, optional exercise, takeaway), the concept's `figures`, the learner's `objectives`
   with status and misconceptions, `prerequisites`, `recent_evidence`, and the source pages.
   - Open with the hook. Then teach **one beat at a time** in your own words: short, concrete,
     a minute of reading. Show a beat's `diagram` in a code block and use its `example`.
     Pause after each beat ("make sense?") so they can steer. Don't dump all beats at once.
   - If an objective is a `gap` or has a `misconception`, aim the explanation straight at it.
   - Point to the pages (`node.page_start`-`page_end`) for the original text. Never claim to
     quote the book; the lessons are paraphrased teaching notes.
   - **Figures.** A beat may list `figures` (labels such as `"2.13"`; captions and pages are
     in the lesson's `figures`). At that beat run `koro figure <label> --open --json`: it
     opens the book's figure on the learner's screen and returns a local `path`. Read that
     PNG yourself, then walk them through it in your own words.
   - **Grounding.** When they ask what the book says exactly, or you're unsure of a detail,
     `koro read <section key> --json` returns that section's original text (stored privately
     for them). Answer from it in your own words; quote at most a sentence or two, with pages.
4. **Check every objective.** Ask the checks one at a time and wait for an answer. Judge it
   against `answer_guide`: `correct`, `partial`, or `wrong`. Be fair and honest: never round
   up to be nice. Record it immediately:

   ```
   koro assess <concept key> --objective <objective key> --verdict <v> \
     --question "<what you asked>" --answer "<their answer, condensed>" \
     --feedback "<what was right / missing>" [--misconception "<specific wrong belief>"] --json
   ```

   Give feedback in a sentence or two. After `partial`/`wrong`, re-explain from a different
   angle and ask a fresh variant (not the same question) and record that too. The result shows
   each objective's new status; once all are `strength` the concept is `mastered`.
5. **Hands-on.** When `modality` is `code` or `exercise`, offer the `exercise`. Let them write
   the code; run it for them if useful; judge against `solution_sketch`; record with
   `--kind exercise`.
6. **Move on.** `koro next --json` and repeat. Every few concepts, and at the end of a
   session, save a 2-3 sentence narrative of where they stand:
   `koro summary "Solid on roofline and decode being memory-bound; shaky on KV cache sizing; hasn't started chapter 4." --json`

## What the learner might say

| They say | You run |
|---|---|
| "I already know this" (a concept or whole section) | `koro known <key> --json`, then offer a one-question confirm |
| "I think my answer was right" | `koro dispute <assessment_id> --verdict correct --reason "..." --json` (ids are in `recent_evidence` and in each assess result) |
| "Jump to 5.3" / "only this section" | `koro start 5.3 --json` (the section becomes the focus) |
| "Where's X covered?" | `koro search "X" --json` |
| "Show me the figure / diagram" | `koro figure <label> --open --json` (`koro figures <section or concept> --json` lists them) |
| "What does the book say exactly?" | `koro read <section key> --json` |
| "How am I doing?" | `koro status --json`: strengths, gaps with misconceptions, fading, time this week |
| "Pause / I'm done with this book / drop it" | `koro pause` / `koro done` / `koro drop` |
| "Start over" | confirm first, then `koro restart --yes` (history is kept) |
| "I want to finish by Dec 1, 20 min a day" | `koro goal --by 2026-12-01 --daily 20` |
| "How much did I study?" | `koro time --days 7 --json` |
| "Save this / I want to learn X later" | `koro save "<X>" --note "<why>" --tag <t> --json` (see below) |
| "What did I save?" / "anything saved about GPUs?" | `koro saved --json` (`--tag gpu`, `--search "..."`, `--all`) |

Several books: commands default to the most recent one; pass `-e "<title or id>"` otherwise.

## Saving things to learn later

While working, the learner may hit something they want to learn properly later: a topic, a
question, a link, a paper, a snippet. Save it right away, confirm in one line with its number,
and get back to what they were doing:

```
koro save "<topic, question, URL or file path>" --title "<short title>" \
  --note "<one line: why it came up, what they were doing>" --tag <topic> --json
```

- A URL is saved as a link (koro looks up the page title; pass `--title` if you know a better
  one). A file path, such as a PDF, is uploaded to their private storage so it's there on every
  machine. `koro save - --title "..."` reads a snippet from stdin.
- Write the note in plain words from the conversation: what they were doing and what puzzled
  them. Add 1-3 short tags. Only add `--here` (records repo, branch and folder) if they want it.
- Koro is a personal database: save the idea, a public link or a short description, never
  proprietary code or internal documents from their job.
- Items are numbered (`#12`) and move inbox → planned → learning → done (or archived):
  `koro saved 12` shows one, `koro saved done 12`, `koro saved planned 12 13`,
  `koro saved edit 12 --note "..." --tag x --untag y`, `koro saved open 12` opens the link or file.
- "What did I save?": `koro saved --json`, then summarize by theme rather than reading the list
  out. If they ask what to learn next from it, suggest an order with a reason for each and mark
  the ones they pick `planned`. (Building a full curriculum from saved items comes later.)

## Adding a source

`koro add <file.pdf> --json` deduplicates (same file or same ISBN/arXiv id returns the
existing book), builds the outline from the PDF's bookmarks, remembers where the file is on
this machine, and stores the complete source privately: the PDF, every section's text and
every figure (a private bucket only the learner's key can read). Then offer to parse it.
For a book added before figures existed, or if `koro teach` shows figures that aren't
stored: `koro ingest assets --pdf <path> --json` (re-running only uploads what's missing).

## Parsing a source

Parsing turns each outline section into lessons. You do it, one section at a time; it's
resumable from any machine and several machines can work in parallel.

1. Read the payload spec once: the `payload_spec` path in the `ingest next` output.
2. Loop:
   - `koro ingest next --json` claims the next section and returns its `source_text`
     (or a note to pass `--pdf <path>` / read the pages yourself) and its `figures`. Put each
     figure's label in the `figures` list of the beat it illustrates.
   - Write the payload JSON to a temp file, in your own words (no 10-word run may match the
     source; the check enforces it).
   - `koro ingest check <file> --node <key> --json`, fix errors, then
     `koro ingest save <key> <file> --json`.
   - Can't finish a section? `koro ingest release <key>` so another session can take it.
   - Stop when `ingest next` returns `"done": true`.
3. Optionally add cross-chapter prerequisites as `[{"from": "<key>", "to": "<earlier key>",
   "confidence": 0.8}]` with `koro ingest link <file>`.
4. `koro ingest finalize --json` resolves links, breaks dependency cycles, and marks it ready.

## Fallback: database tools instead of the CLI

Every CLI command is one Postgres function. If the CLI can't run but a Supabase MCP server
for the koro project is connected, call the same functions with SQL, e.g.
`select koro_learners();` then `select koro_continue('<learner uuid>', 'plugin');`,
`select koro_teach('<learner>', '<concept key>', null, 'plugin');`,
`select koro_assess(p_learner => '<learner>', p_concept_ref => '<key>', p_verdict => 'partial', p_objective_key => '<obj>', p_answer => '...', p_feedback => '...');`.
Function names and parameters match the CLI (`koro_next_concept`, `koro_status`,
`koro_toc`, `koro_search`, `koro_mark_known`, `koro_dispute`, `koro_set_summary`,
`koro_figure`, `koro_figures`, `koro_read_section`, `koro_save_item`, `koro_saved_items`,
`koro_update_saved_item`, ...). Figure images live in the private
Storage bucket `koro-private`, so without the CLI describe figures from their captions.
