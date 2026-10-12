# AGENTS.md: teaching with koro

These are the instructions for any coding agent (Codex, Claude Code, Cursor, ...) that can run shell
commands. Claude Code users can install the plugin instead (see README). The user's request takes
the place of the slash-command arguments below; the payload spec for parsing is in
docs/node-payload.md.

Koro stores what the learner is studying and everything they've shown they know. It never
calls a model: **you** are the teacher. You read lessons and progress from the `koro` CLI,
teach, judge answers, and write evidence back. Always pass `--json` and read the JSON.

## Route the request

- empty, `continue`, `resume` → `koro continue --json`
- `start <book> [section]` → `koro start [section] -e "<book>" --json`
- `status` → `koro status --json` · `toc` → `koro toc --json` · `search <topic>` → `koro search "<topic>" --json`
- `add <file>` → see "Adding a source" · `ingest` → see "Parsing a source"
- anything else ("teach me attention", "quiz me on chapter 3"): find it with `koro search`, then teach it.

If `koro` is missing, install it: `pip install git+https://github.com/ninadkale98/koro` then
`koro init`. If it is installed but can't reach the database, see "Fallback" at the end.

## The session loop

1. **Orient.** From `continue`/`start`, tell the learner in two or three lines where they stand:
   the book, mastered/total, their `knowledge_summary` if any, and what's next and why
   (`reason`). Reasons: `resume` (shown last time, never checked), `gap` (an earlier miss
   to revisit), `fading` (due for review), `next`, `prerequisite`, `fallback`, `complete`.
2. **Prerequisite gate.** If `reason` is `prerequisite`, say which concept they're missing and
   offer: learn it first (recommended), or go ahead anyway (`koro teach <wanted key>`).
3. **Teach.** `koro teach <concept key> --json` returns `teaching_content` (hook, beats,
   checks, optional exercise, takeaway), the learner's `objectives` with status and
   misconceptions, `prerequisites`, `recent_evidence`, and the source pages.
   - Open with the hook. Then teach **one beat at a time** in your own words: short, concrete,
     a minute of reading. Show a beat's `diagram` in a code block and use its `example`.
     Pause after each beat ("make sense?") so they can steer. Don't dump all beats at once.
   - If an objective is a `gap` or has a `misconception`, aim the explanation straight at it.
   - Point to the pages (`node.page_start`-`page_end`) for the original text. Never claim to
     quote the book; the lessons are paraphrased teaching notes.
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
| "How am I doing?" | `koro status --json`: strengths, gaps with misconceptions, fading, time this week |
| "Pause / I'm done with this book / drop it" | `koro pause` / `koro done` / `koro drop` |
| "Start over" | confirm first, then `koro restart --yes` (history is kept) |
| "I want to finish by Dec 1, 20 min a day" | `koro goal --by 2026-12-01 --daily 20` |
| "How much did I study?" | `koro time --days 7 --json` |

Several books: commands default to the most recent one; pass `-e "<title or id>"` otherwise.

## Adding a source

`koro add <file.pdf> --json` deduplicates (same file or same ISBN/arXiv id returns the
existing book), builds the outline from the PDF's bookmarks, and remembers where the file
is on this machine. Then offer to parse it.

## Parsing a source

Parsing turns each outline section into lessons. You do it, one section at a time; it's
resumable from any machine and several machines can work in parallel.

1. Read the payload spec once: the `payload_spec` path in the `ingest next` output.
2. Loop:
   - `koro ingest next --json` claims the next section and returns its `source_text`
     (or a note to pass `--pdf <path>` / read the pages yourself).
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
`koro_toc`, `koro_search`, `koro_mark_known`, `koro_dispute`, `koro_set_summary`, ...).
