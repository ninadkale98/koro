// Koro MCP server: a remote MCP endpoint (Streamable HTTP, stateless JSON
// responses) that lets Claude teach from koro. Transport-agnostic: it works on
// web-standard Request/Response, so it runs on Supabase Edge Functions (Deno)
// and in Node for tests. No dependencies.

export type Rpc = (fn: string, params: Record<string, unknown>) => Promise<any>;
export type Auth = { learner_id: string; learner?: string; token_id?: string; label?: string };
// Private Storage bucket access (figures, the original file). Optional: without it,
// lessons still work and figures are described from their captions.
export type Storage = {
  sign: (bucket: string, path: string, expiresIn?: number) => Promise<string>;
  download: (bucket: string, path: string) => Promise<Uint8Array>;
};
export type Deps = {
  rpc: Rpc;
  checkToken: (token: string) => Promise<Auth | null>;
  storage?: Storage;
  functionName?: string;
  version?: string;
  now?: () => Date;
};

export const VERSION = "0.3.0";
const LINK_SECONDS = 3600; // signed figure links last an hour; show_figure makes a fresh one
const SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CLIENT = "connector";

export const INSTRUCTIONS = `Koro is the learner's personal teacher for books, papers and repos. It stores each source's lesson plan and every piece of evidence about what the learner knows. You are the teacher: koro never writes lessons on the fly.

Session loop
1. Start with continue_learning (or start_book when they name a book or section). In 2-3 lines tell them where they stand and what is next and why (reason: resume = shown but never checked; gap = an earlier miss; fading = due for review; next; prerequisite; fallback; complete).
2. If reason is prerequisite, name the missing concept and offer to learn it first (recommended) or go ahead anyway.
3. get_lesson(concept_key). Open with the hook, then teach ONE beat at a time in your own words: short, concrete, about a minute of reading. Show a beat's diagram in a code block and use its example. Pause after each beat so they can steer. Aim straight at any objective marked gap or with a misconception. Point to the source pages for the original; never claim to quote the book.
   Figures: when a beat lists figures (e.g. "figures": ["2.13"]), call show_figure for each at that beat. You will see the book's figure: walk the learner through what it shows in your own words and give them the figure's link so they can open the image (links last an hour; call show_figure again for a fresh one). The lesson's figures list has every figure for the concept.
   Grounding: when the learner asks what the book says exactly, or you are unsure of a detail, call read_section for the section's original text and answer from it. Paraphrase; quote at most a sentence or two, and cite the pages.
4. Check every objective: ask the lesson's checks one at a time and wait for the answer. Judge it against answer_guide as correct, partial or wrong, honestly (never round up). Call record_answer right away with the question, their answer condensed, your feedback, and a misconception when a specific wrong belief showed. Give a sentence or two of feedback. After partial or wrong, re-explain from a different angle and ask a fresh variant; record that too.
5. For modality code or exercise, offer the exercise, judge it against solution_sketch, record with kind exercise.
6. next_concept and repeat. Every few concepts and at the end of the session, call save_learner_summary with 2-3 sentences on where they stand.

Saving for later: while the learner is working or chatting, they may want to keep something to learn properly later ("save this to koro", "I want to learn X later", a link or paper). Call save_for_later right away with a clear title, the URL if there is one, a one-line note on why it came up, and 1-3 short tags; confirm in one line with its number (#12) and carry on. "What did I save?" -> list_saved, then summarize by theme instead of reading the list out. Move items along with update_saved (planned, learning, done, archived). Save ideas and public links, not proprietary code or internal documents.

When the learner says: "I already know this" -> mark_known, then offer a one-question confirm. "My answer was right" -> dispute_verdict (assessment_id comes from record_answer). "Jump to 5.3" -> start_book(section "5.3"). "Where is X covered?" -> search_book. "How am I doing?" -> progress_report. Pause, finish or drop a book -> set_book_status. Start over -> confirm first, then restart_book(confirm true). Goals -> set_goal. Time studied -> study_time.
In voice or on mobile keep beats even shorter and ask one question at a time.`;

const PROTOCOL_SHORT =
  "Teach one beat at a time from get_lesson (hook, then beats, pausing after each). Then ask each check, " +
  "judge against answer_guide (correct/partial/wrong, honestly), and call record_answer after every answer. " +
  "Then next_concept. Save a short save_learner_summary every few concepts.";

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------
class ToolError extends Error {}

// A tool result that is already MCP content (e.g. an image), not JSON to serialize.
class Content {
  content: unknown[];
  constructor(content: unknown[]) {
    this.content = content;
  }
}

type Ctx = { rpc: Rpc; learner: string; now: () => Date; storage?: Storage };
type Schema = { type: "object"; properties: Record<string, any>; required?: string[]; additionalProperties?: boolean };
type Tool = {
  name: string;
  title: string;
  description: string;
  inputSchema: Schema;
  annotations?: Record<string, boolean | string>;
  run: (ctx: Ctx, args: Record<string, any>) => Promise<unknown>;
};

const URL_RE = /^https?:\/\/\S+$/i;
const TAGS = { type: "array", items: { type: "string" }, description: "1-3 short topic tags, e.g. [\"gpu\", \"kv-cache\"]." };
const SAVED_STATUS = ["inbox", "planned", "learning", "done", "archived"];

const BOOK = { type: "string", description: "Book, paper or repo: title (or part of it) or id. Defaults to the most recent one." };
const SECTION = { type: "string", description: "Section key from the table of contents, e.g. \"5.3\" or \"2.4.1\"." };
const CONCEPT = { type: "string", description: "Concept key, e.g. \"5.3.1/prefix-caching\" (from next_concept, search_book or a lesson)." };

async function resolveBook(ctx: Ctx, book?: string, required = true): Promise<string | null> {
  if (book) return UUID.test(book) ? book : await ctx.rpc("koro_resolve_entity", { p_ref: book });
  const lib: any[] = (await ctx.rpc("koro_library", { p_learner: ctx.learner })) ?? [];
  const pick = lib.find((e) => e.enrollment === "active") ?? lib[0];
  if (!pick) {
    if (required) throw new ToolError("The library is empty. Add a source from a terminal with `koro add <file.pdf>`.");
    return null;
  }
  return pick.entity_id;
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const NOT_UPLOADED =
  "The image for this figure isn't stored yet. Describe it from its caption, and tell the learner to run " +
  "`koro ingest assets --pdf <path to the book>` once on their computer to add every figure.";

// Add a short-lived link to each stored figure (best effort: a figure without one is still described).
async function withLinks(ctx: Ctx, figures: any[] | null | undefined): Promise<any[]> {
  const list = Array.isArray(figures) ? figures : [];
  return Promise.all(list.map(async (f) => {
    const { asset_id: _a, bucket: _b, storage_path: _s, bbox: _x, width: _w, height: _h, ...rest } = f ?? {};
    let url: string | null = null;
    if (f?.uploaded && ctx.storage) {
      try {
        url = await ctx.storage.sign(f.bucket, f.storage_path, LINK_SECONDS);
      } catch {
        url = null;
      }
    }
    return { ...rest, url };
  }));
}

function shapeStart(r: any) {
  if (!r || r.none) {
    return { none: true, message: "No book in progress. Offer one from the library with start_book.", library: r?.library ?? [] };
  }
  const st = r.status ?? {};
  return {
    book: st.entity,
    progress: st.rollup,
    where_you_stand: st.enrollment?.knowledge_summary ?? null,
    focus_section: st.enrollment?.current_node ? `${st.enrollment.current_node} ${st.enrollment.current_node_title ?? ""}`.trim() : null,
    goals: { target_date: st.enrollment?.target_date ?? null, daily_minutes: st.enrollment?.daily_minutes ?? null },
    open_gaps: (st.gaps ?? []).slice(0, 5),
    fading: (st.fading ?? []).slice(0, 5),
    this_week: st.this_week?.[0] ? { minutes: st.this_week[0].minutes, sittings: st.this_week[0].sittings } : null,
    next: r.next,
    teaching_protocol: PROTOCOL_SHORT,
  };
}

export const TOOLS: Tool[] = [
  {
    name: "continue_learning",
    title: "Continue learning",
    description: "Pick up whatever the learner was last studying: returns the book, overall progress, where they stand, open gaps, and the next concept to teach with the reason. Call this first in a learning session.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    run: async (ctx) => shapeStart(await ctx.rpc("koro_continue", { p_learner: ctx.learner, p_client: CLIENT })),
  },
  {
    name: "start_book",
    title: "Start a book or section",
    description: "Start or resume a specific book, optionally focusing on one section (the focus persists until changed). Returns progress and the next concept.",
    inputSchema: { type: "object", properties: { book: BOOK, section: SECTION }, additionalProperties: false },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return shapeStart(await ctx.rpc("koro_start", { p_learner: ctx.learner, p_entity: entity, p_node_key: a.section, p_client: CLIENT }));
    },
  },
  {
    name: "next_concept",
    title: "Next concept",
    description: "What to teach next and why (resume, gap, fading, next, prerequisite, fallback, complete), optionally limited to a section.",
    inputSchema: { type: "object", properties: { book: BOOK, section: SECTION }, additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_next_concept", { p_learner: ctx.learner, p_entity: entity, p_node_key: a.section });
    },
  },
  {
    name: "get_lesson",
    title: "Get a lesson",
    description: "The full lesson for a concept: hook, beats (explanation, diagram, example, and the book figures to show at that beat), checks with answer guides, optional exercise, takeaway, source pages, the concept's figures with links, plus the learner's objectives (with gaps and misconceptions), prerequisites and recent answers. Marks the concept as seen. Teach it one beat at a time.",
    inputSchema: { type: "object", properties: { concept_key: CONCEPT, book: BOOK }, required: ["concept_key"], additionalProperties: false },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book, false);
      const r = await ctx.rpc("koro_teach", { p_learner: ctx.learner, p_concept_ref: a.concept_key, p_entity: entity, p_client: CLIENT });
      const figures = await withLinks(ctx, r?.figures);
      return {
        ...r,
        figures,
        how_to_teach: PROTOCOL_SHORT + (figures.length ? " At a beat that lists figures, call show_figure(label) and walk the learner through it." : ""),
      };
    },
  },
  {
    name: "show_figure",
    title: "Show a figure",
    description: "Show one of the book's figures (e.g. \"2.13\") at the beat that uses it. Returns the image, so you can see it, plus its caption, page and a link the learner can open (valid for an hour). Explain the figure in your own words.",
    inputSchema: {
      type: "object",
      properties: { label: { type: "string", description: "Figure label, e.g. \"2.13\" (from a beat's figures or the lesson's figures)." }, book: BOOK },
      required: ["label"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      const f = await ctx.rpc("koro_figure", { p_entity: entity, p_label: a.label });
      const [info] = await withLinks(ctx, [f]);
      const meta = {
        figure: `Figure ${f.label}`, caption: f.caption, page: f.page, book: f.entity?.title,
        concept_key: f.concept_key, section: f.node_key, url: info.url,
        note: info.url
          ? "Give the learner this link to open the figure. Describe it in your own words; don't reproduce it."
          : f.uploaded ? "The image is stored but a link couldn't be made right now; describe it from the caption." : NOT_UPLOADED,
      };
      if (!f.uploaded || !ctx.storage) return meta;
      try {
        const bytes = await ctx.storage.download(f.bucket, f.storage_path);
        return new Content([
          { type: "image", data: toBase64(bytes), mimeType: "image/png" },
          { type: "text", text: JSON.stringify(meta) },
        ]);
      } catch {
        return meta;
      }
    },
  },
  {
    name: "read_section",
    title: "Read a section",
    description: "The original text of a section of the book (private to the learner), with its subsections, figures and concepts. Use it to ground answers in what the book actually says. Paraphrase; quote at most a sentence or two and cite the pages.",
    inputSchema: { type: "object", properties: { section: SECTION, book: BOOK }, required: ["section"], additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      const r = await ctx.rpc("koro_read_section", { p_entity: entity, p_node_key: a.section });
      return {
        ...r,
        figures: await withLinks(ctx, r?.figures),
        text: r?.text ?? null,
        note: r?.text
          ? "Original text for grounding. Teach in your own words; quote at most a sentence or two."
          : "The section's text isn't stored yet (the learner can run `koro ingest assets --pdf <path>` once). Use the section summary and its concepts' lessons.",
      };
    },
  },
  {
    name: "record_answer",
    title: "Record an answer",
    description: "Record your judgment of the learner's answer to a check (or exercise) as evidence. Call after every answer. Returns each objective's new status; the concept is mastered when all objectives are strengths. The returned assessment_id can be disputed later.",
    inputSchema: {
      type: "object",
      properties: {
        concept_key: CONCEPT,
        objective_key: { type: "string", description: "The objective the check tests (the check's \"objective\" field)." },
        verdict: { type: "string", enum: ["correct", "partial", "wrong"] },
        question: { type: "string", description: "The question you asked." },
        answer: { type: "string", description: "The learner's answer, condensed." },
        feedback: { type: "string", description: "What was right and what was missing." },
        misconception: { type: "string", description: "A specific wrong belief the answer revealed, if any." },
        kind: { type: "string", enum: ["question", "exercise", "placement"], description: "Default question." },
        book: BOOK,
      },
      required: ["concept_key", "verdict"],
      additionalProperties: false,
    },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book, false);
      return ctx.rpc("koro_assess", {
        p_learner: ctx.learner, p_concept_ref: a.concept_key, p_verdict: a.verdict, p_objective_key: a.objective_key,
        p_question: a.question, p_answer: a.answer, p_feedback: a.feedback, p_misconception: a.misconception,
        p_kind: a.kind ?? "question", p_entity: entity, p_client: CLIENT,
      });
    },
  },
  {
    name: "dispute_verdict",
    title: "Dispute a verdict",
    description: "Correct an earlier verdict the learner disagrees with. The original stays in the history; progress is recalculated with the correction.",
    inputSchema: {
      type: "object",
      properties: {
        assessment_id: { type: "string", description: "From record_answer, or recent_evidence in a lesson." },
        verdict: { type: "string", enum: ["correct", "partial", "wrong"] },
        reason: { type: "string" },
      },
      required: ["assessment_id", "verdict", "reason"],
      additionalProperties: false,
    },
    run: (ctx, a) => ctx.rpc("koro_dispute", { p_learner: ctx.learner, p_assessment: a.assessment_id, p_verdict: a.verdict, p_reason: a.reason }),
  },
  {
    name: "mark_known",
    title: "Mark as already known",
    description: "The learner says they already know a concept or a whole section. Records it as lower-confidence evidence; offer a one-question confirm afterwards.",
    inputSchema: {
      type: "object",
      properties: { ref: { type: "string", description: "Section key (e.g. \"2.1\") or concept key." }, note: { type: "string" }, book: BOOK },
      required: ["ref"],
      additionalProperties: false,
    },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_mark_known", { p_learner: ctx.learner, p_entity: entity, p_ref: a.ref, p_note: a.note, p_client: CLIENT });
    },
  },
  {
    name: "table_of_contents",
    title: "Table of contents",
    description: "The book's outline with the learner's progress per section (mastered/total concepts). Depth 0 = chapters, 1 = sections (default), 2+ = subsections.",
    inputSchema: {
      type: "object",
      properties: {
        book: BOOK,
        depth: { type: "integer", minimum: 0, maximum: 6, description: "Default 1." },
        include_summaries: { type: "boolean", description: "Include each section's 2-4 sentence summary. Default false." },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      const r = await ctx.rpc("koro_toc", { p_learner: ctx.learner, p_entity: entity, p_max_depth: a.depth ?? 1 });
      return {
        book: r.entity,
        current_section: r.current_node,
        sections: (r.nodes ?? []).map((n: any) => ({
          key: n.key,
          title: n.title,
          depth: n.depth,
          pages: `${n.page_start}-${n.page_end}`,
          status: n.ingest_status !== "parsed" ? "not parsed yet" : !n.teachable || !n.progress ? "reference only" : n.progress.status,
          mastered: n.progress ? `${n.progress.mastered}/${n.progress.concepts}` : undefined,
          summary: a.include_summaries ? n.summary : undefined,
        })),
      };
    },
  },
  {
    name: "progress_report",
    title: "Progress report",
    description: "How the learner is doing: mastered/learning/untouched counts, open gaps with misconceptions, recently mastered, fading concepts due for review, goals, and study time this week. Optionally for one section.",
    inputSchema: { type: "object", properties: { book: BOOK, section: SECTION }, additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_status", { p_learner: ctx.learner, p_entity: entity, p_node_key: a.section });
    },
  },
  {
    name: "search_book",
    title: "Search",
    description: "Find where a topic is covered: matching sections and concepts with the learner's status. Searches every book unless one is given.",
    inputSchema: { type: "object", properties: { query: { type: "string" }, book: BOOK }, required: ["query"], additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const entity = a.book ? await resolveBook(ctx, a.book) : null;
      return ctx.rpc("koro_search", { p_learner: ctx.learner, p_query: a.query, p_entity: entity });
    },
  },
  {
    name: "list_library",
    title: "Library",
    description: "Every book, paper and repo in koro with the learner's status and progress.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: (ctx) => ctx.rpc("koro_library", { p_learner: ctx.learner }),
  },
  {
    name: "set_book_status",
    title: "Pause, finish or drop a book",
    description: "Set a book's status: active (resume), paused, completed or dropped. History is always kept.",
    inputSchema: {
      type: "object",
      properties: { status: { type: "string", enum: ["active", "paused", "completed", "dropped"] }, book: BOOK },
      required: ["status"],
      additionalProperties: false,
    },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_set_status", { p_learner: ctx.learner, p_entity: entity, p_status: a.status });
    },
  },
  {
    name: "restart_book",
    title: "Restart a book",
    description: "Start a book over: progress resets, history is kept. Confirm with the learner first, then call with confirm true.",
    inputSchema: { type: "object", properties: { confirm: { type: "boolean" }, book: BOOK }, required: ["confirm"], additionalProperties: false },
    annotations: { destructiveHint: true },
    run: async (ctx, a) => {
      if (a.confirm !== true) throw new ToolError("Ask the learner to confirm the restart, then call again with confirm: true.");
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_restart", { p_learner: ctx.learner, p_entity: entity });
    },
  },
  {
    name: "set_goal",
    title: "Set a goal",
    description: "Set a target finish date and/or daily study minutes for a book. Omitted fields keep their current value.",
    inputSchema: {
      type: "object",
      properties: {
        target_date: { type: "string", description: "YYYY-MM-DD" },
        daily_minutes: { type: "integer", minimum: 1, maximum: 600 },
        book: BOOK,
      },
      additionalProperties: false,
    },
    run: async (ctx, a) => {
      if (a.target_date && !/^\d{4}-\d{2}-\d{2}$/.test(a.target_date)) throw new ToolError("target_date must be YYYY-MM-DD");
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_set_goal", { p_learner: ctx.learner, p_entity: entity, p_target_date: a.target_date, p_daily_minutes: a.daily_minutes });
    },
  },
  {
    name: "save_learner_summary",
    title: "Save where the learner stands",
    description: "Save a 2-3 sentence narrative of the learner's strengths, weak spots and position in the book. Shown at the start of the next session.",
    inputSchema: { type: "object", properties: { summary: { type: "string" }, book: BOOK }, required: ["summary"], additionalProperties: false },
    run: async (ctx, a) => {
      const entity = await resolveBook(ctx, a.book);
      return ctx.rpc("koro_set_summary", { p_learner: ctx.learner, p_entity: entity, p_summary: a.summary });
    },
  },
  {
    name: "study_time",
    title: "Study time",
    description: "Minutes studied, sittings and concepts touched per book over the last N days, against goals.",
    inputSchema: { type: "object", properties: { days: { type: "integer", minimum: 1, maximum: 365, description: "Default 7." } }, additionalProperties: false },
    annotations: { readOnlyHint: true },
    run: (ctx, a) => {
      const since = new Date(ctx.now().getTime() - (a.days ?? 7) * 86400000).toISOString();
      return ctx.rpc("koro_study_time", { p_learner: ctx.learner, p_since: since });
    },
  },
  {
    name: "save_for_later",
    title: "Save something to learn later",
    description: "Save a topic, question, link or paper the learner wants to learn properly later (\"save this to koro\", \"I want to learn X later\"). Give a clear title, the URL if there is one, a one-line note on why it came up, and 1-3 tags. Saving the same link again updates the existing item. Returns the item with its number (#12).",
    inputSchema: {
      type: "object",
      properties: {
        what: { type: "string", description: "The topic, question or URL to save." },
        title: { type: "string", description: "Short title. For a link, the page or paper title." },
        note: { type: "string", description: "Why it caught their eye, what they were doing." },
        tags: TAGS,
        kind: { type: "string", enum: ["topic", "question", "note", "url", "pdf"], description: "Usually inferred." },
      },
      required: ["what"],
      additionalProperties: false,
    },
    run: (ctx, a) => {
      const what = String(a.what).trim();
      const isUrl = URL_RE.test(what);
      const kind = a.kind ?? (isUrl ? (/\.pdf([?#]|$)/i.test(what) ? "pdf" : "url") : undefined);
      // A titled topic keeps its full wording in the note.
      const extra = !isUrl && a.title && a.title.trim() !== what ? what : undefined;
      return ctx.rpc("koro_save_item", {
        p_learner: ctx.learner, p_title: a.title ?? (isUrl ? undefined : what), p_kind: kind,
        p_body: [extra, a.note].filter(Boolean).join("\n\n") || undefined, p_url: isUrl ? what : undefined,
        p_tags: a.tags ?? [], p_context: {}, p_client: CLIENT,
      });
    },
  },
  {
    name: "list_saved",
    title: "Things saved to learn",
    description: "What the learner saved to learn later: topics, questions, links, papers, with notes, tags and status (inbox, planned, learning, done, archived). Defaults to open items; filter by status, tag or search words.",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", enum: ["open", "all", ...SAVED_STATUS], description: "Default open (inbox, planned, learning)." },
        tag: { type: "string" },
        search: { type: "string", description: "Words to find in titles, notes, links and tags." },
        limit: { type: "integer", minimum: 1, maximum: 200, description: "Default 50." },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    run: async (ctx, a) => {
      const r = await ctx.rpc("koro_saved_items", {
        p_learner: ctx.learner, p_status: a.status ?? "open", p_tag: a.tag, p_query: a.search, p_limit: a.limit ?? 50,
      });
      const items = await Promise.all((r?.items ?? []).map(async (s: any) => {
        let file_link: string | null = null;
        if (s.file?.storage_path && ctx.storage) {
          try {
            file_link = await ctx.storage.sign(s.file.bucket ?? "koro-private", s.file.storage_path, LINK_SECONDS);
          } catch {
            file_link = null;
          }
        }
        return {
          no: s.no, title: s.title, kind: s.kind, status: s.status, tags: s.tags ?? [], note: s.body ?? null,
          url: s.url ?? null, file: s.file?.filename ?? null, file_link, saved: s.created_at,
          from: s.context?.client ?? null,
        };
      }));
      return { items, counts: r?.counts ?? {}, tags: r?.tags ?? [] };
    },
  },
  {
    name: "update_saved",
    title: "Update a saved item",
    description: "Move a saved item along (planned, learning, done, archived, or back to inbox), retitle it, add a note, or change its tags.",
    inputSchema: {
      type: "object",
      properties: {
        item: { type: "integer", minimum: 1, description: "The item number, e.g. 12 for #12." },
        status: { type: "string", enum: SAVED_STATUS },
        title: { type: "string" },
        note: { type: "string", description: "Appended to the item's notes." },
        add_tags: TAGS,
        remove_tags: { type: "array", items: { type: "string" } },
      },
      required: ["item"],
      additionalProperties: false,
    },
    run: (ctx, a) => ctx.rpc("koro_update_saved_item", {
      p_learner: ctx.learner, p_ref: String(a.item), p_status: a.status, p_title: a.title, p_note: a.note,
      p_add_tags: a.add_tags, p_remove_tags: a.remove_tags,
    }),
  },
];

const TOOL_BY_NAME = new Map(TOOLS.map((t) => [t.name, t]));

export function validateArgs(schema: Schema, args: Record<string, any>): string | null {
  for (const key of schema.required ?? []) {
    if (args[key] === undefined || args[key] === null || args[key] === "") return `missing required argument: ${key}`;
  }
  for (const [key, value] of Object.entries(args)) {
    const spec = schema.properties[key];
    if (!spec) {
      if (schema.additionalProperties === false) return `unknown argument: ${key}`;
      continue;
    }
    if (value === undefined || value === null) continue;
    if (spec.type === "string" && typeof value !== "string") return `${key} must be a string`;
    if (spec.type === "boolean" && typeof value !== "boolean") return `${key} must be true or false`;
    if (spec.type === "integer" && !Number.isInteger(value)) return `${key} must be an integer`;
    if (spec.type === "array") {
      if (!Array.isArray(value)) return `${key} must be a list`;
      if (spec.items?.type === "string" && value.some((v: unknown) => typeof v !== "string")) return `${key} must be a list of strings`;
    }
    if (spec.enum && !spec.enum.includes(value)) return `${key} must be one of: ${spec.enum.join(", ")}`;
    if (typeof spec.minimum === "number" && value < spec.minimum) return `${key} must be at least ${spec.minimum}`;
    if (typeof spec.maximum === "number" && value > spec.maximum) return `${key} must be at most ${spec.maximum}`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Prompts (shown in Claude's prompt menu)
// ---------------------------------------------------------------------------
type Prompt = { name: string; title: string; description: string; arguments?: { name: string; description: string; required?: boolean }[]; text: (a: Record<string, string>) => string };

export const PROMPTS: Prompt[] = [
  {
    name: "continue",
    title: "Continue learning",
    description: "Pick up where you left off and keep learning.",
    text: () => "Continue my koro learning session: call continue_learning, tell me briefly where I stand, then teach me the next concept following the koro teaching protocol.",
  },
  {
    name: "study_section",
    title: "Study a section",
    description: "Focus on one section or topic.",
    arguments: [
      { name: "section", description: "Section key (e.g. 5.3) or a topic", required: true },
      { name: "book", description: "Book title (optional)" },
    ],
    text: (a) => `I want to study ${a.section}${a.book ? ` in ${a.book}` : ""} with koro. If it looks like a section key, call start_book with that section; otherwise use search_book to find it first. Then teach it following the koro teaching protocol.`,
  },
  {
    name: "quiz_me",
    title: "Quiz me",
    description: "Review gaps and fading concepts with questions.",
    arguments: [{ name: "topic", description: "Optional topic or section to focus on" }],
    text: (a) => `Quiz me with koro${a.topic ? ` on ${a.topic}` : ""}: use progress_report to find my open gaps and fading concepts (search_book for a topic), get each lesson, ask its checks one at a time, and record every answer with record_answer.`,
  },
  {
    name: "saved",
    title: "What did I save?",
    description: "Things you saved to learn later, grouped by theme.",
    text: () => "What have I saved in koro to learn later? Call list_saved and summarize it by theme: what's there, what's been waiting longest, and which two or three you'd suggest next and why.",
  },
  {
    name: "progress",
    title: "How am I doing?",
    description: "Strengths, gaps, fading concepts and study time.",
    text: () => "How am I doing in koro? Call progress_report and study_time and give me a short, honest summary: what I've mastered, my gaps and misconceptions, what's fading, and whether I'm on track for my goals.",
  },
];

// ---------------------------------------------------------------------------
// JSON-RPC over Streamable HTTP
// ---------------------------------------------------------------------------
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version, Authorization",
};

function json(value: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...CORS, ...extra } });
}

const ok = (id: unknown, result: unknown) => ({ jsonrpc: "2.0", id, result });
const rpcError = (id: unknown, code: number, message: string) => ({ jsonrpc: "2.0", id: id ?? null, error: { code, message } });

export function extractToken(url: URL, functionName = "koro-mcp"): string | null {
  const segments = url.pathname.split("/").filter(Boolean);
  let i = 0;
  if (segments[i] === "functions" && segments[i + 1] === "v1") i += 2;
  if (segments[i] === functionName) i += 1;
  return segments[i] ?? url.searchParams.get("key");
}

async function callTool(deps: Deps, auth: Auth, params: any) {
  const tool = TOOL_BY_NAME.get(params?.name);
  if (!tool) return { error: { code: -32602, message: `Unknown tool: ${params?.name}` } };
  const args = params?.arguments ?? {};
  if (typeof args !== "object" || Array.isArray(args)) {
    return { result: { content: [{ type: "text", text: "arguments must be an object" }], isError: true } };
  }
  const problem = validateArgs(tool.inputSchema, args);
  if (problem) return { result: { content: [{ type: "text", text: problem }], isError: true } };
  try {
    const ctx: Ctx = { rpc: deps.rpc, learner: auth.learner_id, now: deps.now ?? (() => new Date()), storage: deps.storage };
    const value = await tool.run(ctx, args);
    if (value instanceof Content) return { result: { content: value.content } };
    return { result: { content: [{ type: "text", text: JSON.stringify(value ?? null) }] } };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return { result: { content: [{ type: "text", text: `koro error: ${message}` }], isError: true } };
  }
}

async function handleMessage(deps: Deps, auth: Auth, m: any): Promise<unknown | null> {
  if (!m || typeof m !== "object" || Array.isArray(m) || m.jsonrpc !== "2.0") {
    return rpcError(m?.id, -32600, "Invalid Request");
  }
  if (typeof m.method !== "string") return null; // a response sent by the client: nothing to answer
  if (!("id" in m) || m.id === undefined) return null; // notification
  const id = m.id;
  const params = m.params ?? {};
  try {
    switch (m.method) {
      case "initialize": {
        const requested = params.protocolVersion;
        return ok(id, {
          protocolVersion: SUPPORTED_VERSIONS.includes(requested) ? requested : SUPPORTED_VERSIONS[0],
          capabilities: { tools: { listChanged: false }, prompts: { listChanged: false } },
          serverInfo: { name: "koro", title: "Koro", version: deps.version ?? VERSION },
          instructions: INSTRUCTIONS,
        });
      }
      case "ping":
        return ok(id, {});
      case "tools/list":
        return ok(id, {
          tools: TOOLS.map(({ name, title, description, inputSchema, annotations }) => ({
            name, title, description, inputSchema, ...(annotations ? { annotations: { title, ...annotations } } : {}),
          })),
        });
      case "tools/call": {
        const r = await callTool(deps, auth, params);
        return "error" in r ? rpcError(id, r.error!.code, r.error!.message) : ok(id, r.result);
      }
      case "prompts/list":
        return ok(id, { prompts: PROMPTS.map(({ name, title, description, arguments: args }) => ({ name, title, description, ...(args ? { arguments: args } : {}) })) });
      case "prompts/get": {
        const p = PROMPTS.find((x) => x.name === params.name);
        if (!p) return rpcError(id, -32602, `Unknown prompt: ${params.name}`);
        const a = params.arguments ?? {};
        for (const arg of p.arguments ?? []) if (arg.required && !a[arg.name]) return rpcError(id, -32602, `Missing argument: ${arg.name}`);
        return ok(id, { description: p.description, messages: [{ role: "user", content: { type: "text", text: p.text(a) } }] });
      }
      case "resources/list":
        return ok(id, { resources: [] });
      case "resources/templates/list":
        return ok(id, { resourceTemplates: [] });
      default:
        return rpcError(id, -32601, `Method not found: ${m.method}`);
    }
  } catch (e) {
    return rpcError(id, -32603, e instanceof Error ? e.message : String(e));
  }
}

async function status(deps: Deps, auth: Auth, handler: (r: Request) => Promise<Response>, url: URL) {
  const out: Record<string, unknown> = {
    name: "koro",
    version: deps.version ?? VERSION,
    learner: auth.learner ?? auth.learner_id,
    usage: "Add this URL as a custom connector in Claude (Settings > Connectors). MCP clients POST JSON-RPC to it.",
  };
  try {
    const lib: any[] = (await deps.rpc("koro_library", { p_learner: auth.learner_id })) ?? [];
    out.library = lib.map((e) => ({
      title: e.title,
      status: e.enrollment ?? "not started",
      mastered: e.progress ? `${e.progress.mastered}/${e.progress.concepts}` : null,
    }));
    out.database = "ok";
  } catch (e) {
    out.database = `error: ${e instanceof Error ? e.message : String(e)}`;
  }
  if (url.searchParams.get("selftest") === "1") {
    // Exercise the MCP path end to end (read-only calls) on this runtime.
    const post = async (body: unknown) => {
      const res = await handler(new Request(url.toString(), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
        body: JSON.stringify(body),
      }));
      return { status: res.status, body: res.status === 202 ? null : await res.json() };
    };
    const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "selftest", version: "0" } } });
    const note = await post({ jsonrpc: "2.0", method: "notifications/initialized" });
    const list = await post({ jsonrpc: "2.0", id: 2, method: "tools/list" });
    const next = await post({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "next_concept", arguments: {} } });
    const toc = await post({ jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "table_of_contents", arguments: { depth: 0 } } });
    const text = (r: any) => r.body?.result?.content?.[0]?.text ?? JSON.stringify(r.body?.error ?? r.body);
    out.selftest = {
      initialize: { status: init.status, protocolVersion: init.body?.result?.protocolVersion, has_instructions: !!init.body?.result?.instructions },
      initialized_notification: note.status,
      tools: list.body?.result?.tools?.length ?? 0,
      next_concept: { isError: !!next.body?.result?.isError, text: text(next).slice(0, 400) },
      table_of_contents: { isError: !!toc.body?.result?.isError, text: text(toc).slice(0, 400) },
    };
    const label = url.searchParams.get("figure");
    if (label) {
      // Checks Storage end to end without exposing the link: content types and sizes only.
      const fig = await post({ jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "show_figure", arguments: { label } } });
      const content: any[] = fig.body?.result?.content ?? [];
      const meta = content.find((c) => c.type === "text")?.text ?? "";
      let parsed: any = {};
      try {
        parsed = JSON.parse(meta);
      } catch {
        parsed = { text: meta.slice(0, 300) };
      }
      (out.selftest as Record<string, unknown>).show_figure = {
        isError: !!fig.body?.result?.isError,
        content: content.map((c) => (c.type === "image" ? `image ${c.mimeType} ${Math.round((c.data?.length ?? 0) * 0.75)} bytes` : c.type)),
        has_url: !!parsed.url,
        caption: parsed.caption ?? null,
        note: parsed.url ? undefined : parsed.note ?? parsed.text,
      };
    }
  }
  return out;
}

export function createHandler(deps: Deps): (req: Request) => Promise<Response> {
  const handler = async (req: Request): Promise<Response> => {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    const url = new URL(req.url);
    const token = extractToken(url, deps.functionName ?? "koro-mcp");
    let auth: Auth | null = null;
    if (token) {
      try {
        auth = await deps.checkToken(token);
      } catch {
        return json({ error: "koro could not check the token (database unreachable)" }, 503);
      }
    }
    if (!auth) return json({ error: "not found" }, 404);

    if (req.method === "GET") {
      if ((req.headers.get("accept") ?? "").includes("text/event-stream")) {
        return new Response("This server does not open an SSE stream; POST JSON-RPC instead.", { status: 405, headers: { Allow: "POST", ...CORS } });
      }
      return json(await status(deps, auth, handler, url));
    }
    if (req.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "GET, POST", ...CORS } });

    let body: any;
    try {
      body = await req.json();
    } catch {
      return json(rpcError(null, -32700, "Parse error"), 400);
    }
    const batch = Array.isArray(body);
    const messages = batch ? body : [body];
    if (messages.length === 0) return json(rpcError(null, -32600, "Invalid Request"), 400);
    const replies: unknown[] = [];
    for (const m of messages) {
      const reply = await handleMessage(deps, auth, m);
      if (reply) replies.push(reply);
    }
    if (replies.length === 0) return new Response(null, { status: 202, headers: CORS });
    return json(batch ? replies : replies[0]);
  };
  return handler;
}

// ---------------------------------------------------------------------------
// Database access over PostgREST, and token checks with a short cache
// ---------------------------------------------------------------------------
export function secretKeyFromEnv(get: (name: string) => string | undefined): string {
  const raw = get("SUPABASE_SECRET_KEYS");
  if (raw) {
    try {
      const keys = JSON.parse(raw);
      const key = keys?.default ?? Object.values(keys ?? {})[0];
      if (typeof key === "string" && key) return key;
    } catch {
      // fall through to the legacy key
    }
  }
  const legacy = get("SUPABASE_SERVICE_ROLE_KEY");
  if (legacy) return legacy;
  throw new Error("no secret key available (SUPABASE_SECRET_KEYS / SUPABASE_SERVICE_ROLE_KEY)");
}

export function makeRpc(baseUrl: string, key: string, fetchImpl: typeof fetch = fetch): Rpc {
  const headers: Record<string, string> = { apikey: key, "Content-Type": "application/json", Accept: "application/json" };
  if (key.startsWith("eyJ")) headers.Authorization = `Bearer ${key}`; // legacy JWT keys
  return async (fn, params) => {
    const body: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) body[k] = v;
    const res = await fetchImpl(`${baseUrl.replace(/\/$/, "")}/rest/v1/rpc/${fn}`, { method: "POST", headers, body: JSON.stringify(body) });
    const text = await res.text();
    if (!res.ok) {
      let message = text;
      try {
        const err = JSON.parse(text);
        message = err.message ?? text;
      } catch {
        // keep raw text
      }
      throw new Error(`${fn}: ${message}`);
    }
    return text ? JSON.parse(text) : null;
  };
}

export function makeStorage(baseUrl: string, key: string, fetchImpl: typeof fetch = fetch): Storage {
  const base = `${baseUrl.replace(/\/$/, "")}/storage/v1`;
  const auth: Record<string, string> = { apikey: key };
  if (key.startsWith("eyJ")) auth.Authorization = `Bearer ${key}`;
  const enc = (path: string) => path.split("/").map(encodeURIComponent).join("/");
  return {
    async sign(bucket, path, expiresIn = LINK_SECONDS) {
      const res = await fetchImpl(`${base}/object/sign/${bucket}/${enc(path)}`, {
        method: "POST", headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify({ expiresIn }),
      });
      const text = await res.text();
      if (!res.ok) throw new Error(`storage sign ${path}: ${res.status} ${text}`);
      const body = JSON.parse(text);
      const signed: string = body.signedURL ?? body.signedUrl;
      return signed.startsWith("http") ? signed : `${base}${signed}`;
    },
    async download(bucket, path) {
      const res = await fetchImpl(`${base}/object/${bucket}/${enc(path)}`, { headers: auth });
      if (!res.ok) throw new Error(`storage download ${path}: ${res.status} ${await res.text()}`);
      return new Uint8Array(await res.arrayBuffer());
    },
  };
}

export function cachedTokenCheck(rpc: Rpc, ttlMs = 60_000, now: () => number = Date.now) {
  const cache = new Map<string, { auth: Auth | null; until: number }>();
  return async (token: string): Promise<Auth | null> => {
    const hit = cache.get(token);
    if (hit && hit.until > now()) return hit.auth;
    const auth = (await rpc("koro_check_connector_token", { p_token: token })) as Auth | null;
    // Cache misses briefly too, so a wrong URL can't hammer the database.
    cache.set(token, { auth, until: now() + (auth ? ttlMs : 10_000) });
    if (cache.size > 1000) cache.clear();
    return auth;
  };
}
