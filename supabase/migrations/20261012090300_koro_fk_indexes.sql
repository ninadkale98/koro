-- Cover foreign keys that cascades and joins use (Supabase performance advisor).
create index if not exists assessments_concept_idx on public.assessments (concept_id);
create index if not exists assessments_session_idx on public.assessments (study_session_id);
create index if not exists assessments_submission_idx on public.assessments (submission_asset_id);
create index if not exists enrollments_current_node_idx on public.enrollments (current_node_id);
create index if not exists enrollments_entity_idx on public.enrollments (entity_id);
create index if not exists entities_created_by_idx on public.entities (created_by);
create index if not exists entity_links_to_idx on public.entity_links (to_entity_id);
create index if not exists exercises_starter_asset_idx on public.exercises (starter_asset_id);
