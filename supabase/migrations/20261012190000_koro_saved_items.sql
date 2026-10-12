-- Saved items: things the learner wants to learn later (a topic, a question, a link, a
-- paper, a snippet), captured mid-work from the terminal, Claude Code or a Claude chat.
-- Private to the learner. Numbered per learner (#1, #2, ...) so they are easy to refer to.
-- Status: inbox -> planned -> learning -> done, or archived. Nothing is deleted.

create table public.saved_items (
  id           uuid primary key default gen_random_uuid(),
  learner_id   uuid not null references public.learners (id) on delete cascade,
  no           int not null,
  kind         text not null check (kind in ('topic', 'question', 'note', 'url', 'pdf', 'file', 'snippet')),
  title        text not null,
  body         text,                     -- the learner's note, a question, or a snippet
  url          text,
  tags         text[] not null default '{}',
  status       text not null default 'inbox' check (status in ('inbox', 'planned', 'learning', 'done', 'archived')),
  context      jsonb not null default '{}', -- where it came up: client, device, optionally repo/branch
  bucket       text,                     -- an uploaded file (e.g. a PDF) in private storage
  storage_path text,
  filename     text,
  mime_type    text,
  size_bytes   bigint,
  sha256       text,
  entity_id    uuid references public.entities (id) on delete set null, -- once it becomes a book/paper in koro
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (learner_id, no)
);
create index saved_items_learner_idx on public.saved_items (learner_id, status, created_at desc);
create index saved_items_url_idx on public.saved_items (learner_id, url) where url is not null;
create index saved_items_entity_idx on public.saved_items (entity_id);
alter table public.saved_items enable row level security;
revoke all on public.saved_items from anon, authenticated;

create or replace function public.koro_saved_json(s public.saved_items) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_strip_nulls(jsonb_build_object(
    'no', s.no, 'id', s.id, 'kind', s.kind, 'title', s.title, 'body', s.body, 'url', s.url,
    'tags', to_jsonb(s.tags), 'status', s.status, 'context', s.context,
    'file', case when s.storage_path is not null then jsonb_build_object(
              'filename', s.filename, 'mime_type', s.mime_type, 'size_bytes', s.size_bytes,
              'bucket', s.bucket, 'storage_path', s.storage_path) end,
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title) from public.entities e where e.id = s.entity_id),
    'created_at', s.created_at, 'updated_at', s.updated_at))
$$;

create or replace function public.koro_clean_tags(p_tags text[]) returns text[]
language sql immutable as $$
  select coalesce(array_agg(distinct t order by t), '{}')
    from (select lower(btrim(regexp_replace(x, '^#', ''))) as t from unnest(coalesce(p_tags, '{}')) x) y
   where t <> ''
$$;

-- "12", "#12" or a uuid.
create or replace function public.koro_resolve_saved(p_learner uuid, p_ref text) returns public.saved_items
language plpgsql stable set search_path = public as $$
declare
  v public.saved_items;
  v_ref text := btrim(coalesce(p_ref, ''));
begin
  if v_ref ~ '^#?\d+$' then
    select * into v from public.saved_items where learner_id = p_learner and no = ltrim(v_ref, '#')::int;
  elsif v_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select * into v from public.saved_items where learner_id = p_learner and id = v_ref::uuid;
  end if;
  if v.id is null then
    raise exception 'no saved item %', p_ref;
  end if;
  return v;
end $$;

create or replace function public.koro_append_note(p_body text, p_note text) returns text
language sql immutable as $$
  select case
    when coalesce(btrim(p_note), '') = '' then p_body
    when p_body is null or btrim(p_body) = '' then btrim(p_note)
    when position(btrim(p_note) in p_body) > 0 then p_body
    else p_body || E'\n\n' || btrim(p_note) end
$$;

-- Save something to learn later. The same link saved twice is one item: tags merge and
-- the new note is appended.
create or replace function public.koro_save_item(
  p_learner uuid,
  p_title   text,
  p_kind    text default null,
  p_body    text default null,
  p_url     text default null,
  p_tags    text[] default '{}',
  p_context jsonb default '{}',
  p_client  text default 'cli'
) returns jsonb
language plpgsql set search_path = public as $$
declare
  v public.saved_items;
  v_title text := nullif(btrim(coalesce(p_title, '')), '');
  v_url text := nullif(btrim(coalesce(p_url, '')), '');
  v_kind text;
  v_no int;
begin
  if not exists (select 1 from public.learners where id = p_learner) then
    raise exception 'unknown learner %', p_learner;
  end if;
  if v_title is null and v_url is null then
    raise exception 'nothing to save: give a title, topic or URL';
  end if;
  v_title := left(coalesce(v_title, v_url), 300);
  v_kind := coalesce(nullif(p_kind, ''), case when v_url is not null then 'url'
                                             when v_title ~ '\?\s*$' then 'question' else 'topic' end);

  if v_url is not null then
    select * into v from public.saved_items
     where learner_id = p_learner and url = v_url and status <> 'archived'
     order by created_at limit 1;
    if found then
      update public.saved_items set
        tags = koro_clean_tags(tags || coalesce(p_tags, '{}')),
        body = koro_append_note(body, p_body),
        title = case when title = url and v_title <> v_url then v_title else title end,
        updated_at = now()
       where id = v.id
      returning * into v;
      return koro_saved_json(v) || jsonb_build_object('existed', true);
    end if;
  end if;

  perform pg_advisory_xact_lock(hashtext('koro_saved_items'), hashtext(p_learner::text));
  select coalesce(max(no), 0) + 1 into v_no from public.saved_items where learner_id = p_learner;
  insert into public.saved_items (learner_id, no, kind, title, body, url, tags, context)
  values (p_learner, v_no, v_kind, v_title, nullif(btrim(coalesce(p_body, '')), ''), v_url,
          koro_clean_tags(p_tags),
          coalesce(p_context, '{}'::jsonb) || jsonb_build_object('client', coalesce(p_client, 'cli')))
  returning * into v;
  return koro_saved_json(v) || jsonb_build_object('existed', false);
end $$;

-- A file saved with an item (uploaded to private storage by the CLI first).
create or replace function public.koro_attach_saved_file(
  p_learner uuid, p_ref text, p_bucket text, p_storage_path text, p_filename text,
  p_mime text, p_size bigint, p_sha256 text
) returns jsonb
language plpgsql set search_path = public as $$
declare v public.saved_items;
begin
  v := koro_resolve_saved(p_learner, p_ref);
  update public.saved_items set bucket = p_bucket, storage_path = p_storage_path, filename = p_filename,
         mime_type = p_mime, size_bytes = p_size, sha256 = p_sha256, updated_at = now()
   where id = v.id
  returning * into v;
  return koro_saved_json(v);
end $$;

-- List saved items. Default: open ones (inbox, planned, learning), newest first.
-- p_status: one status, 'open', or 'all'.
create or replace function public.koro_saved_items(
  p_learner uuid,
  p_status  text default 'open',
  p_tag     text default null,
  p_query   text default null,
  p_limit   int default 50
) returns jsonb
language sql stable set search_path = public as $$
  with mine as (select * from public.saved_items where learner_id = p_learner),
  picked as (
    select * from mine s
     where (case coalesce(p_status, 'open')
              when 'open' then s.status in ('inbox', 'planned', 'learning')
              when 'all' then true
              else s.status = p_status end)
       and (p_tag is null or lower(regexp_replace(btrim(p_tag), '^#', '')) = any (s.tags))
       and (p_query is null or s.title ilike '%' || p_query || '%' or s.body ilike '%' || p_query || '%'
            or s.url ilike '%' || p_query || '%' or array_to_string(s.tags, ' ') ilike '%' || p_query || '%')
     order by case s.status when 'learning' then 0 when 'planned' then 1 when 'inbox' then 2 else 3 end,
              s.created_at desc
     limit greatest(1, least(coalesce(p_limit, 50), 500)))
  select jsonb_build_object(
    'items', (select coalesce(jsonb_agg(koro_saved_json(p)), '[]') from picked p),
    'counts', (select jsonb_build_object(
                 'inbox', count(*) filter (where status = 'inbox'),
                 'planned', count(*) filter (where status = 'planned'),
                 'learning', count(*) filter (where status = 'learning'),
                 'done', count(*) filter (where status = 'done'),
                 'archived', count(*) filter (where status = 'archived')) from mine),
    'tags', (select coalesce(jsonb_agg(jsonb_build_object('tag', tag, 'n', n) order by n desc, tag), '[]')
               from (select t as tag, count(*) as n from mine, unnest(tags) t
                      where status in ('inbox', 'planned', 'learning') group by t) x),
    'filter', jsonb_strip_nulls(jsonb_build_object('status', coalesce(p_status, 'open'), 'tag', p_tag, 'query', p_query)))
$$;

create or replace function public.koro_saved_item(p_learner uuid, p_ref text) returns jsonb
language sql stable set search_path = public as $$
  select koro_saved_json(koro_resolve_saved(p_learner, p_ref))
$$;

-- Change status, title or tags, append a note, or link it to a book/paper in koro.
create or replace function public.koro_update_saved_item(
  p_learner     uuid,
  p_ref         text,
  p_status      text default null,
  p_title       text default null,
  p_note        text default null,
  p_add_tags    text[] default null,
  p_remove_tags text[] default null,
  p_entity      uuid default null
) returns jsonb
language plpgsql set search_path = public as $$
declare v public.saved_items;
begin
  v := koro_resolve_saved(p_learner, p_ref);
  if p_status is not null and p_status not in ('inbox', 'planned', 'learning', 'done', 'archived') then
    raise exception 'status must be inbox, planned, learning, done or archived';
  end if;
  update public.saved_items set
    status = coalesce(p_status, status),
    title = coalesce(nullif(btrim(coalesce(p_title, '')), ''), title),
    body = koro_append_note(body, p_note),
    tags = (select coalesce(array_agg(t order by t), '{}')
              from unnest(koro_clean_tags(tags || coalesce(p_add_tags, '{}'))) t
             where not (t = any (koro_clean_tags(p_remove_tags)))),
    entity_id = coalesce(p_entity, entity_id),
    updated_at = now()
   where id = v.id
  returning * into v;
  return koro_saved_json(v);
end $$;

do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
            where ns.nspname = 'public' and p.proname like 'koro\_%' loop
    execute format('revoke execute on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;
