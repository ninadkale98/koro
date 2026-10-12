-- Figure labels ("2.1") and section keys ("2.1") look alike, so a label lookup is its
-- own function and koro_figures resolves concept → section → label, in that order.

create or replace function public.koro_figure_json(a public.assets) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'asset_id', a.id, 'label', a.label, 'caption', a.caption, 'page', a.page, 'bbox', a.bbox,
    'bucket', a.bucket, 'storage_path', a.storage_path, 'uploaded', a.uploaded_at is not null,
    'width', a.width, 'height', a.height,
    'concept_key', (select c.key from public.concepts c where c.id = a.concept_id),
    'node_key', coalesce((select n.key from public.nodes n where n.id = a.node_id),
                         (select n.key from public.concepts c join public.nodes n on n.id = c.node_id where c.id = a.concept_id)))
$$;

-- One figure by label: "2.13", "Figure 2.13", "fig. 2.13".
create or replace function public.koro_figure(p_entity uuid, p_label text) returns jsonb
language plpgsql stable set search_path = public as $$
declare
  v_label text := regexp_replace(trim(coalesce(p_label, '')), '^(figure|fig\.?)\s*', '', 'i');
  v jsonb;
begin
  select koro_figure_json(a) into v from public.assets a
   where a.entity_id = p_entity and a.kind = 'image' and a.label = v_label;
  if v is null then
    raise exception 'no figure % in this book', v_label;
  end if;
  return v || jsonb_build_object('entity', (select jsonb_build_object('id', e.id, 'title', e.title)
                                              from public.entities e where e.id = p_entity));
end $$;

-- Figures for a concept key, or a section and everything under it. A figure label works
-- too when it is neither ("Figure 2.13" always means the figure).
create or replace function public.koro_figures(p_entity uuid, p_ref text) returns jsonb
language plpgsql stable set search_path = public, extensions as $$
declare
  v_ref text := trim(coalesce(p_ref, ''));
  v_path ltree;
  v_concept uuid;
begin
  if v_ref ~* '^(figure|fig\.?)\s*' then
    return jsonb_build_array(koro_figure(p_entity, v_ref));
  end if;
  select id into v_concept from public.concepts where entity_id = p_entity and key = v_ref and archived_at is null;
  if v_concept is not null then
    return (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]') from public.assets a
             where a.kind = 'image' and a.concept_id = v_concept);
  end if;
  select path into v_path from public.nodes where entity_id = p_entity and key = v_ref;
  if v_path is not null then
    return (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]')
              from public.assets a
              left join public.concepts c on c.id = a.concept_id
              join public.nodes n on n.id = coalesce(a.node_id, c.node_id)
             where a.entity_id = p_entity and a.kind = 'image' and n.path <@ v_path);
  end if;
  if exists (select 1 from public.assets where entity_id = p_entity and kind = 'image' and label = v_ref) then
    return jsonb_build_array(koro_figure(p_entity, v_ref));
  end if;
  raise exception 'no concept, section or figure %', v_ref;
end $$;

revoke execute on function public.koro_figure_json(public.assets) from public, anon, authenticated;
revoke execute on function public.koro_figure(uuid, text) from public, anon, authenticated;
revoke execute on function public.koro_figures(uuid, text) from public, anon, authenticated;
