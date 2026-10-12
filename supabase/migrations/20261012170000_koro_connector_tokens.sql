-- Access tokens for the remote MCP connector (claude.ai custom connector).
-- The connector URL carries a random token; only its SHA-256 hash is stored.
create table public.connector_tokens (
  id            uuid primary key default gen_random_uuid(),
  learner_id    uuid not null references public.learners (id) on delete cascade,
  token_hash    text not null unique,
  label         text not null default 'claude.ai',
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz,
  revoked_at    timestamptz
);
create index connector_tokens_learner_idx on public.connector_tokens (learner_id);
alter table public.connector_tokens enable row level security;
revoke all on public.connector_tokens from anon, authenticated;

-- Create a token for a learner. The raw token is returned once and never stored.
create or replace function public.koro_create_connector_token(p_learner uuid, p_label text default 'claude.ai')
returns jsonb
language plpgsql set search_path = public, extensions as $$
declare
  v_token text := 'koro_' || encode(gen_random_bytes(24), 'hex');
  v_id uuid;
begin
  if not exists (select 1 from public.learners where id = p_learner) then
    raise exception 'unknown learner %', p_learner;
  end if;
  insert into public.connector_tokens (learner_id, token_hash, label)
  values (p_learner, encode(digest(v_token, 'sha256'), 'hex'), coalesce(p_label, 'claude.ai'))
  returning id into v_id;
  return jsonb_build_object('token_id', v_id, 'token', v_token, 'label', p_label);
end $$;

-- Check a presented token; returns the learner or null. Records last use.
create or replace function public.koro_check_connector_token(p_token text) returns jsonb
language plpgsql set search_path = public, extensions as $$
declare t public.connector_tokens;
begin
  if p_token is null or length(p_token) < 20 then return null; end if;
  select * into t from public.connector_tokens
   where token_hash = encode(digest(p_token, 'sha256'), 'hex') and revoked_at is null;
  if not found then return null; end if;
  update public.connector_tokens set last_used_at = now()
   where id = t.id and (last_used_at is null or last_used_at < now() - interval '1 minute');
  return jsonb_build_object('learner_id', t.learner_id, 'token_id', t.id, 'label', t.label,
    'learner', (select display_name from public.learners where id = t.learner_id));
end $$;

create or replace function public.koro_list_connector_tokens(p_learner uuid) returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('token_id', id, 'label', label, 'created_at', created_at,
                                               'last_used_at', last_used_at, 'revoked_at', revoked_at)
                            order by created_at), '[]')
    from public.connector_tokens where learner_id = p_learner
$$;

create or replace function public.koro_revoke_connector_token(p_learner uuid, p_token_id uuid) returns jsonb
language plpgsql set search_path = public as $$
begin
  update public.connector_tokens set revoked_at = coalesce(revoked_at, now())
   where id = p_token_id and learner_id = p_learner;
  if not found then raise exception 'unknown token %', p_token_id; end if;
  return jsonb_build_object('token_id', p_token_id, 'revoked', true);
end $$;

revoke execute on function public.koro_create_connector_token(uuid, text) from public, anon, authenticated;
revoke execute on function public.koro_check_connector_token(text) from public, anon, authenticated;
revoke execute on function public.koro_list_connector_tokens(uuid) from public, anon, authenticated;
revoke execute on function public.koro_revoke_connector_token(uuid, uuid) from public, anon, authenticated;
