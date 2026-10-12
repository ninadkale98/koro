-- End-to-end check of the deployed koro-mcp connector over real HTTP, run from
-- SQL. Replace <TOKEN> with a token from `koro connector new` (the URL's last
-- segment). Uses read-only tools only, so it never changes learner progress.
with base as (select 'https://qrfgbkgbwtojxcylmhtl.supabase.co/functions/v1/koro-mcp/<TOKEN>' as u)
select 'initialize' as step, r.status, left(r.content, 200) as body
  from base, extensions.http_post(base.u, '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}', 'application/json') r
union all
select 'tools/list', r.status, (select string_agg(t->>'name', ', ') from jsonb_array_elements(r.content::jsonb->'result'->'tools') t)
  from base, extensions.http_post(base.u, '{"jsonrpc":"2.0","id":2,"method":"tools/list"}', 'application/json') r
union all
select 'progress_report', r.status, left(r.content::jsonb->'result'->'content'->0->>'text', 200)
  from base, extensions.http_post(base.u, '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"progress_report","arguments":{}}}', 'application/json') r
union all
select 'selftest', r.status, left(r.content, 300)
  from base, extensions.http_get(base.u || '?selftest=1') r;
