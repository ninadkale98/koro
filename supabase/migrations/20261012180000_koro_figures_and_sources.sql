-- Complete ingest: the source file, every figure, and each section's original
-- text. All of it is private to the uploader: a private Storage bucket, tables
-- locked to the secret key, and never part of anything shared.

insert into storage.buckets (id, name, public)
values ('koro-private', 'koro-private', false)
on conflict (id) do nothing;

-- Figures are assets of kind 'image' with a label ("2.13"). Rows are registered
-- first (where the figure is) and filled in when the file is uploaded.
alter table public.assets
  add column label       text,
  add column caption     text,
  add column page        int,
  add column bbox        jsonb,          -- crop box in PDF points [x0, top, x1, bottom], page-relative
  add column width       int,
  add column height      int,
  add column uploaded_at timestamptz;
create unique index assets_entity_kind_label_idx on public.assets (entity_id, kind, label) where label is not null;

-- Each section's original text, for grounding answers in what the source says.
create table public.node_sources (
  node_id     uuid primary key references public.nodes (id) on delete cascade,
  entity_id   uuid not null references public.entities (id) on delete cascade,
  text        text not null,
  chars       int generated always as (length(text)) stored,
  updated_at  timestamptz not null default now()
);
create index node_sources_entity_idx on public.node_sources (entity_id);
alter table public.node_sources enable row level security;
revoke all on public.node_sources from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Figures
-- ---------------------------------------------------------------------------
create or replace function public.koro_figure_json(a public.assets) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'asset_id', a.id, 'label', a.label, 'caption', a.caption, 'page', a.page,
    'bucket', a.bucket, 'storage_path', a.storage_path, 'uploaded', a.uploaded_at is not null,
    'width', a.width, 'height', a.height,
    'concept_key', (select c.key from public.concepts c where c.id = a.concept_id),
    'node_key', coalesce((select n.key from public.nodes n where n.id = a.node_id),
                         (select n.key from public.concepts c join public.nodes n on n.id = c.node_id where c.id = a.concept_id)))
$$;

-- p_figures: [{label, caption, page, bbox, node_key, storage_path}]
create or replace function public.koro_register_figures(p_entity uuid, p_figures jsonb) returns jsonb
language plpgsql set search_path = public as $$
declare
  f jsonb;
  v_node uuid;
  v_n int := 0;
begin
  if jsonb_typeof(p_figures) <> 'array' then raise exception 'p_figures must be a JSON array'; end if;
  for f in select value from jsonb_array_elements(p_figures) loop
    if coalesce(f->>'label', '') = '' or coalesce(f->>'storage_path', '') = '' then
      raise exception 'each figure needs label and storage_path';
    end if;
    select id into v_node from public.nodes where entity_id = p_entity and key = f->>'node_key';
    insert into public.assets (entity_id, node_id, kind, label, caption, page, bbox, bucket, storage_path, mime_type, description)
    values (p_entity, v_node, 'image', f->>'label', f->>'caption', (f->>'page')::int, f->'bbox',
            'koro-private', f->>'storage_path', 'image/png', f->>'caption')
    on conflict (entity_id, kind, label) where label is not null do update set
      caption = excluded.caption, page = excluded.page, bbox = excluded.bbox,
      storage_path = excluded.storage_path, description = excluded.description,
      -- keep a concept link if one exists; otherwise attach to the node
      node_id = case when public.assets.concept_id is null then excluded.node_id else null end,
      uploaded_at = case when public.assets.bbox is distinct from excluded.bbox then null else public.assets.uploaded_at end;
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('registered', v_n,
    'pending_upload', (select count(*) from public.assets where entity_id = p_entity and kind = 'image' and uploaded_at is null));
end $$;

-- Attach figures to the concept and beat they illustrate: [{label, concept_key, beat}]
create or replace function public.koro_link_figures(p_entity uuid, p_links jsonb) returns jsonb
language plpgsql set search_path = public as $$
declare
  l jsonb;
  v_concept public.concepts;
  v_beats jsonb;
  v_beat int;
  v_n int := 0;
  v_missing jsonb := '[]'::jsonb;
begin
  for l in select value from jsonb_array_elements(p_links) loop
    select * into v_concept from public.concepts
     where entity_id = p_entity and key = l->>'concept_key' and archived_at is null;
    if not found or not exists (select 1 from public.assets where entity_id = p_entity and kind = 'image' and label = l->>'label') then
      v_missing := v_missing || l;
      continue;
    end if;
    update public.assets set concept_id = v_concept.id, node_id = null
     where entity_id = p_entity and kind = 'image' and label = l->>'label';
    v_beats := v_concept.teaching_content->'beats';
    v_beat := (l->>'beat')::int;
    if v_beat is not null and jsonb_typeof(v_beats) = 'array' and v_beat >= 0 and v_beat < jsonb_array_length(v_beats) then
      -- a beat can show several figures: keep a list, never duplicate
      update public.concepts set
        teaching_content = jsonb_set(teaching_content, array['beats', v_beat::text, 'figures'],
          (select to_jsonb(array(select distinct x from unnest(
             array(select jsonb_array_elements_text(coalesce(teaching_content #> array['beats', v_beat::text, 'figures'], '[]'::jsonb)))
             || array[l->>'label']) x order by x)))),
        updated_at = now()
       where id = v_concept.id;
    end if;
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('linked', v_n, 'skipped', v_missing);
end $$;

-- Figures for a label, a concept key, or a section (its whole subtree).
create or replace function public.koro_figures(p_entity uuid, p_ref text) returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare
  v_path ltree;
  v_concept uuid;
begin
  if exists (select 1 from public.assets where entity_id = p_entity and kind = 'image' and label = p_ref) then
    return (select coalesce(jsonb_agg(koro_figure_json(a)), '[]') from public.assets a
             where a.entity_id = p_entity and a.kind = 'image' and a.label = p_ref);
  end if;
  select id into v_concept from public.concepts where entity_id = p_entity and key = p_ref and archived_at is null;
  if v_concept is not null then
    return (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]') from public.assets a
             where a.kind = 'image' and a.concept_id = v_concept);
  end if;
  select path into v_path from public.nodes where entity_id = p_entity and key = p_ref;
  if v_path is null then raise exception 'no figure, concept or section %', p_ref; end if;
  return (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]')
            from public.assets a
            left join public.concepts c on c.id = a.concept_id
            join public.nodes n on n.id = coalesce(a.node_id, c.node_id)
           where a.entity_id = p_entity and a.kind = 'image' and n.path <@ v_path);
end $$;

-- ---------------------------------------------------------------------------
-- Uploads (done by the CLI from the learner's own copy)
-- ---------------------------------------------------------------------------
create or replace function public.koro_pending_uploads(p_entity uuid) returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('asset_id', id, 'kind', kind, 'label', label, 'page', page, 'bbox', bbox,
                                               'bucket', bucket, 'storage_path', storage_path) order by page, label), '[]')
    from public.assets where entity_id = p_entity and uploaded_at is null
$$;

create or replace function public.koro_mark_uploaded(p_asset uuid, p_size bigint, p_sha256 text,
                                                     p_width int default null, p_height int default null)
returns jsonb
language plpgsql set search_path = public as $$
begin
  update public.assets set size_bytes = p_size, sha256 = p_sha256, width = p_width, height = p_height,
         uploaded_at = now()
   where id = p_asset;
  if not found then raise exception 'unknown asset %', p_asset; end if;
  return jsonb_build_object('asset_id', p_asset, 'uploaded', true);
end $$;

create or replace function public.koro_register_source(p_entity uuid, p_storage_path text, p_size bigint,
                                                       p_sha256 text, p_mime text default 'application/pdf')
returns jsonb
language plpgsql set search_path = public as $$
declare v uuid;
begin
  insert into public.assets (entity_id, kind, label, caption, bucket, storage_path, mime_type, size_bytes, sha256,
                             description, uploaded_at)
  values (p_entity, 'source', 'source', 'Original source file', 'koro-private', p_storage_path, p_mime, p_size,
          p_sha256, 'Original source file', now())
  on conflict (entity_id, kind, label) where label is not null do update set
    storage_path = excluded.storage_path, size_bytes = excluded.size_bytes, sha256 = excluded.sha256,
    mime_type = excluded.mime_type, uploaded_at = now()
  returning id into v;
  return jsonb_build_object('asset_id', v, 'storage_path', p_storage_path);
end $$;

-- ---------------------------------------------------------------------------
-- Section text
-- ---------------------------------------------------------------------------
-- p_texts: {"<node key>": "<text>", ...}
create or replace function public.koro_save_node_sources(p_entity uuid, p_texts jsonb) returns jsonb
language plpgsql set search_path = public as $$
declare
  r record;
  v_node uuid;
  v_n int := 0;
  v_unknown jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(p_texts) <> 'object' then raise exception 'p_texts must be an object of node key -> text'; end if;
  for r in select key, value #>> '{}' as text from jsonb_each(p_texts) loop
    select id into v_node from public.nodes where entity_id = p_entity and key = r.key;
    if v_node is null then
      v_unknown := v_unknown || to_jsonb(r.key);
      continue;
    end if;
    insert into public.node_sources (node_id, entity_id, text) values (v_node, p_entity, coalesce(r.text, ''))
    on conflict (node_id) do update set text = excluded.text, updated_at = now();
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('saved', v_n, 'unknown_keys', v_unknown,
    'sections_with_text', (select count(*) from public.node_sources where entity_id = p_entity));
end $$;

create or replace function public.koro_read_section(p_entity uuid, p_node_key text) returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare n public.nodes;
begin
  select * into n from public.nodes where entity_id = p_entity and key = p_node_key;
  if not found then raise exception 'unknown section %', p_node_key; end if;
  return jsonb_build_object(
    'section', jsonb_build_object('key', n.key, 'title', n.title, 'summary', n.summary,
                                  'page_start', n.page_start, 'page_end', n.page_end),
    'text', (select s.text from public.node_sources s where s.node_id = n.id),
    'subsections', (select coalesce(jsonb_agg(jsonb_build_object('key', c.key, 'title', c.title) order by c.order_index), '[]')
                      from public.nodes c where c.parent_id = n.id),
    'figures', koro_figures(p_entity, p_node_key),
    'concepts', (select coalesce(jsonb_agg(jsonb_build_object('key', c.key, 'name', c.name) order by c.order_index), '[]')
                   from public.concepts c where c.node_id = n.id and c.archived_at is null));
end $$;

-- ---------------------------------------------------------------------------
-- Lessons include their figures
-- ---------------------------------------------------------------------------
create or replace function public.koro_teach(p_learner uuid, p_concept_ref text, p_entity uuid default null,
                                             p_client text default 'cli')
returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_cid uuid;
  v_touch jsonb;
begin
  v_cid := koro_resolve_concept(p_concept_ref, p_entity);
  v_touch := koro_touch(p_learner, v_cid, p_client, true);
  perform koro_recompute_progress(p_learner, v_cid);

  return (
    select jsonb_build_object(
      'concept', jsonb_build_object('id', c.id, 'key', c.key, 'name', c.name, 'summary', c.summary,
                                    'modality', c.modality, 'teaching_content', c.teaching_content),
      'entity', jsonb_build_object('id', e.id, 'title', e.title, 'source_ref', e.source_ref),
      'node', jsonb_build_object('key', n.key, 'title', n.title, 'summary', n.summary,
                                 'page_start', n.page_start, 'page_end', n.page_end),
      'figures', (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]')
                    from public.assets a where a.kind = 'image' and a.concept_id = c.id),
      'progress', jsonb_build_object('status', p.status, 'mastery', round(p.mastery::numeric, 2),
                                     'stability_days', round(p.stability_days::numeric, 1),
                                     'seen_at', p.seen_at, 'notes', p.notes),
      'objectives', (select coalesce(jsonb_agg(jsonb_build_object(
                        'key', ob.key, 'description', ob.description,
                        'status', coalesce(op.status, 'unseen'), 'mastery', round(coalesce(op.mastery, 0)::numeric, 2),
                        'misconception', op.misconception) order by ob.order_index), '[]')
                       from public.objectives ob
                       left join public.objective_progress op on op.objective_id = ob.id and op.learner_id = p_learner
                      where ob.concept_id = c.id and ob.archived_at is null),
      'prerequisites', (select coalesce(jsonb_agg(koro_concept_card(p_learner, ce.to_concept_id)), '[]')
                          from public.concept_edges ce
                          join public.concepts t on t.id = ce.to_concept_id and t.archived_at is null
                         where ce.from_concept_id = c.id and ce.active),
      'recent_evidence', (select coalesce(jsonb_agg(x order by x->>'at' desc), '[]') from (
                            select jsonb_build_object('id', a.id, 'kind', a.kind, 'verdict', a.verdict,
                                     'objective', ob.key, 'feedback', a.feedback,
                                     'misconception', a.misconception, 'at', a.created_at) as x
                              from public.assessments a
                              left join public.objectives ob on ob.id = a.objective_id
                             where a.learner_id = p_learner and a.concept_id = c.id
                             order by a.created_at desc limit 8) recent),
      'has_source_text', exists (select 1 from public.node_sources s where s.node_id = n.id),
      'session', v_touch)
    from public.concepts c
    join public.entities e on e.id = c.entity_id
    join public.nodes n on n.id = c.node_id
    left join public.progress p on p.concept_id = c.id and p.learner_id = p_learner
    where c.id = v_cid);
end $$;

do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
            where ns.nspname = 'public' and p.proname like 'koro\_%' loop
    execute format('revoke execute on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;
