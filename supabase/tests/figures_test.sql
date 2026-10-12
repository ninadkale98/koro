-- Figures, section texts and the source file, checked against the live book inside one
-- transaction that always rolls back (it ends by raising KORO_FIGURES_OK). Nothing persists.
do $$
declare
  e uuid := (select id from public.entities where title = 'Inference Engineering');
  r jsonb;
  v_asset uuid;
  v_payload jsonb;
begin
  -- lookups: label vs section vs concept
  assert koro_figure(e, '2.13')->>'node_key' = '2.4.1', 'figure by label';
  assert koro_figure(e, 'Figure 5.3')->>'node_key' = '5.2', 'figure label with prefix';
  assert jsonb_array_length(koro_figures(e, '5.3')) = 4, 'section 5.3 subtree, not figure 5.3';
  assert jsonb_array_length(koro_figures(e, '2.4.1/arithmetic-intensity-roofline')) = 2, 'figures by concept';
  assert jsonb_array_length(koro_figures(e, '2')) = 18, 'chapter 2 has 18 figures';
  assert (select count(*) from public.assets where entity_id = e and kind = 'image') = 85, '85 figures';
  assert (select count(*) from public.assets where entity_id = e and kind = 'image' and concept_id is null) = 0, 'all linked';
  begin
    perform koro_figure(e, '9.99');
    assert false, 'unknown figure should raise';
  exception when raise_exception then null;
  end;

  -- beats carry their figures
  assert (select teaching_content->'beats'->1->'figures' from public.concepts
           where entity_id = e and key = '2.4.1/arithmetic-intensity-roofline') = '["2.13"]'::jsonb, 'beat figures';

  -- re-registering keeps the concept link
  perform koro_register_figures(e, (select jsonb_agg(jsonb_build_object('label', label, 'caption', caption, 'page', page,
            'bbox', bbox, 'node_key', '2.4.1', 'storage_path', storage_path)) from public.assets where entity_id = e and label = '2.13'));
  assert koro_figure(e, '2.13')->>'concept_key' = '2.4.1/arithmetic-intensity-roofline', 'link survives re-register';

  -- upload bookkeeping
  v_asset := (koro_figure(e, '2.13')->>'asset_id')::uuid;
  perform koro_mark_uploaded(v_asset, 1234, repeat('a', 64), 800, 600);
  r := koro_figure(e, '2.13');
  assert (r->>'uploaded')::boolean and (r->>'width')::int = 800, 'mark uploaded';
  r := koro_asset_status(e);
  assert (r->>'figures_uploaded')::int = 1, 'status counts uploads';
  perform koro_register_source(e, e || '/source.pdf', 24250428, 'b44e8d7a8cd1cc011ce621952e42b3ae8a1fc82bc7767a094e11f1b8091fbe3d');
  assert koro_asset_status(e)->'source'->>'sha256' = 'b44e8d7a8cd1cc011ce621952e42b3ae8a1fc82bc7767a094e11f1b8091fbe3d', 'source';

  -- section texts
  r := koro_save_node_sources(e, '{"2.4.1": "Roofline text.", "nope": "x"}'::jsonb);
  assert (r->>'saved')::int = 1 and r->'unknown_keys' = '["nope"]'::jsonb, 'save sources';
  r := koro_read_section(e, '2.4.1');
  assert r->>'text' = 'Roofline text.' and jsonb_array_length(r->'figures') = 2, 'read section';
  assert (koro_teach((select id from public.learners limit 1), '2.4.1/arithmetic-intensity-roofline', e, 'cli')->>'has_source_text')::boolean,
         'lesson knows the text exists';

  -- ingest: a claimed section lists its figures, and saving a beat with figures links them
  r := koro_claim_next_node(e, 'figures-test', 5, '5.3.1');
  assert (select string_agg(x->>'label', ',' order by x->>'label') from jsonb_array_elements(r->'figures') x) = '5.7,5.8', 'claim lists figures';
  v_payload := jsonb_build_object('summary', 'Prefix caching test.', 'concepts', jsonb_build_array(
    (select jsonb_build_object('key', 'prefix-caching', 'name', name, 'summary', summary, 'modality', modality,
            'teaching_content', jsonb_set(teaching_content, '{beats,0,figures}', '["5.7","5.8"]'),
            'objectives', (select jsonb_agg(jsonb_build_object('key', o.key, 'description', o.description))
                             from public.objectives o where o.concept_id = c.id and o.archived_at is null))
       from public.concepts c where entity_id = e and key = '5.3.1/prefix-caching')));
  perform koro_save_node(e, '5.3.1', v_payload, 'figures-test');
  assert (select count(*) from jsonb_array_elements(koro_figures(e, '5.3.1/prefix-caching'))) = 2, 'save links beat figures';

  raise exception 'KORO_FIGURES_OK';
end $$;
