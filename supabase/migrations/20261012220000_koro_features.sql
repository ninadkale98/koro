-- Feature requests: things the learner wants to build, captured in one line from any tab
-- (terminal, Claude Code, a Claude chat) and picked up later ("tonight's build").
-- Separate from saved items, which are things to learn. Numbered per learner (F1, F2, ...),
-- grouped by project (koro, rterm, ...). Status: idea -> planned -> building -> done, or
-- dropped. Nothing is deleted.

create table public.feature_requests (
  id          uuid primary key default gen_random_uuid(),
  learner_id  uuid not null references public.learners (id) on delete cascade,
  no          int not null,
  project     text,                      -- slug, e.g. koro; null = no project yet
  title       text not null,
  body        text,                      -- details: what, why, how it should work (markdown)
  status      text not null default 'idea' check (status in ('idea', 'planned', 'building', 'done', 'dropped')),
  priority    text not null default 'normal' check (priority in ('high', 'normal', 'low')),
  tags        text[] not null default '{}',
  context     jsonb not null default '{}', -- where it came up: client, device, optionally repo/branch
  done_note   text,                      -- what shipped: commit, version, PR
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  done_at     timestamptz,
  unique (learner_id, no)
);
create index feature_requests_learner_idx on public.feature_requests (learner_id, status, project);
alter table public.feature_requests enable row level security;
revoke all on public.feature_requests from anon, authenticated;

create or replace function public.koro_project_slug(p_project text) returns text
language sql immutable set search_path = public as $$
  select case when coalesce(btrim(p_project), '') in ('', '-', 'none') then null else koro_slug(p_project) end
$$;

create or replace function public.koro_feature_json(f public.feature_requests) returns jsonb
language sql stable as $$
  select jsonb_strip_nulls(jsonb_build_object(
    'no', f.no, 'ref', 'F' || f.no, 'id', f.id, 'project', f.project, 'title', f.title, 'body', f.body,
    'status', f.status, 'priority', f.priority, 'tags', to_jsonb(f.tags), 'context', f.context,
    'done_note', f.done_note, 'created_at', f.created_at, 'updated_at', f.updated_at, 'done_at', f.done_at))
$$;

-- "12", "#12", "F12" or a uuid.
create or replace function public.koro_resolve_feature(p_learner uuid, p_ref text) returns public.feature_requests
language plpgsql stable set search_path = public as $$
declare
  v public.feature_requests;
  v_ref text := btrim(coalesce(p_ref, ''));
begin
  if v_ref ~* '^[#f]?\d+$' then
    select * into v from public.feature_requests
     where learner_id = p_learner and no = regexp_replace(v_ref, '^[#fF]', '')::int;
  elsif v_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select * into v from public.feature_requests where learner_id = p_learner and id = v_ref::uuid;
  end if;
  if v.id is null then
    raise exception 'no feature request %', p_ref;
  end if;
  return v;
end $$;

-- Add a feature request. The same title in the same project, while still open, is one
-- request: the new details are appended and tags merge (existed: true).
create or replace function public.koro_add_feature(
  p_learner  uuid,
  p_title    text,
  p_body     text default null,
  p_project  text default null,
  p_priority text default null,
  p_tags     text[] default '{}',
  p_context  jsonb default '{}',
  p_client   text default 'cli'
) returns jsonb
language plpgsql set search_path = public as $$
declare
  v public.feature_requests;
  v_title text := left(nullif(btrim(coalesce(p_title, '')), ''), 300);
  v_project text := koro_project_slug(p_project);
  v_priority text := coalesce(nullif(lower(btrim(coalesce(p_priority, ''))), ''), 'normal');
  v_no int;
begin
  if not exists (select 1 from public.learners where id = p_learner) then
    raise exception 'unknown learner %', p_learner;
  end if;
  if v_title is null then raise exception 'a feature request needs a title'; end if;
  if v_priority not in ('high', 'normal', 'low') then raise exception 'priority must be high, normal or low'; end if;

  select * into v from public.feature_requests
   where learner_id = p_learner and project is not distinct from v_project and lower(title) = lower(v_title)
     and status in ('idea', 'planned', 'building')
   order by created_at limit 1;
  if found then
    update public.feature_requests set
      body = koro_append_note(body, p_body),
      tags = koro_clean_tags(tags || coalesce(p_tags, '{}')),
      priority = case when p_priority is null then priority else v_priority end,
      updated_at = now()
     where id = v.id
    returning * into v;
    return koro_feature_json(v) || jsonb_build_object('existed', true);
  end if;

  perform pg_advisory_xact_lock(hashtext('koro_feature_requests'), hashtext(p_learner::text));
  select coalesce(max(no), 0) + 1 into v_no from public.feature_requests where learner_id = p_learner;
  insert into public.feature_requests (learner_id, no, project, title, body, priority, tags, context)
  values (p_learner, v_no, v_project, v_title, nullif(btrim(coalesce(p_body, ''), E' \n'), ''), v_priority,
          koro_clean_tags(p_tags),
          coalesce(p_context, '{}'::jsonb) || jsonb_build_object('client', coalesce(p_client, 'cli')))
  returning * into v;
  return koro_feature_json(v) || jsonb_build_object('existed', false);
end $$;

-- The list. Default: open requests (idea, planned, building): building first, then planned,
-- then ideas; high priority first; oldest first. p_status: one status, 'open' or 'all'.
create or replace function public.koro_features(
  p_learner uuid,
  p_status  text default 'open',
  p_project text default null,
  p_query   text default null,
  p_limit   int default 100
) returns jsonb
language sql stable set search_path = public as $$
  with mine as (select * from public.feature_requests where learner_id = p_learner),
  picked as (
    select * from mine f
     where (case coalesce(p_status, 'open')
              when 'open' then f.status in ('idea', 'planned', 'building')
              when 'all' then true
              else f.status = p_status end)
       and (p_project is null or f.project is not distinct from koro_project_slug(p_project))
       and (p_query is null or f.title ilike '%' || p_query || '%' or f.body ilike '%' || p_query || '%'
            or array_to_string(f.tags, ' ') ilike '%' || p_query || '%')
     order by case f.status when 'building' then 0 when 'planned' then 1 when 'idea' then 2 else 3 end,
              case f.priority when 'high' then 0 when 'normal' then 1 else 2 end,
              case when f.status in ('done', 'dropped') then -extract(epoch from coalesce(f.done_at, f.updated_at)) end,
              f.created_at
     limit greatest(1, least(coalesce(p_limit, 100), 500)))
  select jsonb_build_object(
    'items', (select coalesce(jsonb_agg(koro_feature_json(p)), '[]') from picked p),
    'counts', (select jsonb_build_object(
                 'idea', count(*) filter (where status = 'idea'),
                 'planned', count(*) filter (where status = 'planned'),
                 'building', count(*) filter (where status = 'building'),
                 'done', count(*) filter (where status = 'done'),
                 'dropped', count(*) filter (where status = 'dropped')) from mine),
    'projects', (select coalesce(jsonb_agg(jsonb_build_object('project', project, 'open', n_open, 'done', n_done)
                                           order by n_open desc, project nulls last), '[]')
                   from (select project,
                                count(*) filter (where status in ('idea', 'planned', 'building')) as n_open,
                                count(*) filter (where status = 'done') as n_done
                           from mine group by project) x),
    'filter', jsonb_strip_nulls(jsonb_build_object('status', coalesce(p_status, 'open'),
                                                   'project', koro_project_slug(p_project), 'query', p_query)))
$$;

create or replace function public.koro_feature(p_learner uuid, p_ref text) returns jsonb
language sql stable set search_path = public as $$
  select koro_feature_json(koro_resolve_feature(p_learner, p_ref))
$$;

-- Move a request along, rename it, add to or rewrite its details, change project, priority
-- or tags. Done records when (and p_done_note what shipped); reopening clears it.
create or replace function public.koro_update_feature(
  p_learner     uuid,
  p_ref         text,
  p_status      text default null,
  p_title       text default null,
  p_note        text default null,
  p_body        text default null,
  p_project     text default null,
  p_priority    text default null,
  p_add_tags    text[] default null,
  p_remove_tags text[] default null,
  p_done_note   text default null
) returns jsonb
language plpgsql set search_path = public as $$
declare v public.feature_requests;
begin
  v := koro_resolve_feature(p_learner, p_ref);
  if p_status is not null and p_status not in ('idea', 'planned', 'building', 'done', 'dropped') then
    raise exception 'status must be idea, planned, building, done or dropped';
  end if;
  if p_priority is not null and p_priority not in ('high', 'normal', 'low') then
    raise exception 'priority must be high, normal or low';
  end if;
  update public.feature_requests set
    status = coalesce(p_status, status),
    title = coalesce(left(nullif(btrim(coalesce(p_title, '')), ''), 300), title),
    body = case when p_body is not null then nullif(btrim(p_body, E' \n'), '')
                else koro_append_note(body, p_note) end,
    project = case when p_project is not null then koro_project_slug(p_project) else project end,
    priority = coalesce(p_priority, priority),
    tags = (select coalesce(array_agg(t order by t), '{}')
              from unnest(koro_clean_tags(tags || coalesce(p_add_tags, '{}'))) t
             where not (t = any (koro_clean_tags(p_remove_tags)))),
    done_note = case when nullif(btrim(coalesce(p_done_note, '')), '') is not null
                     then koro_append_note(done_note, p_done_note) else done_note end,
    done_at = case when p_status = 'done' and status <> 'done' then now()
                   when p_status is not null and p_status <> 'done' then null
                   else done_at end,
    updated_at = now()
   where id = v.id
  returning * into v;
  return koro_feature_json(v);
end $$;

do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
            where ns.nspname = 'public' and p.proname like 'koro\_%' loop
    execute format('revoke execute on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;
