-- Koro learning functions: the evidence log, calculated progress, the teaching
-- loop, and habits. None of these functions remove rows.

-- ---------------------------------------------------------------------------
-- Learners
-- ---------------------------------------------------------------------------
create or replace function public.koro_ensure_learner(p_display_name text) returns jsonb
language plpgsql set search_path = public as $$
declare v public.learners;
begin
  select * into v from public.learners where display_name = p_display_name order by created_at limit 1;
  if not found then
    insert into public.learners (display_name) values (p_display_name) returning * into v;
  end if;
  return jsonb_build_object('learner_id', v.id, 'display_name', v.display_name, 'created_at', v.created_at);
end $$;

create or replace function public.koro_learners() returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('learner_id', id, 'display_name', display_name) order by created_at), '[]')
    from public.learners
$$;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------
create or replace function public.koro_resolve_concept(p_ref text, p_entity uuid default null) returns uuid
language plpgsql stable set search_path = public as $$
declare v uuid;
begin
  if p_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select id into v from public.concepts where id = p_ref::uuid;
  else
    select id into v from public.concepts
     where key = p_ref and (p_entity is null or entity_id = p_entity) and archived_at is null
     order by created_at limit 1;
  end if;
  if v is null then raise exception 'unknown concept %', p_ref; end if;
  return v;
end $$;

create or replace function public.koro_resolve_entity(p_ref text) returns uuid
language plpgsql stable set search_path = public as $$
declare v uuid;
begin
  if p_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select id into v from public.entities where id = p_ref::uuid;
  else
    select id into v from public.entities
     where title ilike p_ref or canonical_id = p_ref or source_ref = p_ref
     order by created_at limit 1;
    if v is null then
      select id into v from public.entities where title ilike '%' || p_ref || '%' order by created_at limit 1;
    end if;
  end if;
  if v is null then raise exception 'unknown entity %', p_ref; end if;
  return v;
end $$;

create or replace function public.koro_score(p_verdict text) returns real
language sql immutable set search_path = public as $$
  select case p_verdict when 'correct' then 1.0 when 'partial' then 0.5
                        when 'claimed_known' then 0.75 else 0.0 end::real
$$;

-- Get or create the enrollment for a learner and entity.
create or replace function public.koro_enroll(p_learner uuid, p_entity uuid) returns public.enrollments
language plpgsql set search_path = public as $$
declare v public.enrollments;
begin
  insert into public.enrollments (learner_id, entity_id) values (p_learner, p_entity)
  on conflict (learner_id, entity_id) do nothing;
  select * into v from public.enrollments where learner_id = p_learner and entity_id = p_entity;
  return v;
end $$;

-- Open or continue a sitting. A sitting closes itself after 20 idle minutes.
create or replace function public.koro_sitting(p_learner uuid, p_enrollment uuid, p_client text default 'cli',
                                               p_concept_touched boolean default false)
returns uuid
language plpgsql set search_path = public as $$
declare
  v public.study_sessions;
  v_gap interval;
begin
  -- Close idle sittings anywhere for this learner.
  update public.study_sessions set ended_at = last_activity_at
   where learner_id = p_learner and ended_at is null and last_activity_at < now() - interval '20 minutes';

  select * into v from public.study_sessions
   where enrollment_id = p_enrollment and ended_at is null
   order by last_activity_at desc limit 1;

  if not found then
    -- Switching books closes the other open sitting.
    update public.study_sessions set ended_at = now()
     where learner_id = p_learner and ended_at is null and enrollment_id <> p_enrollment;
    insert into public.study_sessions (learner_id, enrollment_id, client, concepts_touched)
    values (p_learner, p_enrollment, coalesce(p_client, 'cli'), case when p_concept_touched then 1 else 0 end)
    returning * into v;
    return v.id;
  end if;

  v_gap := now() - v.last_activity_at;
  update public.study_sessions set
    last_activity_at = now(),
    active_seconds = active_seconds + least(300, greatest(0, extract(epoch from v_gap)))::int,
    concepts_touched = concepts_touched + case when p_concept_touched then 1 else 0 end
   where id = v.id;
  return v.id;
end $$;

-- ---------------------------------------------------------------------------
-- Progress, calculated from the evidence log
-- ---------------------------------------------------------------------------
create or replace function public.koro_recompute_progress(p_learner uuid, p_concept uuid) returns jsonb
language plpgsql set search_path = public as $$
declare
  v_concept public.concepts;
  v_reset timestamptz;
  o record;
  ev record;
  v_m real;
  v_n int;
  v_had_gap boolean;
  v_resolved timestamptz;
  v_mis text;
  v_last_verdict text;
  v_last_at timestamptz;
  v_status text;
  v_obj_total int := 0;
  v_obj_strength int := 0;
  v_mastery_sum real := 0;
  v_concept_mastery real;
  v_stability real := 1;
  v_evidence int := 0;
  v_seen timestamptz;
  v_progress_status text;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_learner::text || ':' || p_concept::text, 0));
  select * into v_concept from public.concepts where id = p_concept;
  if not found then raise exception 'unknown concept %', p_concept; end if;
  select reset_at into v_reset from public.enrollments where learner_id = p_learner and entity_id = v_concept.entity_id;

  -- Per objective: mastery is an exponential moving average of evidence scores.
  -- Whole-concept evidence (objective_id null) counts for every objective.
  for o in select id from public.objectives where concept_id = p_concept and archived_at is null order by order_index loop
    v_obj_total := v_obj_total + 1;
    v_m := 0; v_n := 0; v_had_gap := false; v_resolved := null; v_mis := null;
    v_last_verdict := null; v_last_at := null;
    for ev in
      select a.verdict, a.weight, a.misconception, a.created_at
        from public.assessments a
       where a.learner_id = p_learner and a.concept_id = p_concept
         and (a.objective_id = o.id or a.objective_id is null)
         and (v_reset is null or a.created_at > v_reset)
         and not exists (select 1 from public.assessments d where d.disputes_id = a.id)
       order by a.created_at
    loop
      if ev.verdict = 'claimed_known' then
        v_m := greatest(v_m, 0.75);
      elsif v_n = 0 then
        v_m := koro_score(ev.verdict);
      else
        v_m := v_m + least(1, 0.6 * ev.weight) * (koro_score(ev.verdict) - v_m);
      end if;
      v_n := v_n + 1;
      if ev.verdict in ('wrong', 'partial') then
        v_had_gap := true;
        v_resolved := null;
        v_mis := coalesce(ev.misconception, v_mis);
      elsif v_m >= 0.7 then
        if v_had_gap and v_resolved is null then v_resolved := ev.created_at; end if;
        v_mis := null;
      end if;
      v_last_verdict := ev.verdict;
      v_last_at := ev.created_at;
    end loop;

    v_status := case when v_n = 0 then 'unseen'
                     when v_m >= 0.7 and v_last_verdict in ('correct', 'claimed_known') then 'strength'
                     else 'gap' end;
    if v_status = 'strength' then v_obj_strength := v_obj_strength + 1; end if;
    v_mastery_sum := v_mastery_sum + v_m;

    insert into public.objective_progress (learner_id, objective_id, status, mastery, misconception,
                                           evidence_count, last_evidence_at, resolved_at)
    values (p_learner, o.id, v_status, v_m, v_mis, v_n, v_last_at, case when v_status = 'strength' then v_resolved end)
    on conflict (learner_id, objective_id) do update set
      status = excluded.status, mastery = excluded.mastery, misconception = excluded.misconception,
      evidence_count = excluded.evidence_count, last_evidence_at = excluded.last_evidence_at,
      resolved_at = excluded.resolved_at;
  end loop;

  -- Stability (days a concept holds before fading) grows with each correct recall.
  for ev in
    select a.verdict from public.assessments a
     where a.learner_id = p_learner and a.concept_id = p_concept
       and (v_reset is null or a.created_at > v_reset)
       and not exists (select 1 from public.assessments d where d.disputes_id = a.id)
     order by a.created_at
  loop
    v_evidence := v_evidence + 1;
    v_stability := case ev.verdict
      when 'correct' then least(180, v_stability * 2.5)
      when 'partial' then least(180, v_stability * 1.2)
      when 'claimed_known' then greatest(v_stability, 14)
      else greatest(0.5, v_stability * 0.4) end;
  end loop;

  if v_obj_total > 0 then
    v_concept_mastery := v_mastery_sum / v_obj_total;
  else
    select coalesce(avg(koro_score(a.verdict)), 0) into v_concept_mastery
      from public.assessments a
     where a.learner_id = p_learner and a.concept_id = p_concept
       and (v_reset is null or a.created_at > v_reset)
       and not exists (select 1 from public.assessments d where d.disputes_id = a.id);
  end if;

  select seen_at into v_seen from public.progress where learner_id = p_learner and concept_id = p_concept;
  if v_reset is not null and v_seen is not null and v_seen <= v_reset then v_seen := null; end if;

  v_progress_status := case
    when v_evidence = 0 and v_seen is null then 'untouched'
    when v_evidence = 0 then 'seen'
    when (v_obj_total > 0 and v_obj_strength = v_obj_total)
      or (v_obj_total = 0 and v_concept_mastery >= 0.7) then 'mastered'
    else 'learning' end;

  insert into public.progress (learner_id, concept_id, status, seen_at, mastery, stability_days)
  values (p_learner, p_concept, v_progress_status, v_seen, coalesce(v_concept_mastery, 0), v_stability)
  on conflict (learner_id, concept_id) do update set
    status = excluded.status, seen_at = excluded.seen_at, mastery = excluded.mastery,
    stability_days = excluded.stability_days;

  return jsonb_build_object(
    'concept_key', v_concept.key, 'status', v_progress_status,
    'mastery', round(coalesce(v_concept_mastery, 0)::numeric, 2), 'stability_days', round(v_stability::numeric, 1),
    'objectives', (select coalesce(jsonb_agg(jsonb_build_object(
                      'key', ob.key, 'description', ob.description, 'status', op.status,
                      'mastery', round(op.mastery::numeric, 2), 'misconception', op.misconception)
                    order by ob.order_index), '[]')
                     from public.objectives ob
                     join public.objective_progress op on op.objective_id = ob.id and op.learner_id = p_learner
                    where ob.concept_id = p_concept and ob.archived_at is null));
end $$;

-- Record that the learner was shown a concept (exposure), and keep the
-- enrollment and the current sitting fresh.
create or replace function public.koro_touch(p_learner uuid, p_concept uuid, p_client text default 'cli',
                                             p_mark_seen boolean default true)
returns jsonb
language plpgsql set search_path = public as $$
declare
  v_entity uuid;
  v_enr public.enrollments;
  v_sitting uuid;
  v_new boolean;
begin
  select entity_id into v_entity from public.concepts where id = p_concept;
  if v_entity is null then raise exception 'unknown concept %', p_concept; end if;
  v_enr := koro_enroll(p_learner, v_entity);
  update public.enrollments set last_seen_at = now(),
         status = case when status in ('paused', 'dropped') then 'active' else status end
   where id = v_enr.id;

  select not exists (select 1 from public.progress where learner_id = p_learner and concept_id = p_concept
                       and last_seen_at > now() - interval '20 minutes') into v_new;
  v_sitting := koro_sitting(p_learner, v_enr.id, p_client, v_new);

  insert into public.progress (learner_id, concept_id, status, seen_at, last_seen_at)
  values (p_learner, p_concept, case when p_mark_seen then 'seen' else 'untouched' end,
          case when p_mark_seen then clock_timestamp() end, clock_timestamp())
  on conflict (learner_id, concept_id) do update set
    last_seen_at = clock_timestamp(),
    seen_at = case when p_mark_seen and (public.progress.seen_at is null
                                         or (v_enr.reset_at is not null and public.progress.seen_at <= v_enr.reset_at))
                   then clock_timestamp() else public.progress.seen_at end,
    status = case when p_mark_seen and public.progress.status = 'untouched' then 'seen' else public.progress.status end;

  return jsonb_build_object('enrollment_id', v_enr.id, 'study_session_id', v_sitting);
end $$;

-- ---------------------------------------------------------------------------
-- Choosing what to teach
-- ---------------------------------------------------------------------------
create or replace function public.koro_concept_card(p_learner uuid, p_concept uuid) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'id', c.id, 'key', c.key, 'name', c.name, 'summary', c.summary, 'modality', c.modality,
    'node_key', n.key, 'node_title', n.title, 'page_start', n.page_start, 'page_end', n.page_end,
    'status', coalesce(p.status, 'untouched'), 'mastery', round(coalesce(p.mastery, 0)::numeric, 2))
  from public.concepts c
  join public.nodes n on n.id = c.node_id
  left join public.progress p on p.concept_id = c.id and p.learner_id = p_learner
  where c.id = p_concept
$$;

-- A prerequisite is satisfied when it is mastered, or learning with mastery >= 0.5.
create or replace function public.koro_prereq_ok(p_learner uuid, p_concept uuid) returns boolean
language sql stable set search_path = public as $$
  select not exists (
    select 1 from public.concept_edges ce
      join public.concepts t on t.id = ce.to_concept_id and t.archived_at is null
      left join public.progress p on p.concept_id = t.id and p.learner_id = p_learner
     where ce.from_concept_id = p_concept and ce.active
       and not (coalesce(p.status, 'untouched') = 'mastered'
                or (coalesce(p.status, 'untouched') = 'learning' and coalesce(p.mastery, 0) >= 0.5)))
$$;

-- Every live concept in scope with the learner's state, in reading order.
create or replace function public.koro_scope_rows(p_learner uuid, p_entity uuid, p_scope extensions.ltree)
returns table (concept_id uuid, seq int, ord int, status text, mastery real,
               last_seen_at timestamptz, stability real)
language sql stable set search_path = public, extensions as $$
  select c.id, n.seq, c.order_index, coalesce(p.status, 'untouched'), coalesce(p.mastery, 0)::real,
         p.last_seen_at, coalesce(p.stability_days, 1)::real
    from public.concepts c
    join public.nodes n on n.id = c.node_id
    left join public.progress p on p.concept_id = c.id and p.learner_id = p_learner
   where c.entity_id = p_entity and c.archived_at is null
     and (p_scope is null or n.path <@ p_scope)
$$;

create or replace function public.koro_next_concept(p_learner uuid, p_entity uuid, p_node_key text default null)
returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare
  v_scope ltree;
  v_scope_key text;
  v_pick uuid;
  v_block uuid;
  v_reason text;
begin
  if p_node_key is not null then
    select path, key into v_scope, v_scope_key from public.nodes where entity_id = p_entity and key = p_node_key;
    if v_scope is null then raise exception 'unknown node %', p_node_key; end if;
  else
    select n.path, n.key into v_scope, v_scope_key
      from public.enrollments e join public.nodes n on n.id = e.current_node_id
     where e.learner_id = p_learner and e.entity_id = p_entity;
  end if;

  -- 1. Resume: the most recent concept that was shown but not yet checked.
  select s.concept_id into v_pick from koro_scope_rows(p_learner, p_entity, v_scope) s
   where s.status = 'seen' order by s.last_seen_at desc nulls last limit 1;
  if v_pick is not null then v_reason := 'resume'; end if;

  -- 2. Review: gaps left from an earlier day, or mastered concepts that are fading.
  if v_pick is null then
    select s.concept_id, case when s.status = 'learning' then 'gap' else 'fading' end into v_pick, v_reason
      from koro_scope_rows(p_learner, p_entity, v_scope) s
     where (s.status = 'learning' and s.last_seen_at < now() - interval '12 hours')
        or (s.status = 'mastered' and s.last_seen_at < now() - make_interval(secs => s.stability * 86400))
     order by (s.status = 'learning') desc, s.seq, s.ord limit 1;
  end if;

  -- 3. Next: the earliest untouched concept whose prerequisites are satisfied.
  if v_pick is null then
    select s.concept_id into v_pick from koro_scope_rows(p_learner, p_entity, v_scope) s
     where s.status = 'untouched' and koro_prereq_ok(p_learner, s.concept_id)
     order by s.seq, s.ord limit 1;
    if v_pick is not null then v_reason := 'next'; end if;
  end if;

  -- 4. Blocked: surface the first unmet prerequisite of the earliest untouched concept.
  if v_pick is null then
    select ce.to_concept_id into v_block
      from (select s.concept_id from koro_scope_rows(p_learner, p_entity, v_scope) s
             where s.status = 'untouched' order by s.seq, s.ord limit 1) first_untouched
      join public.concept_edges ce on ce.from_concept_id = first_untouched.concept_id and ce.active
      join public.concepts t on t.id = ce.to_concept_id and t.archived_at is null
      join public.nodes tn on tn.id = t.node_id
      left join public.progress p on p.concept_id = t.id and p.learner_id = p_learner
     where not (coalesce(p.status, 'untouched') = 'mastered'
                or (coalesce(p.status, 'untouched') = 'learning' and coalesce(p.mastery, 0) >= 0.5))
     order by tn.seq, t.order_index limit 1;
    if v_block is not null then v_pick := v_block; v_reason := 'prerequisite'; end if;
  end if;

  -- 5. Fallback: anything not yet mastered, in reading order. It never stalls.
  if v_pick is null then
    select s.concept_id into v_pick from koro_scope_rows(p_learner, p_entity, v_scope) s
     where s.status <> 'mastered' order by s.seq, s.ord limit 1;
    if v_pick is not null then v_reason := 'fallback'; end if;
  end if;

  if v_pick is null then
    return jsonb_build_object('reason', 'complete', 'scope', coalesce(v_scope_key, 'all'), 'concept', null);
  end if;
  return jsonb_build_object('reason', v_reason, 'scope', coalesce(v_scope_key, 'all'),
                            'concept', koro_concept_card(p_learner, v_pick));
end $$;

-- ---------------------------------------------------------------------------
-- The teaching loop
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
      'session', v_touch)
    from public.concepts c
    join public.entities e on e.id = c.entity_id
    join public.nodes n on n.id = c.node_id
    left join public.progress p on p.concept_id = c.id and p.learner_id = p_learner
    where c.id = v_cid);
end $$;

-- Append evidence (the agent has already judged the answer) and recompute.
create or replace function public.koro_assess(
  p_learner uuid,
  p_concept_ref text,
  p_verdict text,
  p_objective_key text default null,
  p_question text default null,
  p_answer text default null,
  p_feedback text default null,
  p_misconception text default null,
  p_kind text default 'question',
  p_weight real default 1.0,
  p_entity uuid default null,
  p_client text default 'cli'
) returns jsonb
language plpgsql set search_path = public as $$
declare
  v_cid uuid;
  v_oid uuid;
  v_touch jsonb;
  v_aid uuid;
begin
  if p_kind not in ('question', 'exercise', 'placement', 'self_report') then
    raise exception 'kind must be question, exercise, placement or self_report (use koro_dispute for disputes)';
  end if;
  v_cid := koro_resolve_concept(p_concept_ref, p_entity);
  if p_objective_key is not null then
    select id into v_oid from public.objectives
     where concept_id = v_cid and key = p_objective_key and archived_at is null;
    if v_oid is null then raise exception 'concept % has no objective %', p_concept_ref, p_objective_key; end if;
  end if;
  v_touch := koro_touch(p_learner, v_cid, p_client, true);
  insert into public.assessments (learner_id, concept_id, objective_id, kind, question, answer, verdict,
                                  weight, feedback, misconception, study_session_id)
  values (p_learner, v_cid, v_oid, p_kind, p_question, p_answer, p_verdict, p_weight, p_feedback,
          p_misconception, (v_touch->>'study_session_id')::uuid)
  returning id into v_aid;
  return jsonb_build_object('assessment_id', v_aid) || koro_recompute_progress(p_learner, v_cid);
end $$;

-- Correct a verdict: a new dispute row replaces the original when calculating.
create or replace function public.koro_dispute(p_learner uuid, p_assessment uuid, p_verdict text, p_reason text)
returns jsonb
language plpgsql set search_path = public as $$
declare
  a public.assessments;
  v_id uuid;
begin
  select * into a from public.assessments where id = p_assessment and learner_id = p_learner;
  if not found then raise exception 'unknown assessment %', p_assessment; end if;
  insert into public.assessments (learner_id, concept_id, objective_id, kind, question, answer, verdict,
                                  weight, feedback, disputes_id)
  values (p_learner, a.concept_id, a.objective_id, 'dispute', a.question, a.answer, p_verdict,
          a.weight, p_reason, a.id)
  returning id into v_id;
  return jsonb_build_object('dispute_id', v_id) || koro_recompute_progress(p_learner, a.concept_id);
end $$;

-- "I already know this" for a concept or a whole section (self-report evidence).
create or replace function public.koro_mark_known(p_learner uuid, p_entity uuid, p_ref text, p_note text default null,
                                                  p_client text default 'cli')
returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_path ltree;
  r record;
  v_n int := 0;
begin
  select path into v_path from public.nodes where entity_id = p_entity and key = p_ref;
  for r in
    select c.id from public.concepts c join public.nodes n on n.id = c.node_id
     where c.entity_id = p_entity and c.archived_at is null
       and ((v_path is not null and n.path <@ v_path) or (v_path is null and c.key = p_ref))
     order by n.seq, c.order_index
  loop
    perform koro_touch(p_learner, r.id, p_client, true);
    insert into public.assessments (learner_id, concept_id, kind, verdict, weight, feedback)
    values (p_learner, r.id, 'self_report', 'claimed_known', 0.6, coalesce(p_note, 'Learner says they already know this'));
    perform koro_recompute_progress(p_learner, r.id);
    v_n := v_n + 1;
  end loop;
  if v_n = 0 then raise exception 'nothing matches % (give a node key or a concept key)', p_ref; end if;
  return jsonb_build_object('marked_known', v_n, 'ref', p_ref);
end $$;

-- ---------------------------------------------------------------------------
-- Sessions, habits, and goals
-- ---------------------------------------------------------------------------
create or replace function public.koro_start(p_learner uuid, p_entity uuid, p_node_key text default null,
                                             p_client text default 'cli')
returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_enr public.enrollments;
  v_node uuid;
  v_sitting uuid;
begin
  v_enr := koro_enroll(p_learner, p_entity);
  if p_node_key is not null then
    select id into v_node from public.nodes where entity_id = p_entity and key = p_node_key;
    if v_node is null then raise exception 'unknown node %', p_node_key; end if;
  end if;
  update public.enrollments set
    current_node_id = coalesce(v_node, current_node_id),
    status = case when status in ('paused', 'dropped') then 'active' else status end,
    last_seen_at = now()
   where id = v_enr.id;
  v_sitting := koro_sitting(p_learner, v_enr.id, p_client, false);
  return jsonb_build_object(
    'study_session_id', v_sitting,
    'status', koro_status(p_learner, p_entity, null),
    'next', koro_next_concept(p_learner, p_entity, null));
end $$;

-- Pick up whatever the learner was last reading.
create or replace function public.koro_continue(p_learner uuid, p_client text default 'cli') returns jsonb
language plpgsql set search_path = public as $$
declare v_entity uuid;
begin
  select entity_id into v_entity from public.enrollments
   where learner_id = p_learner and status = 'active'
   order by last_seen_at desc limit 1;
  if v_entity is null then
    return jsonb_build_object('none', true, 'library', koro_library(p_learner));
  end if;
  return koro_start(p_learner, v_entity, null, p_client);
end $$;

create or replace function public.koro_set_status(p_learner uuid, p_entity uuid, p_status text) returns jsonb
language plpgsql set search_path = public as $$
declare v_enr public.enrollments;
begin
  if p_status not in ('active', 'paused', 'completed', 'dropped') then
    raise exception 'status must be active, paused, completed or dropped';
  end if;
  v_enr := koro_enroll(p_learner, p_entity);
  update public.enrollments set status = p_status,
         completed_at = case when p_status = 'completed' then now() else null end
   where id = v_enr.id;
  update public.study_sessions set ended_at = now()
   where enrollment_id = v_enr.id and ended_at is null and p_status <> 'active';
  return jsonb_build_object('entity_id', p_entity, 'status', p_status);
end $$;

create or replace function public.koro_set_goal(p_learner uuid, p_entity uuid, p_target_date date default null,
                                                p_daily_minutes int default null)
returns jsonb
language plpgsql set search_path = public as $$
declare v_enr public.enrollments;
begin
  v_enr := koro_enroll(p_learner, p_entity);
  update public.enrollments set target_date = coalesce(p_target_date, target_date),
                                daily_minutes = coalesce(p_daily_minutes, daily_minutes)
   where id = v_enr.id
  returning * into v_enr;
  return jsonb_build_object('entity_id', p_entity, 'target_date', v_enr.target_date, 'daily_minutes', v_enr.daily_minutes);
end $$;

create or replace function public.koro_set_summary(p_learner uuid, p_entity uuid, p_summary text) returns jsonb
language plpgsql set search_path = public as $$
declare v_enr public.enrollments;
begin
  v_enr := koro_enroll(p_learner, p_entity);
  update public.enrollments set knowledge_summary = p_summary where id = v_enr.id;
  return jsonb_build_object('entity_id', p_entity, 'knowledge_summary', p_summary);
end $$;

-- Start a book over. History stays in the log; evidence before reset_at is ignored.
create or replace function public.koro_restart(p_learner uuid, p_entity uuid) returns jsonb
language plpgsql set search_path = public as $$
declare
  v_enr public.enrollments;
  r record;
  v_n int := 0;
begin
  v_enr := koro_enroll(p_learner, p_entity);
  update public.enrollments set reset_at = clock_timestamp(), status = 'active', completed_at = null,
         knowledge_summary = null, current_node_id = null
   where id = v_enr.id;
  for r in select p.concept_id from public.progress p join public.concepts c on c.id = p.concept_id
            where p.learner_id = p_learner and c.entity_id = p_entity loop
    perform koro_recompute_progress(p_learner, r.concept_id);
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('entity_id', p_entity, 'reset_at', clock_timestamp(), 'concepts_reset', v_n);
end $$;

-- ---------------------------------------------------------------------------
-- Views of progress
-- ---------------------------------------------------------------------------
create or replace function public.koro_rollup(p_learner uuid, p_entity uuid, p_path extensions.ltree default null) returns jsonb
language sql stable set search_path = public, extensions as $$
  with cs as (
    select c.id, coalesce(p.status, 'untouched') as status, coalesce(p.mastery, 0) as mastery
      from public.concepts c join public.nodes n on n.id = c.node_id
      left join public.progress p on p.concept_id = c.id and p.learner_id = p_learner
     where c.entity_id = p_entity and c.archived_at is null and (p_path is null or n.path <@ p_path))
  select jsonb_build_object(
    'concepts', count(*),
    'untouched', count(*) filter (where status = 'untouched'),
    'seen', count(*) filter (where status = 'seen'),
    'learning', count(*) filter (where status = 'learning'),
    'mastered', count(*) filter (where status = 'mastered'),
    'mastery', round(coalesce(avg(mastery), 0)::numeric, 2),
    'status', case when count(*) = 0 then 'none'
                   when count(*) filter (where status = 'mastered') = count(*) then 'mastered'
                   when count(*) filter (where status = 'untouched') = count(*) then 'untouched'
                   when count(*) filter (where status in ('learning', 'mastered')) > 0 then 'learning'
                   else 'seen' end)
  from cs
$$;

create or replace function public.koro_toc(p_learner uuid, p_entity uuid, p_max_depth int default null) returns jsonb
language sql stable set search_path = public, extensions as $$
  select jsonb_build_object(
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title, 'author', e.author, 'status', e.status,
                                         'source_ref', e.source_ref, 'page_count', e.page_count)
                 from public.entities e where e.id = p_entity),
    'current_node', (select n.key from public.enrollments en join public.nodes n on n.id = en.current_node_id
                      where en.learner_id = p_learner and en.entity_id = p_entity),
    'nodes', coalesce(jsonb_agg(jsonb_build_object(
        'key', n.key, 'title', n.title, 'kind', n.kind, 'depth', n.depth, 'summary', n.summary,
        'page_start', n.page_start, 'page_end', n.page_end, 'teachable', n.teachable,
        'ingest_status', n.ingest_status,
        'progress', case when n.teachable then koro_rollup(p_learner, p_entity, n.path) end)
      order by n.seq), '[]'))
  from public.nodes n
  where n.entity_id = p_entity and (p_max_depth is null or n.depth <= p_max_depth)
$$;

create or replace function public.koro_status(p_learner uuid, p_entity uuid, p_node_key text default null)
returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare
  v_path ltree;
begin
  if p_node_key is not null then
    select path into v_path from public.nodes where entity_id = p_entity and key = p_node_key;
    if v_path is null then raise exception 'unknown node %', p_node_key; end if;
  end if;
  return jsonb_build_object(
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title, 'status', e.status) from public.entities e where e.id = p_entity),
    'scope', coalesce(p_node_key, 'all'),
    'enrollment', (select jsonb_build_object('status', en.status, 'current_node', cn.key,
                                             'current_node_title', cn.title,
                                             'knowledge_summary', en.knowledge_summary,
                                             'target_date', en.target_date, 'daily_minutes', en.daily_minutes,
                                             'started_at', en.started_at, 'last_seen_at', en.last_seen_at,
                                             'reset_at', en.reset_at)
                     from public.enrollments en left join public.nodes cn on cn.id = en.current_node_id
                    where en.learner_id = p_learner and en.entity_id = p_entity),
    'rollup', koro_rollup(p_learner, p_entity, v_path),
    'gaps', (select coalesce(jsonb_agg(g order by g->>'node_key'), '[]') from (
               select jsonb_build_object('concept_key', c.key, 'concept', c.name, 'node_key', n.key,
                                         'objective', ob.description, 'misconception', op.misconception,
                                         'mastery', round(op.mastery::numeric, 2)) as g
                 from public.objective_progress op
                 join public.objectives ob on ob.id = op.objective_id and ob.archived_at is null
                 join public.concepts c on c.id = ob.concept_id and c.archived_at is null
                 join public.nodes n on n.id = c.node_id
                where op.learner_id = p_learner and op.status = 'gap' and c.entity_id = p_entity
                  and (v_path is null or n.path <@ v_path)
                order by n.seq, c.order_index limit 25) gaps),
    'strengths', (select coalesce(jsonb_agg(s), '[]') from (
               select jsonb_build_object('concept_key', c.key, 'concept', c.name, 'mastery', round(p.mastery::numeric, 2)) as s
                 from public.progress p
                 join public.concepts c on c.id = p.concept_id and c.archived_at is null
                 join public.nodes n on n.id = c.node_id
                where p.learner_id = p_learner and p.status = 'mastered' and c.entity_id = p_entity
                  and (v_path is null or n.path <@ v_path)
                order by p.last_seen_at desc limit 25) strengths),
    'fading', (select coalesce(jsonb_agg(f), '[]') from (
               select jsonb_build_object('concept_key', c.key, 'concept', c.name,
                                         'days_since', round((extract(epoch from now() - p.last_seen_at) / 86400)::numeric, 1),
                                         'stability_days', round(p.stability_days::numeric, 1)) as f
                 from public.progress p
                 join public.concepts c on c.id = p.concept_id and c.archived_at is null
                 join public.nodes n on n.id = c.node_id
                where p.learner_id = p_learner and p.status = 'mastered' and c.entity_id = p_entity
                  and (v_path is null or n.path <@ v_path)
                  and p.last_seen_at < now() - make_interval(secs => p.stability_days * 86400)
                order by p.last_seen_at limit 25) fading),
    'this_week', koro_study_time(p_learner, now() - interval '7 days', p_entity));
end $$;

create or replace function public.koro_study_time(p_learner uuid, p_since timestamptz default now() - interval '7 days',
                                                  p_entity uuid default null)
returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'entity_id', e.id, 'title', e.title,
      'minutes', round(coalesce(t.secs, 0) / 60.0, 1), 'sittings', coalesce(t.sittings, 0),
      'concepts_touched', coalesce(t.touched, 0),
      'daily_minutes_goal', en.daily_minutes,
      'goal_minutes_in_range', case when en.daily_minutes is not null
                                    then en.daily_minutes * greatest(1, ceil(extract(epoch from now() - p_since) / 86400)) end,
      'target_date', en.target_date,
      'concepts_remaining', (koro_rollup(p_learner, e.id, null)->>'concepts')::int
                            - (koro_rollup(p_learner, e.id, null)->>'mastered')::int,
      'days_left', case when en.target_date is not null then en.target_date - current_date end)
    order by coalesce(t.secs, 0) desc), '[]')
  from public.enrollments en
  join public.entities e on e.id = en.entity_id
  left join (select enrollment_id, sum(active_seconds) as secs, count(*) as sittings, sum(concepts_touched) as touched
               from public.study_sessions
              where learner_id = p_learner and started_at >= p_since
              group by enrollment_id) t on t.enrollment_id = en.id
  where en.learner_id = p_learner and (p_entity is null or en.entity_id = p_entity)
$$;

create or replace function public.koro_library(p_learner uuid) returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'entity_id', e.id, 'title', e.title, 'author', e.author, 'source_type', e.source_type,
      'ingest_status', e.status, 'enrollment', en.status, 'last_seen_at', en.last_seen_at,
      'progress', koro_rollup(p_learner, e.id, null))
    order by en.last_seen_at desc nulls last, e.created_at desc), '[]')
  from public.entities e
  left join public.enrollments en on en.entity_id = e.id and en.learner_id = p_learner
$$;

create or replace function public.koro_search(p_learner uuid, p_query text, p_entity uuid default null) returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare
  q tsquery := websearch_to_tsquery('english', p_query);
  v_like text := '%' || p_query || '%';
begin
  return jsonb_build_object(
    'query', p_query,
    'nodes', (select coalesce(jsonb_agg(x), '[]') from (
        select jsonb_build_object('entity_id', n.entity_id, 'key', n.key, 'title', n.title, 'summary', n.summary,
                                  'page_start', n.page_start, 'page_end', n.page_end,
                                  'progress', case when n.teachable then koro_rollup(p_learner, n.entity_id, n.path) end) as x
          from public.nodes n
         where (p_entity is null or n.entity_id = p_entity)
           and (n.search_tsv @@ q or n.title ilike v_like)
         order by ts_rank(n.search_tsv, q) desc, n.seq limit 10) nodes),
    'concepts', (select coalesce(jsonb_agg(x), '[]') from (
        select koro_concept_card(p_learner, c.id) as x
          from public.concepts c
         where c.archived_at is null and (p_entity is null or c.entity_id = p_entity)
           and (c.search_tsv @@ q or c.name ilike v_like)
         order by ts_rank(c.search_tsv, q) desc limit 15) concepts));
end $$;

-- Lock every koro function away from anon/authenticated; only the secret key may call them.
do $$
declare r record;
begin
  for r in select p.oid::regprocedure as sig from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
            where ns.nspname = 'public' and p.proname like 'koro\_%' loop
    execute format('revoke execute on function %s from public, anon, authenticated', r.sig);
  end loop;
end $$;
