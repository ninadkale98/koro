-- Saved items, end to end, inside one transaction that always rolls back
-- (it ends by raising KORO_SAVED_OK). Nothing persists.
do $$
declare
  L uuid := (koro_ensure_learner('koro-saved-test')->>'learner_id')::uuid;
  r jsonb;
begin
  -- a topic, a question, a link
  r := koro_save_item(L, 'Paged attention vs. continuous batching', null, 'came up in the vLLM scheduler code',
                      null, array['#GPU', 'serving', 'gpu', ' '], '{"device": "laptop"}', 'cli');
  assert (r->>'no')::int = 1 and r->>'kind' = 'topic' and r->'tags' = '["gpu","serving"]'::jsonb, 'topic + tag cleanup';
  assert r->'context'->>'client' = 'cli' and r->'context'->>'device' = 'laptop', 'context kept';
  r := koro_save_item(L, 'Why is decode memory-bound?');
  assert (r->>'no')::int = 2 and r->>'kind' = 'question', 'question detected';
  r := koro_save_item(L, null, null, 'read later', 'https://arxiv.org/abs/2309.06180', array['papers'], '{}', 'connector');
  assert (r->>'no')::int = 3 and r->>'kind' = 'url' and r->>'title' = 'https://arxiv.org/abs/2309.06180', 'url';

  -- same link again: one item, title improved, tags merged, note appended
  r := koro_save_item(L, 'PagedAttention paper', null, 'section 4 on block tables', 'https://arxiv.org/abs/2309.06180',
                      array['kv-cache']);
  assert (r->>'existed')::boolean and (r->>'no')::int = 3, 'dedupe by url';
  assert r->>'title' = 'PagedAttention paper' and r->'tags' = '["kv-cache","papers"]'::jsonb, 'merge';
  assert r->>'body' = E'read later\n\nsection 4 on block tables', 'note appended';

  -- a file attached after upload
  r := koro_save_item(L, 'flash-attention.pdf', 'pdf');
  r := koro_attach_saved_file(L, '#4', 'koro-private', 'saved/x/4-flash-attention.pdf', 'flash-attention.pdf',
                              'application/pdf', 123456, repeat('b', 64));
  assert r->'file'->>'filename' = 'flash-attention.pdf', 'file attached';

  -- list, filter, search
  r := koro_saved_items(L);
  assert jsonb_array_length(r->'items') = 4 and (r->'counts'->>'inbox')::int = 4, 'list open';
  assert jsonb_array_length(koro_saved_items(L, 'open', 'GPU')->'items') = 1, 'tag filter, case-insensitive';
  assert jsonb_array_length(koro_saved_items(L, 'open', null, 'block tables')->'items') = 1, 'search notes';
  assert r->'tags'->0->>'tag' is not null, 'tag counts';

  -- lifecycle
  r := koro_update_saved_item(L, '2', 'learning', null, 'started in chapter 2', array['inference'], null);
  assert r->>'status' = 'learning' and r->>'body' = 'started in chapter 2', 'update';
  assert (koro_saved_items(L)->'items'->0->>'no')::int = 2, 'learning items come first';
  r := koro_update_saved_item(L, '1', 'done', null, null, null, array['serving']);
  assert r->'tags' = '["gpu"]'::jsonb, 'remove tag';
  assert jsonb_array_length(koro_saved_items(L)->'items') = 3, 'done hidden by default';
  assert jsonb_array_length(koro_saved_items(L, 'all')->'items') = 4, 'all';
  assert jsonb_array_length(koro_saved_items(L, 'done')->'items') = 1, 'by status';
  r := koro_update_saved_item(L, '3', 'archived');
  r := koro_save_item(L, 'PagedAttention again', null, null, 'https://arxiv.org/abs/2309.06180');
  assert (r->>'no')::int = 5 and not (r->>'existed')::boolean, 'an archived link can be saved again';
  assert koro_saved_item(L, '#5')->>'title' = 'PagedAttention again', 'get one';

  begin
    perform koro_saved_item(L, '99');
    assert false, 'unknown item should raise';
  exception when raise_exception then null;
  end;
  begin
    perform koro_update_saved_item(L, '1', 'someday');
    assert false, 'bad status should raise';
  exception when raise_exception then null;
  end;
  begin
    perform koro_save_item(L, '  ');
    assert false, 'empty save should raise';
  exception when raise_exception then null;
  end;

  raise exception 'KORO_SAVED_OK';
end $$;
