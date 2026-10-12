-- What of a source is stored: the original file, section texts, figures. `koro ingest
-- assets` reads this to upload only what is missing, and to check that the local PDF
-- is the same file the book was built from.

create or replace function public.koro_asset_status(p_entity uuid) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'entity', (select jsonb_build_object('id', e.id, 'title', e.title, 'source_hash', e.source_hash,
                                         'canonical_id', e.canonical_id, 'page_count', e.page_count)
                 from public.entities e where e.id = p_entity),
    'source', (select jsonb_build_object('asset_id', a.id, 'bucket', a.bucket, 'storage_path', a.storage_path,
                                         'sha256', a.sha256, 'size_bytes', a.size_bytes, 'uploaded_at', a.uploaded_at)
                 from public.assets a where a.entity_id = p_entity and a.kind = 'source' and a.label = 'source'),
    'sections', jsonb_build_object(
      'total', (select count(*) from public.nodes where entity_id = p_entity),
      'with_text', (select count(*) from public.node_sources where entity_id = p_entity),
      'chars', (select coalesce(sum(chars), 0) from public.node_sources where entity_id = p_entity)),
    'figures', (select coalesce(jsonb_agg(koro_figure_json(a) order by a.page, a.label), '[]')
                  from public.assets a where a.entity_id = p_entity and a.kind = 'image'),
    'figures_uploaded', (select count(*) from public.assets
                          where entity_id = p_entity and kind = 'image' and uploaded_at is not null))
$$;

revoke execute on function public.koro_asset_status(uuid) from public, anon, authenticated;
