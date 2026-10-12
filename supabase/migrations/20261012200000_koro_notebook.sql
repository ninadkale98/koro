-- The notebook: saved things organized into pages that nest as deep as you like.
-- A page has its own write-up (markdown, versioned, every change kept) and holds saved
-- items. Items without a page are the Inbox. Pages are addressed by their outline number
-- (1, 1.1, 1.1.2: computed from order), a name, or a slug path. Nothing is deleted:
-- pages are archived, write-ups keep their history.

create table public.notebook_pages (
  id          uuid primary key default gen_random_uuid(),
  learner_id  uuid not null references public.learners (id) on delete cascade,
  parent_id   uuid references public.notebook_pages (id) on delete cascade,
  title       text not null,
  slug        text not null,
  body        text,
  status      text not null default 'inbox' check (status in ('inbox', 'planned', 'learning', 'done', 'archived')),
  tags        text[] not null default '{}',
  order_index int not null default 1,
  version     int not null default 1,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index notebook_pages_tree_idx on public.notebook_pages (learner_id, parent_id, order_index);
create index notebook_pages_parent_idx on public.notebook_pages (parent_id);

create table public.notebook_page_revisions (
  id         uuid primary key default gen_random_uuid(),
  page_id    uuid not null references public.notebook_pages (id) on delete cascade,
  version    int not null,
  title      text not null,
  body       text,
  client     text,
  created_at timestamptz not null default now(),
  unique (page_id, version)
);

alter table public.saved_items add column page_id uuid references public.notebook_pages (id) on delete set null;
create index saved_items_page_idx on public.saved_items (page_id);

alter table public.notebook_pages enable row level security;
alter table public.notebook_page_revisions enable row level security;
revoke all on public.notebook_pages, public.notebook_page_revisions from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Numbering and lookup
-- ---------------------------------------------------------------------------
create or replace function public.koro_slug(p_text text) returns text
language sql immutable as $$
  select coalesce(nullif(btrim(regexp_replace(lower(coalesce(p_text, '')), '[^a-z0-9]+', '-', 'g'), '-'), ''), 'page')
$$;

-- Every page with its outline number (1, 1.2, 1.2.3). Archived pages (and what's under
-- them) are left out unless p_all, so archiving renumbers the siblings after it.
create or replace function public.koro_page_numbers(p_learner uuid, p_all boolean default false)
returns table (id uuid, parent_id uuid, number text, depth int, sort_key int[])
language sql stable set search_path = public as $$
  with recursive ordered as (
    select p.id, p.parent_id,
           row_number() over (partition by p.parent_id order by p.order_index, p.created_at, p.id)::int as rn
      from public.notebook_pages p
     where p.learner_id = p_learner and (p_all or p.status <> 'archived')
  ), tree as (
    select o.id, o.parent_id, o.rn::text as number, 0 as depth, array[o.rn] as sort_key
      from ordered o where o.parent_id is null
    union all
    select o.id, o.parent_id, t.number || '.' || o.rn, t.depth + 1, t.sort_key || o.rn
      from ordered o join tree t on o.parent_id = t.id
  )
  select * from tree
$$;

-- Each active page's slug path ("linux-internals/signals").
create or replace function public.koro_page_paths(p_learner uuid)
returns table (id uuid, path text)
language sql stable set search_path = public as $$
  with recursive t as (
    select p.id, p.slug::text as path from public.notebook_pages p
     where p.learner_id = p_learner and p.parent_id is null and p.status <> 'archived'
    union all
    select p.id, t.path || '/' || p.slug from public.notebook_pages p join t on p.parent_id = t.id
     where p.status <> 'archived'
  )
  select * from t
$$;

create or replace function public.koro_page_number(p_learner uuid, p_page uuid) returns text
language sql stable set search_path = public as $$
  select number from koro_page_numbers(p_learner, true) where id = p_page
$$;

-- A page by outline number ("1.2"), slug path ("linux/signals"), id, or name (exact title
-- first, then a unique part of a title). "inbox" is not a page: callers handle it.
create or replace function public.koro_resolve_page(p_learner uuid, p_ref text) returns public.notebook_pages
language plpgsql stable set search_path = public as $$
declare
  v public.notebook_pages;
  v_ref text := btrim(coalesce(p_ref, ''));
  v_ids uuid[];
  v_names text;
  v_norm text;
begin
  if v_ref = '' then
    raise exception 'which page? give its number from `koro notebook` (e.g. 1.2) or its name';
  end if;
  if v_ref ~ '^\d+(\.\d+)*$' then
    select p.* into v from public.notebook_pages p
      join koro_page_numbers(p_learner, false) n on n.id = p.id where n.number = v_ref;
  elsif v_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select * into v from public.notebook_pages where learner_id = p_learner and id = v_ref::uuid;
  else
    -- exact title, then slug path (last segment's slug, ancestors checked), then a unique fragment
    select array_agg(id) into v_ids from public.notebook_pages
     where learner_id = p_learner and status <> 'archived' and lower(title) = lower(v_ref);
    if coalesce(array_length(v_ids, 1), 0) = 0 and position('/' in v_ref) > 0 then
      v_norm := array_to_string(array(select koro_slug(x) from unnest(string_to_array(v_ref, '/')) x where btrim(x) <> ''), '/');
      select array_agg(t.id) into v_ids from koro_page_paths(p_learner) t
       where t.path = v_norm or t.path like '%/' || v_norm;
    end if;
    if coalesce(array_length(v_ids, 1), 0) = 0 then
      select array_agg(id) into v_ids from public.notebook_pages
       where learner_id = p_learner and status <> 'archived'
         and (title ilike '%' || v_ref || '%' or slug = koro_slug(v_ref));
    end if;
    if coalesce(array_length(v_ids, 1), 0) = 0 then  -- an archived page, by its exact name
      select array_agg(id) into v_ids from public.notebook_pages
       where learner_id = p_learner and status = 'archived' and lower(title) = lower(v_ref);
    end if;
    if coalesce(array_length(v_ids, 1), 0) > 1 then
      select string_agg(n.number || ' ' || p.title, ', ' order by n.sort_key) into v_names
        from public.notebook_pages p join koro_page_numbers(p_learner, false) n on n.id = p.id
       where p.id = any (v_ids);
      raise exception 'more than one page matches "%": % (use the number)', v_ref, v_names;
    end if;
    if coalesce(array_length(v_ids, 1), 0) = 1 then
      select * into v from public.notebook_pages where id = v_ids[1];
    end if;
  end if;
  if v.id is null then
    raise exception 'no page "%" (see `koro notebook` for numbers and names)', v_ref;
  end if;
  return v;
end $$;

-- ---------------------------------------------------------------------------
-- Write-ups as markdown: headings are addresses inside a page
-- ---------------------------------------------------------------------------
-- Headings outside code fences, each with the line range of its section
-- (up to the next heading of the same or a higher level).
create or replace function public.koro_md_headings(p_body text)
returns table (heading text, level int, line_no int, end_line int)
language plpgsql immutable as $$
declare
  lines text[] := string_to_array(coalesce(p_body, ''), E'\n');
  n int := coalesce(array_length(lines, 1), 0);
  in_fence boolean := false;
  hs text[] := '{}';
  lv int[] := '{}';
  ln int[] := '{}';
  m text[];
  i int;
  j int;
  k int;
  e int;
begin
  for i in 1 .. n loop
    if lines[i] ~ '^\s*(```|~~~)' then
      in_fence := not in_fence;
      continue;
    end if;
    if not in_fence then
      m := regexp_match(lines[i], '^(#{1,6})\s+(\S.*)$');
      if m is not null then
        hs := hs || btrim(regexp_replace(m[2], '\s+#+\s*$', ''));
        lv := lv || length(m[1]);
        ln := ln || i;
      end if;
    end if;
  end loop;
  for k in 1 .. coalesce(array_length(hs, 1), 0) loop
    e := n;
    for j in k + 1 .. array_length(hs, 1) loop
      if lv[j] <= lv[k] then
        e := ln[j] - 1;
        exit;
      end if;
    end loop;
    heading := hs[k]; level := lv[k]; line_no := ln[k]; end_line := e;
    return next;
  end loop;
end $$;

-- The section for a heading name (exact, case-insensitive; else a unique part of one).
create or replace function public.koro_md_find_section(p_body text, p_section text)
returns table (heading text, level int, line_no int, end_line int)
language plpgsql immutable as $$
declare
  v_n int;
  v_all text;
begin
  select count(*) into v_n from koro_md_headings(p_body) h where lower(h.heading) = lower(btrim(p_section));
  if v_n >= 1 then
    return query select * from koro_md_headings(p_body) h where lower(h.heading) = lower(btrim(p_section)) order by h.line_no limit 1;
    return;
  end if;
  select count(*) into v_n from koro_md_headings(p_body) h where h.heading ilike '%' || btrim(p_section) || '%';
  if v_n = 1 then
    return query select * from koro_md_headings(p_body) h where h.heading ilike '%' || btrim(p_section) || '%';
    return;
  end if;
  select string_agg('"' || h.heading || '"', ', ' order by h.line_no) into v_all from koro_md_headings(p_body) h;
  if v_n > 1 then
    raise exception 'more than one heading matches "%": %', p_section, v_all;
  end if;
  raise exception 'no heading "%" in this page%', p_section,
    case when v_all is null then ' (it has no headings yet: add "## ' || btrim(p_section) || '" first, or append to the end)'
         else ' (headings: ' || v_all || ')' end;
end $$;

-- Join two chunks of a write-up with exactly one blank line between them, changing
-- nothing but the seam (the rest of the text, blank lines inside code included, is kept).
create or replace function public.koro_join_text(a text, b text) returns text
language sql immutable as $$
  select case when coalesce(btrim(a, E' \n'), '') = '' then b
              when coalesce(btrim(b, E' \n'), '') = '' then a
              else rtrim(a, E' \n') || E'\n\n' || ltrim(b, E'\n') end
$$;

create or replace function public.koro_lines(p_lines text[]) returns text
language sql immutable as $$
  select array_to_string(coalesce(p_lines, '{}'), E'\n')
$$;

-- ---------------------------------------------------------------------------
-- Page views
-- ---------------------------------------------------------------------------
create or replace function public.koro_page_brief(p_learner uuid, p public.notebook_pages) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'id', p.id, 'number', koro_page_number(p_learner, p.id), 'title', p.title, 'status', p.status,
    'tags', to_jsonb(p.tags), 'version', p.version,
    'items', (select count(*) from public.saved_items s where s.page_id = p.id and s.status <> 'archived'),
    'pages', (select count(*) from public.notebook_pages c where c.parent_id = p.id and c.status <> 'archived'),
    'has_writeup', coalesce(btrim(p.body), '') <> '', 'updated_at', p.updated_at)
$$;

-- Saved items now say which page they are on.
create or replace function public.koro_saved_json(s public.saved_items) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_strip_nulls(jsonb_build_object(
    'no', s.no, 'id', s.id, 'kind', s.kind, 'title', s.title, 'body', s.body, 'url', s.url,
    'tags', to_jsonb(s.tags), 'status', s.status, 'context', s.context,
    'page', (select jsonb_build_object('number', koro_page_number(s.learner_id, p.id), 'title', p.title)
               from public.notebook_pages p where p.id = s.page_id),
    'file', case when s.storage_path is not null then jsonb_build_object(
              'filename', s.filename, 'mime_type', s.mime_type, 'size_bytes', s.size_bytes,
              'bucket', s.bucket, 'storage_path', s.storage_path) end,
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title) from public.entities e where e.id = s.entity_id),
    'created_at', s.created_at, 'updated_at', s.updated_at))
$$;

-- The notebook's table of contents: every page with its number, status and counts, plus
-- the inbox. p_ref limits it to one page and everything under it; p_items lists items too.
create or replace function public.koro_notebook(p_learner uuid, p_ref text default null,
                                                p_items boolean default false, p_all boolean default false)
returns jsonb
language plpgsql stable set search_path = public as $$
declare
  v_root public.notebook_pages;
  v_prefix text;
begin
  if p_ref is not null and lower(btrim(p_ref)) <> 'inbox' then
    v_root := koro_resolve_page(p_learner, p_ref);
    v_prefix := koro_page_number(p_learner, v_root.id);
  end if;
  return jsonb_build_object(
    'pages', (select coalesce(jsonb_agg(jsonb_build_object(
                  'number', n.number, 'depth', n.depth, 'title', p.title, 'status', p.status, 'tags', to_jsonb(p.tags),
                  'items', (select count(*) from public.saved_items s where s.page_id = p.id and (p_all or s.status <> 'archived')),
                  'has_writeup', coalesce(btrim(p.body), '') <> '', 'updated_at', p.updated_at)
                  || case when p_items then jsonb_build_object('item_list',
                       (select coalesce(jsonb_agg(jsonb_build_object('no', s.no, 'kind', s.kind, 'title', s.title,
                                                                     'url', s.url, 'status', s.status)
                                                  order by s.created_at), '[]')
                          from public.saved_items s where s.page_id = p.id and (p_all or s.status <> 'archived')))
                     else '{}'::jsonb end
                  order by n.sort_key), '[]')
                from koro_page_numbers(p_learner, p_all) n join public.notebook_pages p on p.id = n.id
               where lower(coalesce(btrim(p_ref), '')) <> 'inbox'
                 and (v_prefix is null or n.number = v_prefix or n.number like v_prefix || '.%')),
    'inbox', jsonb_build_object(
      'items', (select count(*) from public.saved_items s
                 where s.learner_id = p_learner and s.page_id is null and s.status <> 'archived'),
      'item_list', case when p_items and (p_ref is null or lower(btrim(p_ref)) = 'inbox') then
                     (select coalesce(jsonb_agg(jsonb_build_object('no', s.no, 'kind', s.kind, 'title', s.title,
                                                                   'url', s.url, 'status', s.status)
                                                order by s.created_at), '[]')
                        from public.saved_items s
                       where s.learner_id = p_learner and s.page_id is null and (p_all or s.status <> 'archived'))
                   end),
    'scope', coalesce(v_prefix, case when lower(coalesce(btrim(p_ref), '')) = 'inbox' then 'inbox' end));
end $$;

-- One page: its write-up, the headings inside it, sub-pages and items.
create or replace function public.koro_page(p_learner uuid, p_ref text, p_full boolean default false)
returns jsonb
language plpgsql stable set search_path = public as $$
declare
  p public.notebook_pages;
begin
  p := koro_resolve_page(p_learner, p_ref);
  return koro_page_brief(p_learner, p) || jsonb_build_object(
    'body', p.body,
    'path', (with recursive up as (
               select q.id, q.parent_id, q.title, 0 as d from public.notebook_pages q where q.id = p.parent_id
               union all
               select q.id, q.parent_id, q.title, up.d + 1 from public.notebook_pages q join up on q.id = up.parent_id)
             select coalesce(jsonb_agg(jsonb_build_object('number', koro_page_number(p_learner, up.id), 'title', up.title)
                                       order by up.d desc), '[]') from up),
    'headings', (select coalesce(jsonb_agg(jsonb_build_object('heading', h.heading, 'level', h.level) order by h.line_no), '[]')
                   from koro_md_headings(p.body) h),
    'subpages', (select coalesce(jsonb_agg(koro_page_brief(p_learner, c) order by c.order_index, c.created_at), '[]')
                   from public.notebook_pages c where c.parent_id = p.id and c.status <> 'archived'),
    'item_list', (select coalesce(jsonb_agg(
                     case when p_full then koro_saved_json(s) - 'context' - 'page'
                          else jsonb_strip_nulls(jsonb_build_object('no', s.no, 'kind', s.kind, 'title', s.title,
                                 'url', s.url, 'status', s.status, 'tags', to_jsonb(s.tags),
                                 'chars', length(s.body), 'file', s.filename)) end
                     order by s.created_at), '[]')
                    from public.saved_items s where s.page_id = p.id and s.status <> 'archived'));
end $$;

-- One section of a write-up (for editing just that part).
create or replace function public.koro_page_section(p_learner uuid, p_ref text, p_section text) returns jsonb
language plpgsql stable set search_path = public as $$
declare
  p public.notebook_pages;
  h record;
  lines text[];
begin
  p := koro_resolve_page(p_learner, p_ref);
  select * into h from koro_md_find_section(p.body, p_section);
  lines := string_to_array(coalesce(p.body, ''), E'\n');
  return jsonb_build_object('page', koro_page_brief(p_learner, p), 'heading', h.heading, 'level', h.level,
                            'text', btrim(koro_lines(lines[h.line_no + 1 : h.end_line]), E'\n'), 'version', p.version);
end $$;

-- ---------------------------------------------------------------------------
-- Changing pages
-- ---------------------------------------------------------------------------
create or replace function public.koro_page_place(p_learner uuid, p_parent uuid, p_after uuid) returns int
language plpgsql set search_path = public as $$
declare v_order int;
begin
  if p_after is not null then
    select order_index + 1 into v_order from public.notebook_pages where id = p_after;
    update public.notebook_pages set order_index = order_index + 1
     where learner_id = p_learner and parent_id is not distinct from p_parent and order_index >= v_order;
    return v_order;
  end if;
  select coalesce(max(order_index), 0) + 1 into v_order from public.notebook_pages
   where learner_id = p_learner and parent_id is not distinct from p_parent;
  return v_order;
end $$;

-- A new page, at the end of its parent (or top level), or right after a sibling.
-- The same title under the same parent returns the existing page.
create or replace function public.koro_page_create(
  p_learner uuid,
  p_title   text,
  p_parent  text default null,
  p_after   text default null,
  p_body    text default null,
  p_tags    text[] default '{}',
  p_client  text default 'cli'
) returns jsonb
language plpgsql set search_path = public as $$
declare
  v_parent public.notebook_pages;
  v_after public.notebook_pages;
  v_parent_id uuid;
  v public.notebook_pages;
  v_title text := left(btrim(coalesce(p_title, '')), 200);
begin
  if v_title = '' then raise exception 'a page needs a title'; end if;
  if not exists (select 1 from public.learners where id = p_learner) then
    raise exception 'unknown learner %', p_learner;
  end if;
  if p_after is not null then
    v_after := koro_resolve_page(p_learner, p_after);
    v_parent_id := v_after.parent_id;
  end if;
  if p_parent is not null then
    v_parent := koro_resolve_page(p_learner, p_parent);
    if p_after is not null and v_after.parent_id is distinct from v_parent.id then
      raise exception '"%" is not directly under "%"', v_after.title, v_parent.title;
    end if;
    v_parent_id := v_parent.id;
  end if;
  select * into v from public.notebook_pages
   where learner_id = p_learner and parent_id is not distinct from v_parent_id
     and lower(title) = lower(v_title) and status <> 'archived';
  if found then
    return koro_page_brief(p_learner, v) || jsonb_build_object('existed', true);
  end if;
  perform pg_advisory_xact_lock(hashtext('koro_notebook'), hashtext(p_learner::text));
  insert into public.notebook_pages (learner_id, parent_id, title, slug, body, tags, order_index)
  values (p_learner, v_parent_id, v_title, koro_slug(v_title), nullif(btrim(coalesce(p_body, '')), ''),
          koro_clean_tags(p_tags), koro_page_place(p_learner, v_parent_id, v_after.id))
  returning * into v;
  return koro_page_brief(p_learner, v) || jsonb_build_object('existed', false);
end $$;

-- Title, status and tags (the write-up has its own versioned function).
create or replace function public.koro_page_update(
  p_learner     uuid,
  p_ref         text,
  p_title       text default null,
  p_status      text default null,
  p_add_tags    text[] default null,
  p_remove_tags text[] default null
) returns jsonb
language plpgsql set search_path = public as $$
declare p public.notebook_pages;
begin
  p := koro_resolve_page(p_learner, p_ref);
  if p_status is not null and p_status not in ('inbox', 'planned', 'learning', 'done', 'archived') then
    raise exception 'status must be inbox, planned, learning, done or archived';
  end if;
  update public.notebook_pages set
    title = coalesce(nullif(left(btrim(coalesce(p_title, '')), 200), ''), title),
    slug = koro_slug(coalesce(nullif(btrim(coalesce(p_title, '')), ''), title)),
    status = coalesce(p_status, status),
    tags = (select coalesce(array_agg(t order by t), '{}')
              from unnest(koro_clean_tags(tags || coalesce(p_add_tags, '{}'))) t
             where not (t = any (koro_clean_tags(p_remove_tags)))),
    updated_at = now()
   where id = p.id
  returning * into p;
  return koro_page_brief(p_learner, p);
end $$;

-- Change a write-up without resending all of it. Ops:
--   append          add p_text at the end, or at the end of section p_section
--   replace_section replace what is under heading p_section (the heading stays)
--   replace_text    replace p_find (must appear exactly once) with p_text
--   replace         the whole write-up
-- p_if_version: refuse if the page changed since it was read. Every change is kept.
create or replace function public.koro_page_write(
  p_learner    uuid,
  p_ref        text,
  p_op         text,
  p_text       text default null,
  p_section    text default null,
  p_find       text default null,
  p_if_version int default null,
  p_client     text default 'cli'
) returns jsonb
language plpgsql set search_path = public as $$
declare
  p public.notebook_pages;
  h record;
  lines text[];
  v_body text;
  v_text text := coalesce(p_text, '');
  v_n int;
begin
  p := koro_resolve_page(p_learner, p_ref);
  select * into p from public.notebook_pages where id = p.id for update;
  if p_if_version is not null and p_if_version <> p.version then
    raise exception 'page "%" changed since you read it (you had version %, it is now %): read it again and redo the edit',
      p.title, p_if_version, p.version;
  end if;
  lines := string_to_array(coalesce(p.body, ''), E'\n');

  if p_op = 'replace' then
    v_body := v_text;
  elsif p_op = 'append' and p_section is null then
    if btrim(v_text) = '' then raise exception 'nothing to append'; end if;
    v_body := koro_join_text(p.body, btrim(v_text, E'\n'));
  elsif p_op in ('append', 'replace_section') then
    if p_section is null then raise exception '% needs a section (a heading in the page)', p_op; end if;
    select * into h from koro_md_find_section(p.body, p_section);
    if p_op = 'append' then
      if btrim(v_text) = '' then raise exception 'nothing to append'; end if;
      v_body := koro_join_text(koro_join_text(koro_lines(lines[1 : h.end_line]), btrim(v_text, E'\n')),
                               koro_lines(lines[h.end_line + 1 : array_length(lines, 1)]));
    else
      v_body := koro_join_text(koro_join_text(koro_lines(lines[1 : h.line_no]), btrim(v_text, E'\n')),
                               koro_lines(lines[h.end_line + 1 : array_length(lines, 1)]));
    end if;
  elsif p_op = 'replace_text' then
    if coalesce(p_find, '') = '' then raise exception 'replace_text needs the text to find'; end if;
    v_n := (length(coalesce(p.body, '')) - length(replace(coalesce(p.body, ''), p_find, ''))) / length(p_find);
    if v_n = 0 then
      raise exception 'that text is not in page "%" (it may have changed: read it again)', p.title;
    elsif v_n > 1 then
      raise exception 'that text appears % times in page "%": include more of it so it is unique', v_n, p.title;
    end if;
    v_body := replace(p.body, p_find, v_text);
  else
    raise exception 'unknown op %: use append, replace_section, replace_text or replace', p_op;
  end if;
  v_body := nullif(btrim(v_body, E' \n'), '');

  if v_body is not distinct from p.body then
    return koro_page_brief(p_learner, p) || jsonb_build_object('changed', false);
  end if;
  insert into public.notebook_page_revisions (page_id, version, title, body, client)
  values (p.id, p.version, p.title, p.body, p_client);
  update public.notebook_pages set body = v_body, version = version + 1, updated_at = now()
   where id = p.id
  returning * into p;
  return koro_page_brief(p_learner, p) || jsonb_build_object('changed', true, 'chars', length(p.body));
end $$;

-- Move a page (and everything under it) under another page, to the top level, or after a sibling.
create or replace function public.koro_page_move(
  p_learner uuid,
  p_ref     text,
  p_parent  text default null,
  p_after   text default null,
  p_top     boolean default false
) returns jsonb
language plpgsql set search_path = public as $$
declare
  p public.notebook_pages;
  v_parent public.notebook_pages;
  v_after public.notebook_pages;
  v_parent_id uuid;
  v_order int;
begin
  p := koro_resolve_page(p_learner, p_ref);
  if p_after is not null then
    v_after := koro_resolve_page(p_learner, p_after);
    if v_after.id = p.id then raise exception 'a page cannot go after itself'; end if;
    v_parent_id := v_after.parent_id;
  elsif p_parent is not null then
    v_parent := koro_resolve_page(p_learner, p_parent);
    v_parent_id := v_parent.id;
  elsif not p_top then
    raise exception 'move where? give a parent page, a page to go after, or top level';
  end if;
  if v_parent_id is not null and (v_parent_id = p.id or exists (
       with recursive up as (select q.id, q.parent_id from public.notebook_pages q where q.id = v_parent_id
                             union all
                             select q.id, q.parent_id from public.notebook_pages q join up on q.id = up.parent_id)
       select 1 from up where up.id = p.id)) then
    raise exception 'cannot move "%" inside itself', p.title;
  end if;
  perform pg_advisory_xact_lock(hashtext('koro_notebook'), hashtext(p_learner::text));
  v_order := koro_page_place(p_learner, v_parent_id, v_after.id);  -- may shift siblings, so not inside the update
  update public.notebook_pages set parent_id = v_parent_id, order_index = v_order, updated_at = now()
   where id = p.id
  returning * into p;
  return koro_page_brief(p_learner, p);
end $$;

create or replace function public.koro_page_history(p_learner uuid, p_ref text) returns jsonb
language plpgsql stable set search_path = public as $$
declare p public.notebook_pages;
begin
  p := koro_resolve_page(p_learner, p_ref);
  return koro_page_brief(p_learner, p) || jsonb_build_object('history',
    (select coalesce(jsonb_agg(jsonb_build_object('version', r.version, 'title', r.title, 'chars', coalesce(length(r.body), 0),
                                                  'client', r.client, 'replaced_at', r.created_at,
                                                  'starts', left(coalesce(r.body, ''), 120))
                               order by r.version desc), '[]')
       from public.notebook_page_revisions r where r.page_id = p.id));
end $$;

-- Bring back an earlier write-up (the current one is kept in history too).
create or replace function public.koro_page_restore(p_learner uuid, p_ref text, p_version int, p_client text default 'cli')
returns jsonb
language plpgsql set search_path = public as $$
declare
  p public.notebook_pages;
  r public.notebook_page_revisions;
begin
  p := koro_resolve_page(p_learner, p_ref);
  select * into r from public.notebook_page_revisions where page_id = p.id and version = p_version;
  if not found then raise exception 'page "%" has no version %', p.title, p_version; end if;
  return koro_page_write(p_learner, p.id::text, 'replace', coalesce(r.body, ''), null, null, null, p_client);
end $$;

-- ---------------------------------------------------------------------------
-- Items on pages
-- ---------------------------------------------------------------------------
-- File saved items on a page, or back in the inbox (p_page null or 'inbox').
create or replace function public.koro_file_items(p_learner uuid, p_items text[], p_page text) returns jsonb
language plpgsql set search_path = public as $$
declare
  v_page public.notebook_pages;
  v_ref text;
  v public.saved_items;
  v_out jsonb := '[]';
begin
  if p_page is not null and lower(btrim(p_page)) <> 'inbox' then
    v_page := koro_resolve_page(p_learner, p_page);
  end if;
  foreach v_ref in array coalesce(p_items, '{}') loop
    v := koro_resolve_saved(p_learner, v_ref);
    update public.saved_items set page_id = v_page.id, updated_at = now() where id = v.id returning * into v;
    v_out := v_out || koro_saved_json(v);
  end loop;
  return jsonb_build_object('page', case when v_page.id is null then null else koro_page_brief(p_learner, v_page) end,
                            'items', v_out);
end $$;

-- Save something, optionally straight onto a page (koro_save_item keeps working for older clients).
create or replace function public.koro_capture(
  p_learner uuid,
  p_title   text,
  p_kind    text default null,
  p_body    text default null,
  p_url     text default null,
  p_tags    text[] default '{}',
  p_context jsonb default '{}',
  p_client  text default 'cli',
  p_page    text default null
) returns jsonb
language plpgsql set search_path = public as $$
declare
  r jsonb;
  v_page public.notebook_pages;
begin
  if p_page is not null and lower(btrim(p_page)) <> 'inbox' then
    v_page := koro_resolve_page(p_learner, p_page);  -- fail before saving if the page is wrong
  end if;
  r := koro_save_item(p_learner, p_title, p_kind, p_body, p_url, p_tags, p_context, p_client);
  if v_page.id is not null then
    update public.saved_items set page_id = v_page.id where id = (r->>'id')::uuid;
    r := koro_saved_json((select s from public.saved_items s where s.id = (r->>'id')::uuid))
         || jsonb_build_object('existed', r->'existed');
  end if;
  return r;
end $$;

-- Rewrite an item's title and note as a whole (editing it in an editor).
create or replace function public.koro_set_saved_text(p_learner uuid, p_ref text, p_title text, p_body text)
returns jsonb
language plpgsql set search_path = public as $$
declare v public.saved_items;
begin
  v := koro_resolve_saved(p_learner, p_ref);
  update public.saved_items set
    title = coalesce(nullif(left(btrim(coalesce(p_title, '')), 300), ''), title),
    body = nullif(btrim(coalesce(p_body, ''), E' \n'), ''),
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
