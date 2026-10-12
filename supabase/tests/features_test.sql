-- Feature requests, end to end, inside one transaction that always rolls back
-- (it ends by raising KORO_FEATURES_OK). Nothing persists.
do $$
declare
  L uuid := (koro_ensure_learner('koro-features-test')->>'learner_id')::uuid;
  r jsonb;
begin
  r := koro_add_feature(L, 'Save feature requests from any tab', 'so I can build them at night', 'koro', null, array['cli']);
  assert r->>'ref' = 'F1' and r->>'project' = 'koro' and r->>'status' = 'idea' and r->>'priority' = 'normal', 'added';
  assert r->'context'->>'client' = 'cli' and not (r->>'existed')::boolean, 'context and existed';
  r := koro_add_feature(L, 'save feature requests from ANY tab', 'also from the connector', 'Koro', 'high', array['connector']);
  assert (r->>'existed')::boolean and r->>'no' = '1', 'same title, same project: one request';
  assert r->>'body' = E'so I can build them at night\n\nalso from the connector' and r->>'priority' = 'high', 'details appended';
  assert r->'tags' = '["cli", "connector"]'::jsonb, 'tags merged';
  r := koro_add_feature(L, 'Save feature requests from any tab', null, 'rterm');
  assert r->>'no' = '2', 'same title in another project is separate';
  r := koro_add_feature(L, 'Dark mode for the status page', null, 'Code Praxis', 'low');
  assert r->>'project' = 'code-praxis', 'project slugged';
  r := koro_add_feature(L, 'Something without a project');
  assert r->'project' is null and r->>'no' = '4', 'no project';
  begin
    perform koro_add_feature(L, '  ');
    assert false, 'empty title should raise';
  exception when raise_exception then null;
  end;
  begin
    perform koro_add_feature(L, 'x', null, null, 'urgent');
    assert false, 'bad priority should raise';
  exception when raise_exception then null;
  end;

  -- the list: building, then planned, then ideas; high priority first
  perform koro_update_feature(L, 'F2', 'building');
  perform koro_update_feature(L, '#3', 'planned');
  r := koro_features(L);
  assert (select string_agg(x->>'no', ',') from jsonb_array_elements(r->'items') x) = '2,3,1,4', 'build order';
  assert (r->'counts'->>'idea')::int = 2 and (r->'counts'->>'building')::int = 1, 'counts';
  assert jsonb_array_length(koro_features(L, 'open', 'koro')->'items') = 1, 'one project';
  assert jsonb_array_length(koro_features(L, 'open', null, 'dark')->'items') = 1, 'search';
  assert (select x->>'open' from jsonb_array_elements(r->'projects') x where x->>'project' = 'koro') = '1', 'projects';

  -- moving along, done, reopen
  r := koro_update_feature(L, '1', 'done', null, null, null, null, null, null, null, 'shipped in 0.5.0');
  assert r->>'status' = 'done' and r->>'done_at' is not null and r->>'done_note' = 'shipped in 0.5.0', 'done';
  assert jsonb_array_length(koro_features(L)->'items') = 3, 'done is not open';
  assert koro_features(L, 'done')->'items'->0->>'no' = '1', 'done list';
  r := koro_update_feature(L, 'f1', 'idea');
  assert r->'done_at' is null and r->>'done_note' = 'shipped in 0.5.0', 'reopened';
  r := koro_update_feature(L, '1', null, 'Feature inbox', 'one more detail', null, '', 'low', array['x'], array['cli']);
  assert r->>'title' = 'Feature inbox' and r->>'body' like E'%\n\none more detail' and r->'project' is null, 'edit, append, clear project';
  assert r->>'priority' = 'low' and r->'tags' = '["connector", "x"]'::jsonb, 'priority and tags';
  r := koro_update_feature(L, '1', p_body => 'rewritten');
  assert r->>'body' = 'rewritten', 'body replaced';
  r := koro_update_feature(L, '1', p_project => 'koro');
  assert r->>'project' = 'koro' and r->>'body' = 'rewritten', 'project set, body kept';
  begin
    perform koro_update_feature(L, '1', 'shipped');
    assert false, 'bad status should raise';
  exception when raise_exception then null;
  end;
  begin
    perform koro_feature(L, 'F99');
    assert false, 'unknown ref should raise';
  exception when raise_exception then null;
  end;
  assert koro_feature(L, 'F4')->>'title' = 'Something without a project', 'one by ref';

  raise exception 'KORO_FEATURES_OK';
end $$;
