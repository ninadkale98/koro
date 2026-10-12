-- The notebook, end to end, inside one transaction that always rolls back
-- (it ends by raising KORO_NOTEBOOK_OK). Nothing persists.
do $$
declare
  L uuid := (koro_ensure_learner('koro-notebook-test')->>'learner_id')::uuid;
  r jsonb;
  v int;
begin
  -- a tree: 1 Linux internals > 1.1 Signals > 1.1.1 sigaction, 1.1.2 pthread_kill; 2 Inference
  r := koro_page_create(L, 'Linux internals');
  assert r->>'number' = '1' and not (r->>'existed')::boolean, 'top page';
  r := koro_page_create(L, 'Signals', 'Linux internals', null, 'Why I care: the PD-dump race.', array['linux']);
  assert r->>'number' = '1.1', 'child page by name';
  assert koro_page_create(L, 'sigaction & SA_RESTART', '1.1')->>'number' = '1.1.1', 'grandchild by number';
  assert koro_page_create(L, 'pthread_kill', 'signals')->>'number' = '1.1.2', 'by fragment';
  assert koro_page_create(L, 'Inference')->>'number' = '2', 'second top page';
  assert (koro_page_create(L, 'signals', '1')->>'existed')::boolean, 'same title under same parent: existing page';
  r := koro_page_create(L, 'Real-time signals', '1.1', '1.1.1');
  assert r->>'number' = '1.1.2', 'inserted after 1.1.1';
  assert (koro_resolve_page(L, '1.1.3')).title = 'pthread_kill', 'later sibling shifted';
  assert (koro_resolve_page(L, 'linux-internals/signals')).title = 'Signals', 'slug path';
  assert (koro_resolve_page(L, 'signals/pthread_kill')).title = 'pthread_kill', 'partial slug path';
  begin
    perform koro_resolve_page(L, 'sig');  -- Signals, sigaction, Real-time signals
    assert false, 'ambiguous name should raise';
  exception when raise_exception then
    null;
  end;

  -- the TOC
  r := koro_notebook(L);
  assert jsonb_array_length(r->'pages') = 6, 'six pages';
  assert (select string_agg(x->>'number', ',') from jsonb_array_elements(r->'pages') x) = '1,1.1,1.1.1,1.1.2,1.1.3,2', 'reading order';
  assert jsonb_array_length(koro_notebook(L, '1.1')->'pages') = 4, 'one branch';

  -- items: inbox, straight onto a page, filed later
  r := koro_capture(L, 'Unix signals overview', null, 'long paragraph...', null, array['signals'], '{}', 'cli', null);
  assert r->'page' is null, 'no page = inbox';
  r := koro_capture(L, 'EINTR example', 'snippet', E'#include <errno.h>\nwhile (ioctl(fd, X) < 0 && errno == EINTR) {}',
                    null, '{}', '{}', 'cli', 'signals');
  assert r->'page'->>'number' = '1.1', 'captured onto a page';
  r := koro_file_items(L, array['1'], '1.1.3');
  assert r->'items'->0->'page'->>'title' = 'pthread_kill', 'filed';
  assert (koro_notebook(L)->'inbox'->>'items')::int = 0, 'inbox empty';
  r := koro_file_items(L, array['#1'], 'inbox');
  assert (koro_notebook(L, 'inbox', true)->'inbox'->'item_list'->0->>'no')::int = 1, 'back to inbox, listed';
  begin
    perform koro_capture(L, 'x', null, null, null, '{}', '{}', 'cli', 'no such page');
    assert false, 'bad page should raise before saving';
  exception when raise_exception then
    null;
  end;
  assert (select count(*) from saved_items where learner_id = L) = 2, 'nothing saved on a bad page';

  -- the write-up: append, sections, find/replace, versions, history, restore
  r := koro_page(L, '1.1');
  assert r->>'body' = 'Why I care: the PD-dump race.' and (r->>'version')::int = 1, 'page read';
  assert jsonb_array_length(r->'subpages') = 3 and jsonb_array_length(r->'item_list') = 1, 'subpages and items';
  r := koro_page_write(L, '1.1', 'append', E'## Delivery\nA process-directed signal goes to any thread that does not block it.\n\n## EINTR\nBlocking calls return EINTR.');
  assert (r->>'version')::int = 2, 'append bumps version';
  r := koro_page_write(L, '1.1', 'append', E'```c\n# not a heading inside code\n```', 'Delivery');
  r := koro_page(L, '1.1');
  assert (select string_agg(x->>'heading', '|') from jsonb_array_elements(r->'headings') x) = 'Delivery|EINTR', 'code # is not a heading';
  assert r->>'body' like E'%does not block it.\n\n```c\n# not a heading inside code\n```\n\n## EINTR%', 'appended inside the section, before the next heading';
  r := koro_page_section(L, 'signals', 'eintr');
  assert r->>'text' = 'Blocking calls return EINTR.' and r->>'heading' = 'EINTR', 'read one section';
  r := koro_page_write(L, '1.1', 'replace_section', 'Blocking calls fail with EINTR unless SA_RESTART is set.', 'EINTR',
                       null, (r->>'version')::int);
  assert koro_page(L, '1.1')->>'body' like E'%## EINTR\n\nBlocking calls fail with EINTR unless SA_RESTART is set.', 'section replaced';
  assert koro_page(L, '1.1')->>'body' like 'Why I care: the PD-dump race.%', 'the rest untouched';
  r := koro_page_write(L, '1.1', 'replace_text', 'any one thread', null, 'any thread');
  assert koro_page(L, '1.1')->>'body' like '%any one thread%', 'find and replace';
  v := (koro_page(L, '1.1')->>'version')::int;
  begin
    perform koro_page_write(L, '1.1', 'append', 'stale edit', null, null, v - 1);
    assert false, 'stale version should be refused';
  exception when raise_exception then
    null;
  end;
  begin
    perform koro_page_write(L, '1.1', 'replace_text', 'x', null, 'text that is not there');
    assert false, 'missing text should raise';
  exception when raise_exception then
    null;
  end;
  begin
    perform koro_page_write(L, '1.1', 'append', 'x', 'No such heading');
    assert false, 'missing heading should raise';
  exception when raise_exception then
    null;
  end;
  assert not (koro_page_write(L, '1.1', 'replace_text', 'any one thread', null, 'any one thread')->>'changed')::boolean, 'no-op';
  r := koro_page_history(L, '1.1');
  assert jsonb_array_length(r->'history') = v - 1 and (r->'history'->-1->>'version')::int = 1, 'every version kept';
  r := koro_page_restore(L, '1.1', 1);
  assert koro_page(L, '1.1')->>'body' = 'Why I care: the PD-dump race.', 'restored';
  assert (koro_page(L, '1.1')->>'version')::int = v + 1, 'restore is a new version';

  -- metadata, moves, archive
  r := koro_page_update(L, '1.1', 'Unix signals', 'learning', array['fastrpc'], array['linux']);
  assert r->>'title' = 'Unix signals' and r->>'status' = 'learning' and r->'tags' = '["fastrpc"]'::jsonb, 'update';
  r := koro_page_move(L, '2', '1.1');
  assert r->>'number' = '1.1.4', 'moved under signals';
  begin
    perform koro_page_move(L, '1', '1.1');
    assert false, 'cannot move a page inside itself';
  exception when raise_exception then
    null;
  end;
  r := koro_page_move(L, '1.1.4', null, null, true);
  assert r->>'number' = '2', 'back to top level';
  r := koro_page_move(L, '1.1.3', null, '1.1.1');
  assert r->>'number' = '1.1.2', 'moved after a sibling';
  perform koro_page_update(L, '2', null, 'archived');
  assert jsonb_array_length(koro_notebook(L)->'pages') = 5, 'archived pages hidden';
  assert jsonb_array_length(koro_notebook(L, null, false, true)->'pages') = 6, 'shown with all';
  assert (koro_resolve_page(L, 'Inference')).status = 'archived', 'archived page found by exact name';

  -- editing an item as a whole
  r := koro_set_saved_text(L, '2', 'EINTR retry loop', E'Retry on EINTR.\n\n```c\nwhile (...) {}\n```');
  assert r->>'title' = 'EINTR retry loop' and r->>'body' like 'Retry on EINTR.%', 'item rewritten';

  raise exception 'KORO_NOTEBOOK_OK';
end $$;
