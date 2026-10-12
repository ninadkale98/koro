-- Add prerequisite references across nodes (for example after a whole-book pass
-- that links chapters), without replacing a concept's other references.
create or replace function public.koro_add_edge_refs(p_entity uuid, p_refs jsonb) returns jsonb
language plpgsql set search_path = public as $$
declare
  r jsonb;
  v_n int := 0;
  v_skipped jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(p_refs) <> 'array' then raise exception 'p_refs must be a JSON array'; end if;
  for r in select value from jsonb_array_elements(p_refs) loop
    if not exists (select 1 from public.concepts where entity_id = p_entity and key = r->>'from' and archived_at is null)
       or r->>'from' = r->>'to' then
      v_skipped := v_skipped || r;
      continue;
    end if;
    insert into public.ingest_edge_refs (entity_id, from_key, to_key, confidence, active)
    values (p_entity, r->>'from', r->>'to', least(1, greatest(0, coalesce((r->>'confidence')::real, 0.7))), true)
    on conflict (entity_id, from_key, to_key) do update set confidence = excluded.confidence, active = true;
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('added', v_n, 'skipped', v_skipped);
end $$;
revoke execute on function public.koro_add_edge_refs(uuid, jsonb) from public, anon, authenticated;
