-- Figures in ingest: `ingest next` lists the section's figures, and a lesson beat
-- that shows one ("figures": ["2.13"]) links the figure to its concept.

create or replace function public.koro_claim_next_node(
  p_entity uuid,
  p_device text,
  p_lease_minutes int default 30,
  p_node_key text default null
) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_node public.nodes;
  v_remaining int;
begin
  if p_node_key is not null then
    select * into v_node from public.nodes
     where entity_id = p_entity and key = p_node_key
     for update;
    if not found then
      raise exception 'unknown node %', p_node_key;
    end if;
    if v_node.ingest_status = 'parsing' and v_node.claimed_until > now() and v_node.claimed_by <> p_device then
      raise exception 'node % is being parsed by % until %', p_node_key, v_node.claimed_by, v_node.claimed_until;
    end if;
  else
    select * into v_node from public.nodes
     where entity_id = p_entity
       and (ingest_status = 'pending' or (ingest_status = 'parsing' and claimed_until < now()))
     order by seq
     for update skip locked
     limit 1;
    if not found then
      select count(*) into v_remaining from public.nodes
       where entity_id = p_entity and ingest_status in ('pending', 'parsing', 'failed');
      return jsonb_build_object('done', v_remaining = 0, 'claimed', false,
        'parsing_elsewhere', (select count(*) from public.nodes where entity_id = p_entity and ingest_status = 'parsing'),
        'failed', (select coalesce(jsonb_agg(key order by seq), '[]') from public.nodes where entity_id = p_entity and ingest_status = 'failed'));
    end if;
  end if;

  update public.nodes set ingest_status = 'parsing', claimed_by = p_device,
         claimed_until = now() + make_interval(mins => p_lease_minutes), ingest_error = null
   where id = v_node.id;

  select count(*) into v_remaining from public.nodes
   where entity_id = p_entity and ingest_status in ('pending', 'failed');

  return jsonb_build_object(
    'claimed', true,
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title, 'source_ref', e.source_ref,
                                         'source_type', e.source_type)
                 from public.entities e where e.id = p_entity),
    'node', jsonb_build_object('key', v_node.key, 'title', v_node.title, 'kind', v_node.kind,
                               'page_start', v_node.page_start, 'page_end', v_node.page_end,
                               'teachable', v_node.teachable, 'depth', v_node.depth),
    'ancestors', (select coalesce(jsonb_agg(jsonb_build_object('key', a.key, 'title', a.title) order by a.depth), '[]')
                    from public.nodes a where a.entity_id = p_entity and a.path @> v_node.path and a.id <> v_node.id),
    'children', (select coalesce(jsonb_agg(jsonb_build_object('key', c.key, 'title', c.title,
                                                              'page_start', c.page_start) order by c.order_index), '[]')
                   from public.nodes c where c.parent_id = v_node.id),
    'figures', (select coalesce(jsonb_agg(jsonb_build_object('label', a.label, 'caption', a.caption, 'page', a.page)
                                          order by a.page, a.label), '[]')
                  from public.assets a left join public.concepts ac on ac.id = a.concept_id
                 where a.entity_id = p_entity and a.kind = 'image' and coalesce(a.node_id, ac.node_id) = v_node.id),
    'known_concepts', (select coalesce(jsonb_agg(jsonb_build_object('key', c.key, 'name', c.name) order by n2.seq, c.order_index), '[]')
                         from public.concepts c join public.nodes n2 on n2.id = c.node_id
                        where c.entity_id = p_entity and c.archived_at is null),
    'lease_until', now() + make_interval(mins => p_lease_minutes),
    'remaining_after_this', v_remaining
  );
end $$;

create or replace function public.koro_save_node(
  p_entity uuid,
  p_node_key text,
  p_payload jsonb,
  p_device text default null
) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_node public.nodes;
  c jsonb;
  o jsonb;
  d jsonb;
  ci int := 0;
  oi int;
  v_ckey text;
  v_tkey text;
  v_cid uuid;
  v_keys text[] := '{}';
  v_okeys text[];
  v_objs int := 0;
  v_refs int := 0;
begin
  select * into v_node from public.nodes where entity_id = p_entity and key = p_node_key for update;
  if not found then
    raise exception 'unknown node %', p_node_key;
  end if;
  if p_device is not null and v_node.ingest_status = 'parsing' and v_node.claimed_by <> p_device
     and v_node.claimed_until > now() then
    raise exception 'node % is claimed by % until %', p_node_key, v_node.claimed_by, v_node.claimed_until;
  end if;
  if jsonb_typeof(p_payload) <> 'object' then
    raise exception 'payload must be a JSON object';
  end if;
  if p_payload ? 'concepts' and jsonb_typeof(p_payload->'concepts') <> 'array' then
    raise exception 'payload.concepts must be an array';
  end if;

  for c in select value from jsonb_array_elements(coalesce(p_payload->'concepts', '[]'::jsonb)) loop
    ci := ci + 1;
    if coalesce(c->>'key', '') = '' or coalesce(c->>'name', '') = '' then
      raise exception 'concept % in node % is missing key or name', ci, p_node_key;
    end if;
    if c ? 'teaching_content' and jsonb_typeof(c->'teaching_content') <> 'object' then
      raise exception 'concept % teaching_content must be an object', c->>'key';
    end if;
    v_ckey := case when position('/' in c->>'key') > 0 then c->>'key' else p_node_key || '/' || (c->>'key') end;
    v_keys := v_keys || v_ckey;

    insert into public.concepts (entity_id, node_id, key, name, summary, teaching_content, modality, order_index)
    values (p_entity, v_node.id, v_ckey, c->>'name', c->>'summary',
            coalesce(c->'teaching_content', '{}'::jsonb), coalesce(c->>'modality', 'read'), ci)
    on conflict (entity_id, key) do update set
      node_id = excluded.node_id, name = excluded.name, summary = excluded.summary,
      teaching_content = excluded.teaching_content, modality = excluded.modality,
      order_index = excluded.order_index, archived_at = null, updated_at = now()
    returning id into v_cid;

    v_okeys := '{}';
    oi := 0;
    for o in select value from jsonb_array_elements(coalesce(c->'objectives', '[]'::jsonb)) loop
      oi := oi + 1;
      if coalesce(o->>'key', '') = '' or coalesce(o->>'description', '') = '' then
        raise exception 'objective % of concept % is missing key or description', oi, v_ckey;
      end if;
      v_okeys := v_okeys || (o->>'key');
      insert into public.objectives (concept_id, key, description, order_index)
      values (v_cid, o->>'key', o->>'description', oi)
      on conflict (concept_id, key) do update set
        description = excluded.description, order_index = excluded.order_index, archived_at = null;
      v_objs := v_objs + 1;
    end loop;
    update public.objectives set archived_at = now()
     where concept_id = v_cid and archived_at is null and not (key = any (v_okeys));

    -- Figures shown in this concept's beats ("figures": ["2.13"] or "figure": "2.13") belong to it.
    update public.assets set concept_id = v_cid, node_id = null
     where entity_id = p_entity and kind = 'image'
       and label in (select jsonb_array_elements_text(b->'figures')
                       from jsonb_array_elements(coalesce(c->'teaching_content'->'beats', '[]'::jsonb)) b
                      where jsonb_typeof(b->'figures') = 'array'
                     union
                     select b->>'figure'
                       from jsonb_array_elements(coalesce(c->'teaching_content'->'beats', '[]'::jsonb)) b
                      where jsonb_typeof(b->'figure') = 'string');

    -- Replace this concept's prerequisite references (deactivate, then upsert active).
    update public.ingest_edge_refs set active = false
     where entity_id = p_entity and from_key = v_ckey and active;
    for d in select value from jsonb_array_elements(coalesce(c->'depends_on', '[]'::jsonb)) loop
      v_tkey := case jsonb_typeof(d) when 'string' then d #>> '{}' else d->>'key' end;
      if v_tkey is null or v_tkey = '' then continue; end if;
      if position('/' in v_tkey) = 0 then v_tkey := p_node_key || '/' || v_tkey; end if;
      if v_tkey = v_ckey then continue; end if;
      insert into public.ingest_edge_refs (entity_id, from_key, to_key, confidence, active)
      values (p_entity, v_ckey, v_tkey,
              least(1, greatest(0, coalesce(case jsonb_typeof(d) when 'object' then (d->>'confidence')::real end, 0.8))),
              true)
      on conflict (entity_id, from_key, to_key) do update set confidence = excluded.confidence, active = true;
      v_refs := v_refs + 1;
    end loop;
  end loop;

  -- Concepts this node no longer produces are archived, never deleted.
  update public.concepts set archived_at = now(), updated_at = now()
   where node_id = v_node.id and archived_at is null and not (key = any (v_keys));

  update public.nodes set
    summary = coalesce(p_payload->>'summary', summary),
    teachable = coalesce((p_payload->>'teachable')::boolean, teachable),
    ingest_status = 'parsed', parsed_at = now(), ingest_error = null,
    claimed_by = null, claimed_until = null
   where id = v_node.id;

  return jsonb_build_object('node_key', p_node_key, 'concepts', ci, 'objectives', v_objs, 'edge_refs', v_refs,
    'remaining', (select count(*) from public.nodes where entity_id = p_entity and ingest_status <> 'parsed'));
end $$;

revoke execute on function public.koro_claim_next_node(uuid,text,int,text) from public, anon, authenticated;
revoke execute on function public.koro_save_node(uuid,text,jsonb,text) from public, anon, authenticated;
