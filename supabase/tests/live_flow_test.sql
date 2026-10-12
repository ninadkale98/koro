-- Live end-to-end test of the koro functions. Runs inside one DO block that
-- always ends by raising, so every change rolls back and the database stays clean.
-- Success looks like: ERROR: KORO_TEST_OK {...results...}
do $$
declare
  L uuid; E uuid; r jsonb; res jsonb := '{}'::jsonb; a1 uuid; c_attn uuid;
begin
  -- learner + entity (dedupe)
  L := (koro_ensure_learner('koro-test')->>'learner_id')::uuid;
  r := koro_create_entity('Koro Test Book', 'book', 'test.pdf', 'test:koro-flow', 'hash-koro-flow', null, 'Tester', 40, L);
  E := (r->>'entity_id')::uuid;
  assert (r->>'existed')::boolean = false, 'entity should be new';
  r := koro_create_entity('Same Book Again', 'book', 'other.pdf', null, 'hash-koro-flow');
  assert (r->>'existed')::boolean and (r->>'entity_id')::uuid = E, 'dedupe by hash failed';

  -- outline
  r := koro_build_outline(E, '[
    {"key":"1","kind":"chapter","title":"Chapter 1: Basics","order_index":1,"page_start":1,"page_end":20},
    {"key":"1.1","parent_key":"1","kind":"section","title":"1.1 Tokens","order_index":1,"page_start":2,"page_end":8},
    {"key":"1.2","parent_key":"1","kind":"section","title":"1.2 Attention","order_index":2,"page_start":9,"page_end":20},
    {"key":"2","kind":"chapter","title":"Chapter 2: Speed","order_index":2,"page_start":21,"page_end":40},
    {"key":"2.1","parent_key":"2","kind":"section","title":"2.1 KV Cache","order_index":1,"page_start":22,"page_end":40},
    {"key":"pref","kind":"chapter","title":"Preface","order_index":0,"page_start":0,"page_end":0,"teachable":false}
  ]'::jsonb);
  assert (r->>'inserted')::int = 6, 'outline insert count';
  r := koro_build_outline(E, '[{"key":"1","kind":"chapter","title":"Chapter 1: Basics (rev)","order_index":1}]'::jsonb);
  assert (r->>'updated')::int = 1, 'outline upsert by key';

  -- two machines claim different nodes
  r := koro_claim_next_node(E, 'laptop-A');
  assert r->'node'->>'key' = '1', 'first claim should be chapter 1, got ' || (r->'node'->>'key');
  r := koro_claim_next_node(E, 'laptop-B');
  assert r->'node'->>'key' = '1.1', 'second machine should get 1.1, got ' || (r->'node'->>'key');
  begin
    perform koro_save_node(E, '1.1', '{"summary":"x","concepts":[]}'::jsonb, 'laptop-A');
    assert false, 'laptop-A must not save a node claimed by laptop-B';
  exception when raise_exception then null;
  end;

  -- save parsed nodes (includes a deliberate cycle and an unresolved ref)
  perform koro_save_node(E, '1', '{"summary":"Intro chapter.","concepts":[]}'::jsonb, 'laptop-A');
  perform koro_save_node(E, '1.1', '{"summary":"Tokens and tokenizers.","concepts":[
     {"key":"tokens","name":"Tokens","summary":"Text is split into tokens.","modality":"read",
      "teaching_content":{"beats":[{"title":"What a token is","explain":"..."}],
                          "checks":[{"objective":"define","question":"What is a token?"}]},
      "objectives":[{"key":"define","description":"Define a token"},{"key":"count","description":"Estimate token counts"}],
      "depends_on":[{"key":"1.2/attention","confidence":0.3}]}
  ]}'::jsonb, 'laptop-B');
  perform koro_save_node(E, '1.2', '{"summary":"Attention.","concepts":[
     {"key":"attention","name":"Attention","summary":"Tokens attend to each other.",
      "objectives":[{"key":"explain","description":"Explain attention"}],
      "depends_on":["1.1/tokens"]}
  ]}'::jsonb);
  perform koro_save_node(E, '2', '{"summary":"Speed chapter.","concepts":[]}'::jsonb);
  perform koro_save_node(E, '2.1', '{"summary":"KV cache.","concepts":[
     {"key":"kv-cache","name":"KV cache","summary":"Reuse keys and values.",
      "objectives":[{"key":"why","description":"Explain why caching helps decode"}],
      "depends_on":["1.2/attention", "9.9/missing-thing"]}
  ]}'::jsonb);
  perform koro_save_node(E, 'pref', '{"summary":"Why this book exists.","teachable":false,"concepts":[]}'::jsonb);

  r := koro_finalize_ingest(E);
  res := res || jsonb_build_object('finalize', r);
  assert r->>'status' = 'ready', 'entity should be ready';
  assert jsonb_array_length(r->'cycles_broken') = 1, 'one cycle should be broken';
  assert r->'cycles_broken'->0->>'from' = '1.1/tokens', 'the forward edge tokens->attention should be dropped';
  assert jsonb_array_length(r->'unresolved_refs') = 1, 'one unresolved ref';
  assert (r->>'edges')::int = 2, 'two active edges should remain, got ' || (r->>'edges');
  assert (r->'non_teachable_nodes' ? 'pref') and not (r->'non_teachable_nodes' ? '1'), 'teachable flags';

  -- learning loop
  r := koro_start(L, E, null, 'cli');
  assert r->'next'->>'reason' = 'next' and r->'next'->'concept'->>'key' = '1.1/tokens', 'first concept should be tokens';

  r := koro_teach(L, '1.1/tokens', E, 'cli');
  assert r->'progress'->>'status' = 'seen', 'teach marks seen';
  assert r->'figures' = '[]'::jsonb and (r->>'has_source_text')::boolean = false, 'teach has figure fields';
  r := koro_next_concept(L, E);
  assert r->>'reason' = 'resume' and r->'concept'->>'key' = '1.1/tokens', 'resume shown-but-unchecked concept';

  -- attention is blocked until tokens is satisfied
  r := koro_next_concept(L, E, '1.2');
  assert r->>'reason' = 'prerequisite' and r->'concept'->>'key' = '1.1/tokens', 'should surface prerequisite, got ' || r::text;

  r := koro_assess(L, '1.1/tokens', 'wrong', 'define', 'What is a token?', 'A word', 'Tokens are sub-word units',
                   'thinks tokens are always whole words', 'question', 1.0, E);
  a1 := (r->>'assessment_id')::uuid;
  assert r->>'status' = 'learning', 'wrong answer -> learning';
  assert r->'objectives'->0->>'status' = 'gap' and r->'objectives'->0->>'misconception' is not null, 'gap + misconception';

  r := koro_assess(L, '1.1/tokens', 'correct', 'define', null, null, null, null, 'question', 1.0, E);
  assert r->'objectives'->0->>'status' = 'gap', 'one correct after wrong still a gap (0.6)';
  r := koro_assess(L, '1.1/tokens', 'correct', 'define', null, null, null, null, 'question', 1.0, E);
  assert r->'objectives'->0->>'status' = 'strength', 'two corrects close the gap';
  r := koro_assess(L, '1.1/tokens', 'correct', 'count', null, null, null, null, 'question', 1.0, E);
  assert r->>'status' = 'mastered', 'all objectives strong -> mastered, got ' || r::text;
  res := res || jsonb_build_object('tokens_after_checks', r);

  assert (select op.resolved_at is not null from objective_progress op
     join objectives ob on ob.id = op.objective_id join concepts c on c.id = ob.concept_id
     where op.learner_id = L and c.key = '1.1/tokens' and ob.key = 'define'), 'gap closing records resolved_at';

  -- the log is append-only
  begin
    update assessments set verdict = 'correct' where id = a1;
    assert false, 'assessments must be immutable';
  exception when raise_exception then null;
  end;

  -- dispute the wrong verdict: history stays, calculation uses the correction
  r := koro_dispute(L, a1, 'correct', 'My answer was right in context');
  assert r->>'status' = 'mastered', 'dispute keeps mastered';
  assert (select count(*) from assessments where learner_id = L) = 5, 'dispute appends a row';

  -- now attention is teachable
  r := koro_next_concept(L, E);
  assert r->>'reason' = 'next' and r->'concept'->>'key' = '1.2/attention', 'attention next, got ' || r::text;

  -- "I already know this" for a whole chapter
  r := koro_mark_known(L, E, '2', 'did this at work');
  assert (r->>'marked_known')::int = 1, 'mark known chapter 2';
  select id into c_attn from concepts where entity_id = E and key = '2.1/kv-cache';
  assert (select status from progress where learner_id = L and concept_id = c_attn) = 'mastered', 'claimed known -> mastered';

  -- views
  r := koro_toc(L, E);
  res := res || jsonb_build_object('toc_rollup_ch1', (select x->'progress' from jsonb_array_elements(r->'nodes') x where x->>'key' = '1'));
  assert (select x->'progress'->>'status' from jsonb_array_elements(r->'nodes') x where x->>'key' = '1') = 'learning', 'chapter 1 learning';
  assert (select x->'progress' from jsonb_array_elements(r->'nodes') x where x->>'key' = 'pref') = 'null'::jsonb, 'preface has no rollup';
  r := koro_status(L, E);
  res := res || jsonb_build_object('status', r);
  assert (r->'rollup'->>'mastered')::int = 2, 'two mastered';
  r := koro_search(L, 'cache', E);
  assert jsonb_array_length(r->'concepts') >= 1, 'search finds kv cache';

  -- goals, pause, continue
  perform koro_set_goal(L, E, (current_date + 30), 20);
  perform koro_set_status(L, E, 'paused');
  r := koro_continue(L);
  assert (r->>'none')::boolean, 'paused book is not continued';
  perform koro_set_status(L, E, 'active');
  r := koro_continue(L);
  assert r->'next'->'concept'->>'key' = '1.2/attention', 'continue picks the book back up';
  res := res || jsonb_build_object('study_time', koro_study_time(L));

  -- restart: history kept, progress back to untouched
  r := koro_restart(L, E);
  assert (select count(*) from progress where learner_id = L and status <> 'untouched') = 0, 'restart resets progress';
  assert (select count(*) from assessments where learner_id = L) = 6, 'restart keeps the log';
  r := koro_next_concept(L, E);
  assert r->'concept'->>'key' = '1.1/tokens', 'after restart, start from the beginning';

  raise exception 'KORO_TEST_OK %', res::text;
end $$;
