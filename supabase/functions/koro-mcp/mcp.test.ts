// Run: node --experimental-strip-types --test supabase/functions/koro-mcp/mcp.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  INSTRUCTIONS, PROMPTS, TOOLS, cachedTokenCheck, createHandler, extractToken, makeRpc, secretKeyFromEnv, validateArgs,
} from "./mcp.ts";

const L = "fea6d85b-2166-458c-ba61-6bed113c1819";
const E = "d48ee9db-7099-48ec-a59e-3108bcd9434a";
const TOKEN = "koro_" + "a".repeat(48);
const BASE = `https://proj.supabase.co/functions/v1/koro-mcp/${TOKEN}`;

// Parameter names of the deployed koro_* functions (from pg_proc).
const SIGNATURES: Record<string, string[]> = {
  koro_continue: ["p_learner", "p_client"],
  koro_start: ["p_learner", "p_entity", "p_node_key", "p_client"],
  koro_next_concept: ["p_learner", "p_entity", "p_node_key"],
  koro_teach: ["p_learner", "p_concept_ref", "p_entity", "p_client"],
  koro_assess: ["p_learner", "p_concept_ref", "p_verdict", "p_objective_key", "p_question", "p_answer", "p_feedback",
    "p_misconception", "p_kind", "p_weight", "p_entity", "p_client"],
  koro_dispute: ["p_learner", "p_assessment", "p_verdict", "p_reason"],
  koro_mark_known: ["p_learner", "p_entity", "p_ref", "p_note", "p_client"],
  koro_toc: ["p_learner", "p_entity", "p_max_depth"],
  koro_status: ["p_learner", "p_entity", "p_node_key"],
  koro_search: ["p_learner", "p_query", "p_entity"],
  koro_library: ["p_learner"],
  koro_set_status: ["p_learner", "p_entity", "p_status"],
  koro_restart: ["p_learner", "p_entity"],
  koro_set_goal: ["p_learner", "p_entity", "p_target_date", "p_daily_minutes"],
  koro_set_summary: ["p_learner", "p_entity", "p_summary"],
  koro_study_time: ["p_learner", "p_since", "p_entity"],
  koro_resolve_entity: ["p_ref"],
  koro_check_connector_token: ["p_token"],
};

const ROLLUP = { concepts: 210, mastered: 3, learning: 1, seen: 0, untouched: 206, mastery: 0.02, status: "learning" };
const CARD = { key: "0/inference-stack-layers", name: "The three layers", status: "untouched" };
const START = {
  study_session_id: "s1",
  status: { entity: { id: E, title: "Inference Engineering" }, rollup: ROLLUP,
            enrollment: { knowledge_summary: "Solid on chapter 0.", current_node: null, target_date: null, daily_minutes: 20 },
            gaps: [{ concept_key: "1.1/x", objective: "Explain x" }], fading: [], this_week: [{ minutes: 12, sittings: 2 }] },
  next: { reason: "next", scope: "all", concept: CARD },
};
const CANNED: Record<string, unknown> = {
  koro_library: [{ entity_id: E, title: "Inference Engineering", enrollment: "active", progress: ROLLUP }],
  koro_resolve_entity: E,
  koro_continue: START,
  koro_start: START,
  koro_next_concept: { reason: "next", concept: CARD },
  koro_teach: { concept: { key: CARD.key, teaching_content: { hook: "h", beats: [], checks: [] } }, objectives: [] },
  koro_assess: { assessment_id: "a1", status: "learning", objectives: [] },
  koro_dispute: { dispute_id: "d1", status: "mastered" },
  koro_mark_known: { marked_known: 3 },
  koro_toc: { entity: { title: "Inference Engineering" }, current_node: null, nodes: [
    { key: "0", title: "Chapter 0", depth: 0, page_start: 17, page_end: 24, teachable: true, ingest_status: "parsed", summary: "s", progress: ROLLUP },
    { key: "preface", title: "Preface", depth: 0, page_start: 11, page_end: 16, teachable: false, ingest_status: "parsed", summary: "p", progress: null }] },
  koro_status: { rollup: ROLLUP },
  koro_search: { nodes: [], concepts: [CARD] },
  koro_set_status: { status: "paused" },
  koro_restart: { concepts_reset: 4 },
  koro_set_goal: { daily_minutes: 20 },
  koro_set_summary: { knowledge_summary: "x" },
  koro_study_time: [{ minutes: 30 }],
};

function setup(overrides: Record<string, unknown> = {}) {
  const calls: [string, Record<string, unknown>][] = [];
  const responses = { ...CANNED, ...overrides };
  const rpc = async (fn: string, params: Record<string, unknown>) => {
    const sig = SIGNATURES[fn];
    assert.ok(sig, `unexpected rpc ${fn}`);
    for (const k of Object.keys(params)) assert.ok(sig.includes(k), `${fn} has no parameter ${k}`);
    calls.push([fn, params]);
    const r = responses[fn];
    if (r instanceof Error) throw r;
    return r;
  };
  const checkToken = async (t: string) => (t === TOKEN ? { learner_id: L, learner: "Ninad" } : null);
  const handler = createHandler({ rpc, checkToken, now: () => new Date("2026-10-12T12:00:00Z") });
  const post = async (body: unknown, url = BASE) => {
    const res = await handler(new Request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
    return { status: res.status, body: res.status === 202 ? null : await res.json() };
  };
  const call = async (name: string, args: Record<string, unknown> = {}) => {
    const r = await post({ jsonrpc: "2.0", id: 7, method: "tools/call", params: { name, arguments: args } });
    assert.equal(r.status, 200);
    return r.body;
  };
  const last = (fn: string) => {
    for (let i = calls.length - 1; i >= 0; i--) if (calls[i][0] === fn) return calls[i][1];
    assert.fail(`${fn} not called; calls: ${calls.map((c) => c[0]).join(", ")}`);
  };
  return { handler, post, call, calls, last };
}

const payload = (body: any) => JSON.parse(body.result.content[0].text);

test("token in path or query; anything else is 404", async () => {
  assert.equal(extractToken(new URL(BASE)), TOKEN);
  assert.equal(extractToken(new URL(`https://x/koro-mcp/${TOKEN}`)), TOKEN);
  assert.equal(extractToken(new URL(`https://x/koro-mcp?key=${TOKEN}`)), TOKEN);
  const { handler, post } = setup();
  assert.equal((await handler(new Request("https://proj.supabase.co/functions/v1/koro-mcp", { method: "POST", body: "{}" }))).status, 404);
  assert.equal((await post({ jsonrpc: "2.0", id: 1, method: "ping" }, BASE + "x")).status, 404);
});

test("initialize negotiates the version and sends instructions", async () => {
  const { post } = setup();
  const r = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {} } });
  assert.equal(r.body.result.protocolVersion, "2025-03-26");
  assert.equal(r.body.result.serverInfo.name, "koro");
  assert.deepEqual(Object.keys(r.body.result.capabilities).sort(), ["prompts", "tools"]);
  assert.equal(r.body.result.instructions, INSTRUCTIONS);
  const r2 = await post({ jsonrpc: "2.0", id: 2, method: "initialize", params: { protocolVersion: "1999-01-01" } });
  assert.equal(r2.body.result.protocolVersion, "2025-06-18");
});

test("notifications get 202, pings and unknown methods answer", async () => {
  const { post } = setup();
  assert.equal((await post({ jsonrpc: "2.0", method: "notifications/initialized" })).status, 202);
  assert.deepEqual((await post({ jsonrpc: "2.0", id: 3, method: "ping" })).body.result, {});
  assert.equal((await post({ jsonrpc: "2.0", id: 4, method: "nope" })).body.error.code, -32601);
  assert.equal((await post({ id: 5, method: "ping" })).body.error.code, -32600);
});

test("batches and parse errors", async () => {
  const { post, handler } = setup();
  const r = await post([{ jsonrpc: "2.0", id: 1, method: "ping" }, { jsonrpc: "2.0", method: "notifications/initialized" },
                        { jsonrpc: "2.0", id: 2, method: "tools/list" }]);
  assert.equal(r.body.length, 2);
  const bad = await handler(new Request(BASE, { method: "POST", body: "{not json" }));
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).error.code, -32700);
});

test("tools/list: valid schemas, unique names, every tool reachable", async () => {
  const { post } = setup();
  const tools = (await post({ jsonrpc: "2.0", id: 1, method: "tools/list" })).body.result.tools;
  assert.equal(tools.length, TOOLS.length);
  assert.equal(new Set(tools.map((t: any) => t.name)).size, tools.length);
  for (const t of tools) {
    assert.match(t.name, /^[a-z_]{3,40}$/);
    assert.equal(t.inputSchema.type, "object");
    assert.ok(t.description.length > 30, t.name);
    for (const req of t.inputSchema.required ?? []) assert.ok(req in t.inputSchema.properties, `${t.name}.${req}`);
  }
  assert.equal(tools.find((t: any) => t.name === "list_library").annotations.readOnlyHint, true);
  assert.equal(tools.find((t: any) => t.name === "restart_book").annotations.destructiveHint, true);
});

test("continue_learning shapes the session start", async () => {
  const { call, last } = setup();
  const p = payload(await call("continue_learning"));
  assert.deepEqual(last("koro_continue"), { p_learner: L, p_client: "connector" });
  assert.equal(p.book.title, "Inference Engineering");
  assert.equal(p.where_you_stand, "Solid on chapter 0.");
  assert.equal(p.next.concept.key, CARD.key);
  assert.equal(p.open_gaps.length, 1);
  assert.match(p.teaching_protocol, /record_answer/);
});

test("continue_learning with nothing in progress", async () => {
  const { call } = setup({ koro_continue: { none: true, library: [{ title: "X" }] } });
  const p = payload(await call("continue_learning"));
  assert.equal(p.none, true);
  assert.equal(p.library[0].title, "X");
});

test("start_book resolves titles and passes the section", async () => {
  const { call, last } = setup();
  await call("start_book", { book: "Inference", section: "5.3" });
  assert.deepEqual(last("koro_resolve_entity"), { p_ref: "Inference" });
  assert.deepEqual(last("koro_start"), { p_learner: L, p_entity: E, p_node_key: "5.3", p_client: "connector" });
  await call("start_book", { book: E });
  assert.equal(last("koro_start").p_entity, E);
});

test("get_lesson defaults to the most recent active book", async () => {
  const { call, last } = setup();
  const p = payload(await call("get_lesson", { concept_key: "0/inference-stack-layers" }));
  assert.deepEqual(last("koro_teach"), { p_learner: L, p_concept_ref: "0/inference-stack-layers", p_entity: E, p_client: "connector" });
  assert.match(p.how_to_teach, /one beat at a time/);
});

test("record_answer sends evidence; bad input is a tool error, not an rpc", async () => {
  const { call, last, calls } = setup();
  const p = payload(await call("record_answer", { concept_key: "0/x", objective_key: "o1", verdict: "partial",
    question: "q", answer: "a", feedback: "f", misconception: "m" }));
  assert.equal(p.assessment_id, "a1");
  const sent = last("koro_assess");
  assert.equal(sent.p_verdict, "partial");
  assert.equal(sent.p_kind, "question");
  assert.equal(sent.p_misconception, "m");
  const n = calls.length;
  const bad = await call("record_answer", { concept_key: "0/x", verdict: "maybe" });
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /verdict must be one of/);
  const missing = await call("record_answer", { verdict: "correct" });
  assert.match(missing.result.content[0].text, /missing required argument: concept_key/);
  const extra = await call("record_answer", { concept_key: "0/x", verdict: "correct", bogus: 1 });
  assert.match(extra.result.content[0].text, /unknown argument: bogus/);
  assert.equal(calls.length, n);
});

test("database errors come back as readable tool errors", async () => {
  const { call } = setup({ koro_teach: new Error("koro_teach: unknown concept 9.9/x") });
  const r = await call("get_lesson", { concept_key: "9.9/x" });
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /unknown concept 9\.9\/x/);
});

test("unknown tool is a protocol error", async () => {
  const { post } = setup();
  const r = await post({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "nope", arguments: {} } });
  assert.equal(r.body.error.code, -32602);
});

test("restart needs confirm; every other tool calls the right function", async () => {
  const { call, last, calls } = setup();
  const n = calls.length;
  assert.equal((await call("restart_book", { confirm: false })).result.isError, true);
  assert.equal(calls.length, n);
  await call("restart_book", { confirm: true });
  assert.deepEqual(last("koro_restart"), { p_learner: L, p_entity: E });
  await call("dispute_verdict", { assessment_id: "a1", verdict: "correct", reason: "r" });
  assert.deepEqual(last("koro_dispute"), { p_learner: L, p_assessment: "a1", p_verdict: "correct", p_reason: "r" });
  await call("mark_known", { ref: "2.1", note: "work" });
  assert.equal(last("koro_mark_known").p_ref, "2.1");
  await call("next_concept", { section: "5.3" });
  assert.equal(last("koro_next_concept").p_node_key, "5.3");
  await call("progress_report", { section: "5" });
  assert.equal(last("koro_status").p_node_key, "5");
  await call("search_book", { query: "kv cache" });
  assert.deepEqual(last("koro_search"), { p_learner: L, p_query: "kv cache", p_entity: null });
  await call("search_book", { query: "kv", book: "Inference" });
  assert.equal(last("koro_search").p_entity, E);
  await call("list_library");
  await call("set_book_status", { status: "paused" });
  assert.equal(last("koro_set_status").p_status, "paused");
  await call("set_goal", { daily_minutes: 20 });
  assert.equal(last("koro_set_goal").p_daily_minutes, 20);
  assert.equal((await call("set_goal", { target_date: "next week" })).result.isError, true);
  await call("save_learner_summary", { summary: "s" });
  assert.equal(last("koro_set_summary").p_summary, "s");
  await call("study_time", { days: 7 });
  assert.equal(last("koro_study_time").p_since, "2026-10-05T12:00:00.000Z");
});

test("table_of_contents is compact and marks reference sections", async () => {
  const { call, last } = setup();
  const p = payload(await call("table_of_contents"));
  assert.equal(last("koro_toc").p_max_depth, 1);
  assert.equal(p.sections[0].mastered, "3/210");
  assert.equal(p.sections[0].summary, undefined);
  assert.equal(p.sections[1].status, "reference only");
  const withSummaries = payload(await call("table_of_contents", { depth: 0, include_summaries: true }));
  assert.equal(withSummaries.sections[0].summary, "s");
});

test("empty library is a clear tool error", async () => {
  const { call } = setup({ koro_library: [] });
  const r = await call("next_concept");
  assert.match(r.result.content[0].text, /library is empty/);
});

test("prompts list and render", async () => {
  const { post } = setup();
  const list = (await post({ jsonrpc: "2.0", id: 1, method: "prompts/list" })).body.result.prompts;
  assert.equal(list.length, PROMPTS.length);
  const got = (await post({ jsonrpc: "2.0", id: 2, method: "prompts/get", params: { name: "study_section", arguments: { section: "5.3" } } })).body.result;
  assert.match(got.messages[0].content.text, /5\.3/);
  const missing = await post({ jsonrpc: "2.0", id: 3, method: "prompts/get", params: { name: "study_section", arguments: {} } });
  assert.equal(missing.body.error.code, -32602);
});

test("GET: status page, selftest, and no SSE stream", async () => {
  const { handler } = setup();
  const page = await (await handler(new Request(BASE))).json();
  assert.equal(page.database, "ok");
  assert.equal(page.library[0].mastered, "3/210");
  const st = await (await handler(new Request(BASE + "?selftest=1"))).json();
  assert.equal(st.selftest.initialize.protocolVersion, "2025-06-18");
  assert.equal(st.selftest.initialized_notification, 202);
  assert.equal(st.selftest.tools, TOOLS.length);
  assert.equal(st.selftest.next_concept.isError, false);
  assert.equal(st.selftest.table_of_contents.isError, false);
  const sse = await handler(new Request(BASE, { headers: { Accept: "text/event-stream" } }));
  assert.equal(sse.status, 405);
  assert.equal((await handler(new Request(BASE, { method: "DELETE" }))).status, 405);
  assert.equal((await handler(new Request(BASE, { method: "OPTIONS" }))).status, 204);
});

test("token check failures are 503, not 404", async () => {
  const handler = createHandler({ rpc: async () => null, checkToken: async () => { throw new Error("down"); } });
  assert.equal((await handler(new Request(BASE))).status, 503);
});

test("validateArgs", () => {
  const schema = TOOLS.find((t) => t.name === "set_goal")!.inputSchema;
  assert.equal(validateArgs(schema, { daily_minutes: 0 }), "daily_minutes must be at least 1");
  assert.equal(validateArgs(schema, { daily_minutes: 2.5 }), "daily_minutes must be an integer");
  assert.equal(validateArgs(schema, {}), null);
});

test("secret key from env: new JSON keys, then legacy", () => {
  assert.equal(secretKeyFromEnv((n) => (n === "SUPABASE_SECRET_KEYS" ? '{"default":"sb_secret_x"}' : undefined)), "sb_secret_x");
  assert.equal(secretKeyFromEnv((n) => (n === "SUPABASE_SERVICE_ROLE_KEY" ? "eyJlegacy" : n === "SUPABASE_SECRET_KEYS" ? "not json" : undefined)), "eyJlegacy");
  assert.throws(() => secretKeyFromEnv(() => undefined));
});

test("makeRpc: headers, null params dropped, errors readable", async () => {
  const seen: any[] = [];
  const fakeFetch = (async (url: string, init: any) => {
    seen.push({ url, init });
    if (url.endsWith("/boom")) return new Response(JSON.stringify({ message: "unknown concept" }), { status: 400 });
    if (url.endsWith("/nothing")) return new Response("", { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }) as typeof fetch;
  const rpc = makeRpc("https://p.supabase.co/", "sb_secret_k", fakeFetch);
  assert.deepEqual(await rpc("koro_x", { p_a: 1, p_b: null, p_c: undefined }), { ok: true });
  assert.equal(seen[0].url, "https://p.supabase.co/rest/v1/rpc/koro_x");
  assert.equal(seen[0].init.headers.apikey, "sb_secret_k");
  assert.equal(seen[0].init.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(seen[0].init.body), { p_a: 1 });
  assert.equal(await rpc("nothing", {}), null);
  await assert.rejects(rpc("boom", {}), /boom: unknown concept/);
  const legacy = makeRpc("https://p", "eyJabc", fakeFetch);
  await legacy("koro_x", {});
  assert.equal(seen.at(-1).init.headers.Authorization, "Bearer eyJabc");
});

test("token cache: hits skip the database, misses expire quickly", async () => {
  let t = 0;
  let n = 0;
  const check = cachedTokenCheck(async () => { n++; return { learner_id: L }; }, 60_000, () => t);
  await check(TOKEN); await check(TOKEN);
  assert.equal(n, 1);
  t = 61_000;
  await check(TOKEN);
  assert.equal(n, 2);
});
