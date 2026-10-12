-- Edges and edge references are never deleted: they are deactivated. This keeps
-- re-ingest and cycle-breaking history, and means no koro function deletes data.
alter table public.ingest_edge_refs
  add column active boolean not null default true;

alter table public.concept_edges
  add column active boolean not null default true,
  add column inactive_reason text check (inactive_reason in ('cycle', 'removed', 'archived'));

create index concept_edges_active_from_idx on public.concept_edges (from_concept_id) where active;
