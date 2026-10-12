-- Koro ingest functions. The CLI never calls a model: the coding agent reads the
-- source, writes structured JSON, and these functions store it.

create or replace function public.koro_node_label(p_key text) returns text
language sql immutable set search_path = public as $$
  select 'n_' || regexp_replace(p_key, '[^A-Za-z0-9]', '_', 'g')
$$;

-- Create an entity, or return the existing one when the canonical id or file hash matches.
create or replace function public.koro_create_entity(
  p_title text,
  p_source_type text,
  p_source_ref text default null,
  p_canonical_id text default null,
  p_source_hash text default null,
  p_source_version text default null,
  p_author text default null,
  p_page_count int default null,
  p_learner uuid default null
) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_existing public.entities;
  v_id uuid;
begin
  select * into v_existing from public.entities
   where (p_canonical_id is not null and canonical_id = p_canonical_id)
      or (p_source_hash is not null and source_hash = p_source_hash)
   limit 1;
  if found then
    return jsonb_build_object('entity_id', v_existing.id, 'existed', true,
                              'title', v_existing.title, 'status', v_existing.status);
  end if;
  insert into public.entities (title, source_type, source_ref, canonical_id, source_hash,
                               source_version, author, page_count, created_by)
  values (p_title, p_source_type, p_source_ref, p_canonical_id, p_source_hash,
          p_source_version, p_author, p_page_count, p_learner)
  returning id into v_id;
  return jsonb_build_object('entity_id', v_id, 'existed', false, 'title', p_title, 'status', 'ingesting');
end $$;

-- Create or update the outline. p_nodes is a JSON array in reading order:
-- [{key, parent_key, kind, title, order_index, page_start, page_end, teachable}]
create or replace function public.koro_build_outline(p_entity uuid, p_nodes jsonb) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  n jsonb;
  i int := 0;
  v_parent public.nodes;
  v_path ltree;
  v_depth int;
  v_inserted int := 0;
  v_updated int := 0;
  v_existing uuid;
begin
  if jsonb_typeof(p_nodes) <> 'array' then
    raise exception 'p_nodes must be a JSON array';
  end if;
  for n in select value from jsonb_array_elements(p_nodes) loop
    i := i + 1;
    if coalesce(n->>'key', '') = '' or coalesce(n->>'title', '') = '' then
      raise exception 'node % is missing key or title', i;
    end if;
    v_parent := null;
    if n->>'parent_key' is not null then
      select * into v_parent from public.nodes where entity_id = p_entity and key = n->>'parent_key';
      if not found then
        raise exception 'node % has unknown parent_key % (parents must come first)', n->>'key', n->>'parent_key';
      end if;
      v_path := v_parent.path || text2ltree(koro_node_label(n->>'key'));
      v_depth := v_parent.depth + 1;
    else
      v_path := text2ltree(koro_node_label(n->>'key'));
      v_depth := 0;
    end if;
    select id into v_existing from public.nodes where entity_id = p_entity and key = n->>'key';
    if found then
      update public.nodes set
        parent_id = v_parent.id, kind = coalesce(n->>'kind', kind), title = n->>'title',
        order_index = coalesce((n->>'order_index')::int, order_index), seq = i, depth = v_depth,
        path = v_path, page_start = (n->>'page_start')::int, page_end = (n->>'page_end')::int,
        teachable = coalesce((n->>'teachable')::boolean, teachable)
      where id = v_existing;
      v_updated := v_updated + 1;
    else
      insert into public.nodes (entity_id, parent_id, key, kind, title, order_index, seq, depth, path,
                                page_start, page_end, teachable)
      values (p_entity, v_parent.id, n->>'key', coalesce(n->>'kind', 'section'), n->>'title',
              coalesce((n->>'order_index')::int, 0), i, v_depth, v_path,
              (n->>'page_start')::int, (n->>'page_end')::int, coalesce((n->>'teachable')::boolean, true));
      v_inserted := v_inserted + 1;
    end if;
  end loop;
  update public.entities set status = 'ingesting' where id = p_entity and status <> 'ingesting'
    and exists (select 1 from public.nodes where entity_id = p_entity and ingest_status <> 'parsed');
  return jsonb_build_object('nodes', i, 'inserted', v_inserted, 'updated', v_updated);
end $$;

-- Claim the next node to parse. Lease-based and skip-locked, so several machines
-- can ingest the same entity in parallel without taking the same node.
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
    'known_concepts', (select coalesce(jsonb_agg(jsonb_build_object('key', c.key, 'name', c.name) order by n2.seq, c.order_index), '[]')
                         from public.concepts c join public.nodes n2 on n2.id = c.node_id
                        where c.entity_id = p_entity and c.archived_at is null),
    'lease_until', now() + make_interval(mins => p_lease_minutes),
    'remaining_after_this', v_remaining
  );
end $$;

-- Save a parsed node. Upserts by stable key, so re-ingest keeps learner evidence.
-- p_payload: {summary, teachable?, concepts: [{key, name, summary, modality,
--   teaching_content: {...}, objectives: [{key, description}], depends_on: [key | {key, confidence}]}]}
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

-- Give a node back (pending), or mark it failed with an error message.
create or replace function public.koro_release_node(p_entity uuid, p_node_key text, p_error text default null)
returns jsonb
language plpgsql set search_path = public, extensions as $$
begin
  update public.nodes set
    ingest_status = case when p_error is null then 'pending' else 'failed' end,
    ingest_error = p_error, claimed_by = null, claimed_until = null
  where entity_id = p_entity and key = p_node_key;
  if not found then raise exception 'unknown node %', p_node_key; end if;
  return jsonb_build_object('node_key', p_node_key, 'status', case when p_error is null then 'pending' else 'failed' end);
end $$;

-- Resolve prerequisite references, break dependency cycles, mark non-teachable
-- nodes, and set the entity ready when every node is parsed. Nothing is removed:
-- edges are deactivated with a reason.
create or replace function public.koro_finalize_ingest(p_entity uuid) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_unresolved jsonb;
  v_broken jsonb := '[]'::jsonb;
  e record;
  v_cycle boolean;
  v_unparsed int;
  v_edges int;
begin
  -- 1. Turn active references whose target exists into edges.
  insert into public.concept_edges (from_concept_id, to_concept_id, confidence, active, inactive_reason)
  select f.id, t.id, r.confidence, true, null
    from public.ingest_edge_refs r
    join public.concepts f on f.entity_id = r.entity_id and f.key = r.from_key and f.archived_at is null
    join public.concepts t on t.entity_id = r.entity_id and t.key = r.to_key and t.archived_at is null
   where r.entity_id = p_entity and r.active and f.id <> t.id
  on conflict (from_concept_id, to_concept_id)
  do update set confidence = excluded.confidence, active = true, inactive_reason = null;

  -- Edges whose reference went away, or that touch archived concepts, are deactivated.
  update public.concept_edges ce
     set active = false,
         inactive_reason = case when f.archived_at is not null or t.archived_at is not null
                                then 'archived' else 'removed' end
    from public.concepts f, public.concepts t
   where ce.from_concept_id = f.id and ce.to_concept_id = t.id
     and f.entity_id = p_entity and ce.active
     and (f.archived_at is not null or t.archived_at is not null
          or not exists (select 1 from public.ingest_edge_refs r
                          where r.entity_id = p_entity and r.active
                            and r.from_key = f.key and r.to_key = t.key));

  select coalesce(jsonb_agg(jsonb_build_object('from', r.from_key, 'to', r.to_key)), '[]') into v_unresolved
    from public.ingest_edge_refs r
   where r.entity_id = p_entity and r.active
     and not exists (select 1 from public.concepts t
                      where t.entity_id = r.entity_id and t.key = r.to_key and t.archived_at is null);

  -- 2. Break cycles. Every cycle contains at least one forward edge (a concept
  -- needing something that comes later in reading order), so check forward
  -- edges weakest first and deactivate any that close a loop.
  for e in
    select ce.from_concept_id, ce.to_concept_id, ce.confidence, f.key as from_key, t.key as to_key
      from public.concept_edges ce
      join public.concepts f on f.id = ce.from_concept_id
      join public.nodes fn on fn.id = f.node_id
      join public.concepts t on t.id = ce.to_concept_id
      join public.nodes tn on tn.id = t.node_id
     where f.entity_id = p_entity and ce.active
       and (tn.seq, t.order_index) > (fn.seq, f.order_index)
     order by ce.confidence asc, tn.seq desc
  loop
    with recursive reach(id) as (
      select e.to_concept_id
      union
      select ce2.to_concept_id from public.concept_edges ce2
        join reach on ce2.from_concept_id = reach.id
       where ce2.active
    )
    select exists (select 1 from reach where id = e.from_concept_id) into v_cycle;
    if v_cycle then
      update public.concept_edges set active = false, inactive_reason = 'cycle'
       where from_concept_id = e.from_concept_id and to_concept_id = e.to_concept_id;
      v_broken := v_broken || jsonb_build_object('from', e.from_key, 'to', e.to_key, 'confidence', e.confidence);
    end if;
  end loop;

  -- 3. A node is teachable when its subtree holds at least one live concept.
  update public.nodes n set teachable = exists (
      select 1 from public.concepts c join public.nodes cn on cn.id = c.node_id
       where c.archived_at is null and cn.entity_id = p_entity and cn.path <@ n.path)
   where n.entity_id = p_entity and n.ingest_status = 'parsed';

  select count(*) into v_unparsed from public.nodes where entity_id = p_entity and ingest_status <> 'parsed';
  update public.entities set status = case when v_unparsed = 0 then 'ready' else 'ingesting' end where id = p_entity;

  select count(*) into v_edges from public.concept_edges ce join public.concepts f on f.id = ce.from_concept_id
   where f.entity_id = p_entity and ce.active;

  return jsonb_build_object(
    'status', case when v_unparsed = 0 then 'ready' else 'ingesting' end,
    'unparsed_nodes', v_unparsed,
    'concepts', (select count(*) from public.concepts where entity_id = p_entity and archived_at is null),
    'objectives', (select count(*) from public.objectives o join public.concepts c on c.id = o.concept_id
                    where c.entity_id = p_entity and c.archived_at is null and o.archived_at is null),
    'edges', v_edges,
    'unresolved_refs', v_unresolved,
    'cycles_broken', v_broken,
    'non_teachable_nodes', (select coalesce(jsonb_agg(key order by seq), '[]') from public.nodes
                             where entity_id = p_entity and not teachable));
end $$;

do $$
declare f text;
begin
  foreach f in array array[
    'koro_node_label(text)',
    'koro_create_entity(text,text,text,text,text,text,text,int,uuid)',
    'koro_build_outline(uuid,jsonb)',
    'koro_claim_next_node(uuid,text,int,text)',
    'koro_save_node(uuid,text,jsonb,text)',
    'koro_release_node(uuid,text,text)',
    'koro_finalize_ingest(uuid)']
  loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
  end loop;
end $$;
