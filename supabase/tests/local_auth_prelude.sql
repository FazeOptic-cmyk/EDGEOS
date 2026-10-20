-- WARNING: LOCAL VANILLA-POSTGRES TEST SHIM ONLY.
--
-- Never apply this file to a hosted or self-hosted Supabase project. Supabase
-- owns the auth schema, auth.users table, API roles, and auth.uid() function.
-- This minimal shim exists only so the EDGEOS public-schema migration can be
-- syntax/integrity-tested in the repository's plain postgres:16 container.
-- Mount it before migrations/20261016180000_initial_edgeos_schema.sql.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end;
$$;

create schema if not exists auth;
revoke all on schema auth from public, anon, authenticated;

create table if not exists auth.users (
  id uuid primary key,
  instance_id uuid,
  aud text,
  role text,
  email text unique,
  encrypted_password text,
  email_confirmed_at timestamptz,
  raw_app_meta_data jsonb not null default '{}'::jsonb,
  raw_user_meta_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function auth.uid()
returns uuid
language sql
stable
set search_path = ''
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

revoke all on function auth.uid() from public;
grant execute on function auth.uid() to anon, authenticated, service_role;
