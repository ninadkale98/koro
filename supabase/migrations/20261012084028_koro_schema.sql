-- Koro schema: shared content layer + private learner layer.
-- Design rule: every piece of evidence about what a learner knows is one
-- append-only row in `assessments`; progress tables are calculated from it.

create extension if not exists ltree with schema extensions;
create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------------
-- Learners (single user today; auth_user_id links to Supabase Auth later)
-- ---------------------------------------------------------------------------
create table public.learners (
  id            uuid primary key default gen_random_uuid(),
  display_name  text not null,
  auth_user_id  uuid unique references auth.users (id) on delete set null,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Shared content layer
-- ---------------------------------------------------------------------------
create table public.entities (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  author          text,
  source_type     text not null check (source_type in ('book', 'paper', 'repo')),
  source_ref      text,                    -- file name, arXiv id, or git URL
  canonical_id    text unique,             -- isbn:..., doi:..., arxiv:..., git:host/owner/repo
  source_hash     text unique,             -- sha256 of the uploaded file
  source_version  text,                    -- commit SHA, edition, or arXiv version
  page_count      int,
  status          text not null default 'ingesting'
                  check (status in ('ingesting', 'ready', 'failed')),
  created_by      uuid references public.learners (id) on delete set null,
  created_at      timestamptz not null default now()
);

create table public.entity_links (
  from_entity_id  uuid not null references public.entities (id) on delete cascade,
  to_entity_id    uuid not null references public.entities (id) on delete cascade,
  kind            text not null check (kind in ('cites', 'implements', 'new_edition_of', 'companion')),
  created_at      timestamptz not null default now(),
  primary key (from_entity_id, to_entity_id, kind),
  check (from_entity_id <> to_entity_id)
);

create table public.nodes (
  id             uuid primary key default gen_random_uuid(),
  entity_id      uuid not null references public.entities (id) on delete cascade,
  parent_id      uuid references public.nodes (id) on delete cascade,
  key            text not null,            -- stable, e.g. '2.4.1'; re-ingest updates by key
  kind           text not null check (kind in ('chapter', 'section', 'subsection', 'appendix',
                                               'part', 'directory', 'module', 'file')),
  title          text not null,
  summary        text,
  order_index    int not null default 0,   -- order among siblings
  seq            int not null default 0,   -- global reading order within the entity
  depth          int not null default 0,
  path           extensions.ltree not null,
  page_start     int,
  page_end       int,
  teachable      boolean not null default true,
  ingest_status  text not null default 'pending'
                 check (ingest_status in ('pending', 'parsing', 'parsed', 'failed')),
  ingest_error   text,
  claimed_by     text,
  claimed_until  timestamptz,
  parsed_at      timestamptz,
  search_tsv     tsvector generated always as
                 (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, ''))) stored,
  embedding      extensions.vector,
  unique (entity_id, key)
);
create index nodes_entity_seq_idx on public.nodes (entity_id, seq);
create index nodes_parent_idx on public.nodes (parent_id);
create index nodes_path_idx on public.nodes using gist (path);
create index nodes_search_idx on public.nodes using gin (search_tsv);
create index nodes_ingest_idx on public.nodes (entity_id, ingest_status);

create table public.topics (
  id           uuid primary key default gen_random_uuid(),
  name         text not null unique,
  description  text,
  embedding    extensions.vector,
  created_at   timestamptz not null default now()
);

create table public.concepts (
  id                uuid primary key default gen_random_uuid(),
  entity_id         uuid not null references public.entities (id) on delete cascade,
  node_id           uuid not null references public.nodes (id) on delete cascade,
  key               text not null,         -- '<node key>/<slug>', stable across re-ingest
  name              text not null,
  summary           text,                  -- one line, for listings and search
  teaching_content  jsonb not null default '{}'::jsonb,
  modality          text not null default 'read' check (modality in ('read', 'code', 'exercise')),
  topic_id          uuid references public.topics (id) on delete set null,
  order_index       int not null default 0,
  archived_at       timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  search_tsv        tsvector generated always as
                    (to_tsvector('english', coalesce(name, '') || ' ' || coalesce(summary, ''))) stored,
  unique (entity_id, key)
);
create index concepts_node_idx on public.concepts (node_id, order_index);
create index concepts_topic_idx on public.concepts (topic_id);
create index concepts_search_idx on public.concepts using gin (search_tsv);

create table public.concept_edges (
  from_concept_id  uuid not null references public.concepts (id) on delete cascade,  -- needs the prerequisite
  to_concept_id    uuid not null references public.concepts (id) on delete cascade,  -- the prerequisite
  confidence       real not null default 0.8 check (confidence >= 0 and confidence <= 1),
  primary key (from_concept_id, to_concept_id),
  check (from_concept_id <> to_concept_id)
);
create index concept_edges_to_idx on public.concept_edges (to_concept_id);

-- Staging for prerequisites referenced by key before the target concept exists.
create table public.ingest_edge_refs (
  entity_id   uuid not null references public.entities (id) on delete cascade,
  from_key    text not null,
  to_key      text not null,
  confidence  real not null default 0.8,
  primary key (entity_id, from_key, to_key)
);

create table public.objectives (
  id           uuid primary key default gen_random_uuid(),
  concept_id   uuid not null references public.concepts (id) on delete cascade,
  key          text not null,
  description  text not null,
  order_index  int not null default 0,
  archived_at  timestamptz,
  unique (concept_id, key)
);

create table public.assets (
  id            uuid primary key default gen_random_uuid(),
  entity_id     uuid not null references public.entities (id) on delete cascade,
  concept_id    uuid references public.concepts (id) on delete cascade,
  node_id       uuid references public.nodes (id) on delete cascade,
  kind          text not null check (kind in ('source', 'diagram', 'animation', 'image', 'starter_code', 'submission')),
  description   text,
  search_tsv    tsvector generated always as (to_tsvector('english', coalesce(description, ''))) stored,
  bucket        text not null,
  storage_path  text not null,
  mime_type     text,
  size_bytes    bigint,
  sha256        text,
  created_at    timestamptz not null default now(),
  check (num_nonnulls(concept_id, node_id) <= 1)
);
create index assets_entity_idx on public.assets (entity_id);
create index assets_concept_idx on public.assets (concept_id);
create index assets_node_idx on public.assets (node_id);
create index assets_search_idx on public.assets using gin (search_tsv);

create table public.exercises (
  id                uuid primary key default gen_random_uuid(),
  concept_id        uuid not null references public.concepts (id) on delete cascade,
  objective_ids     uuid[] not null default '{}',
  prompt            text not null,
  starter_asset_id  uuid references public.assets (id) on delete set null,
  check_spec        jsonb not null default '{}'::jsonb,
  runtime           jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now()
);
create index exercises_concept_idx on public.exercises (concept_id);

-- ---------------------------------------------------------------------------
-- Private learner layer
-- ---------------------------------------------------------------------------
create table public.enrollments (
  id                 uuid primary key default gen_random_uuid(),
  learner_id         uuid not null references public.learners (id) on delete cascade,
  entity_id          uuid not null references public.entities (id) on delete cascade,
  status             text not null default 'active'
                     check (status in ('active', 'paused', 'completed', 'dropped')),
  current_node_id    uuid references public.nodes (id) on delete set null,
  knowledge_summary  text,
  target_date        date,
  daily_minutes      int check (daily_minutes is null or daily_minutes > 0),
  reset_at           timestamptz,
  started_at         timestamptz not null default now(),
  completed_at       timestamptz,
  last_seen_at       timestamptz not null default now(),
  unique (learner_id, entity_id)
);
create index enrollments_recent_idx on public.enrollments (learner_id, last_seen_at desc);

create table public.study_sessions (
  id                uuid primary key default gen_random_uuid(),
  learner_id        uuid not null references public.learners (id) on delete cascade,
  enrollment_id     uuid not null references public.enrollments (id) on delete cascade,
  client            text not null default 'cli' check (client in ('cli', 'plugin', 'connector', 'other')),
  started_at        timestamptz not null default now(),
  last_activity_at  timestamptz not null default now(),
  ended_at          timestamptz,
  active_seconds    int not null default 0,
  concepts_touched  int not null default 0
);
create index study_sessions_enrollment_idx on public.study_sessions (enrollment_id, last_activity_at desc);
create index study_sessions_learner_idx on public.study_sessions (learner_id, started_at desc);

create table public.assessments (
  id                   uuid primary key default gen_random_uuid(),
  learner_id           uuid not null references public.learners (id) on delete cascade,
  concept_id           uuid not null references public.concepts (id) on delete cascade,
  objective_id         uuid references public.objectives (id) on delete set null,  -- null = whole concept
  kind                 text not null check (kind in ('question', 'exercise', 'placement', 'self_report', 'dispute')),
  question             text,
  answer               text,
  submission_asset_id  uuid references public.assets (id) on delete set null,
  verdict              text not null check (verdict in ('correct', 'partial', 'wrong', 'claimed_known')),
  weight               real not null default 1.0 check (weight > 0 and weight <= 2),
  feedback             text,
  misconception        text,
  disputes_id          uuid references public.assessments (id) on delete cascade,
  study_session_id     uuid references public.study_sessions (id) on delete set null,
  created_at           timestamptz not null default now(),
  check ((kind = 'dispute') = (disputes_id is not null))
);
create index assessments_learner_concept_idx on public.assessments (learner_id, concept_id, created_at);
create index assessments_objective_idx on public.assessments (objective_id);
create index assessments_disputes_idx on public.assessments (disputes_id);

-- The log is append-only: rows are never edited. (Deletes are allowed only so
-- that deleting a learner or entity can cascade.)
create function public.koro_assessments_immutable() returns trigger
language plpgsql set search_path = public as $$
begin
  raise exception 'assessments are append-only; add a dispute row instead of editing';
end $$;
create trigger assessments_no_update before update on public.assessments
  for each row execute function public.koro_assessments_immutable();

create table public.objective_progress (
  learner_id        uuid not null references public.learners (id) on delete cascade,
  objective_id      uuid not null references public.objectives (id) on delete cascade,
  status            text not null default 'unseen' check (status in ('unseen', 'gap', 'strength')),
  mastery           real not null default 0,
  misconception     text,
  evidence_count    int not null default 0,
  last_evidence_at  timestamptz,
  resolved_at       timestamptz,
  primary key (learner_id, objective_id)
);
create index objective_progress_objective_idx on public.objective_progress (objective_id);

create table public.progress (
  learner_id      uuid not null references public.learners (id) on delete cascade,
  concept_id      uuid not null references public.concepts (id) on delete cascade,
  status          text not null default 'untouched'
                  check (status in ('untouched', 'seen', 'learning', 'mastered')),
  seen_at         timestamptz,
  last_seen_at    timestamptz,
  mastery         real not null default 0,
  stability_days  real not null default 1,
  notes           text,
  primary key (learner_id, concept_id)
);
create index progress_concept_idx on public.progress (concept_id);
create index progress_recent_idx on public.progress (learner_id, last_seen_at desc);

-- ---------------------------------------------------------------------------
-- Lockdown: RLS on everywhere with no policies for anon/authenticated, so only
-- the secret (service role) key used by the koro CLI can read or write.
-- Per-user policies come with multi-user support.
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['learners', 'entities', 'entity_links', 'nodes', 'topics', 'concepts',
                           'concept_edges', 'ingest_edge_refs', 'objectives', 'assets', 'exercises',
                           'enrollments', 'study_sessions', 'assessments', 'objective_progress', 'progress']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
  end loop;
end $$;

revoke execute on function public.koro_assessments_immutable() from public, anon, authenticated;
