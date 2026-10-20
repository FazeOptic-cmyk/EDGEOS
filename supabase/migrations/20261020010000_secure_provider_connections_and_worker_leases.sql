-- Secure user-scoped provider configuration, encrypted credential envelopes,
-- ingestion worker coordination, and versioned settlement-review evidence.
--
-- Credential encryption/decryption is deliberately performed by the trusted
-- application with a master key held outside PostgreSQL. The database stores
-- only an authenticated-encryption envelope; plaintext credentials never enter
-- an exposed schema, a JSON settings column, or a browser-readable view.

begin;

-- Stable provider reference rows are configuration metadata, not market data.
-- They contain no prices, markets, credentials, or claim of a current
-- connection. Runtime health remains in provider_connections and user-specific
-- connection rows.
insert into public.providers (
  slug, display_name, provider_kind, official_url, docs_url, feed_type,
  availability_status, requires_api_key, read_only, is_demo
)
values
  (
    'the_odds_api', 'The Odds API', 'odds_aggregator',
    'https://the-odds-api.com', 'https://the-odds-api.com/liveapi/guides/v4/',
    'polling', 'requires_configuration', true, true, false
  ),
  (
    'kalshi', 'Kalshi', 'prediction_market',
    'https://kalshi.com', 'https://docs.kalshi.com',
    'polling', 'disconnected', false, true, false
  ),
  (
    'polymarket_international', 'Polymarket International', 'prediction_market',
    'https://polymarket.com', 'https://docs.polymarket.com',
    'polling', 'disconnected', false, true, false
  ),
  (
    'polymarket_us', 'Polymarket US', 'prediction_market',
    'https://polymarket.us', 'https://docs.polymarket.us',
    'polling', 'disconnected', false, true, false
  ),
  (
    'edgeos_demo', 'EDGEOS Demo', 'internal', null, null,
    'demo', 'demo_only', false, true, true
  )
on conflict (slug) do update set
  display_name = excluded.display_name,
  provider_kind = excluded.provider_kind,
  official_url = excluded.official_url,
  docs_url = excluded.docs_url,
  requires_api_key = excluded.requires_api_key,
  read_only = true,
  is_demo = excluded.is_demo,
  updated_at = statement_timestamp();

-- Provider research products can be denominated in cash tokens such as USDC
-- or pUSD. Preserve the exact upstream denomination instead of coercing it to
-- USD. User portfolio/base currencies remain ISO-style three-letter codes;
-- cross-currency opportunities still require an explicit conversion model.
alter table public.markets drop constraint if exists markets_currency_check;
alter table public.markets add constraint markets_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.fee_schedules drop constraint if exists fee_schedules_currency_check;
alter table public.fee_schedules add constraint fee_schedules_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.quotes drop constraint if exists quotes_currency_check;
alter table public.quotes add constraint quotes_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.opportunities drop constraint if exists opportunities_currency_check;
alter table public.opportunities add constraint opportunities_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');

-- ---------- Safe user-visible connection metadata ----------

create table public.user_provider_connections (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  provider_id uuid not null references public.providers(id) on delete restrict,
  enabled boolean not null default true,
  status text not null default 'requires_configuration' check (
    status in (
      'requires_configuration', 'testing', 'connected', 'invalid_credentials',
      'insufficient_plan', 'rate_limited', 'provider_unavailable', 'error',
      'disabled'
    )
  ),
  selected_sports jsonb not null default '[]'::jsonb
    check (jsonb_typeof(selected_sports) = 'array'),
  selected_regions jsonb not null default '[]'::jsonb
    check (jsonb_typeof(selected_regions) = 'array'),
  selected_markets jsonb not null default '[]'::jsonb
    check (jsonb_typeof(selected_markets) = 'array'),
  poll_interval_seconds integer not null default 60
    check (poll_interval_seconds between 15 and 86400),
  request_budget_per_hour integer
    check (request_budget_per_hour is null or request_budget_per_hour > 0),
  credential_mask jsonb not null default '{}'::jsonb
    check (jsonb_typeof(credential_mask) = 'object'),
  capabilities jsonb not null default '{}'::jsonb
    check (jsonb_typeof(capabilities) = 'object'),
  coverage jsonb not null default '{}'::jsonb
    check (jsonb_typeof(coverage) = 'object'),
  quota jsonb not null default '{}'::jsonb
    check (jsonb_typeof(quota) = 'object'),
  last_tested_at timestamptz,
  last_successful_request_at timestamptz,
  last_market_update_at timestamptz,
  last_error_code text,
  last_error_summary text,
  disabled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, provider_id),
  unique (id, user_id),
  check ((enabled and status <> 'disabled' and disabled_at is null)
      or (not enabled and status = 'disabled' and disabled_at is not null))
);

create index user_provider_connections_user_status_idx
  on public.user_provider_connections (user_id, status, updated_at desc);
create index user_provider_connections_provider_enabled_idx
  on public.user_provider_connections (provider_id, updated_at desc)
  where enabled;

create trigger user_provider_connections_set_updated_at
before update on public.user_provider_connections
for each row execute function private.set_updated_at();

-- Preserve user-entered presentation text and retry identity for the durable
-- watchlist/alert API without weakening the canonical target foreign keys.
alter table public.user_watchlist_items
  add column label text check (label is null or length(btrim(label)) between 1 and 200),
  add column detail text check (detail is null or length(detail) <= 1000),
  add column idempotency_key text
    check (idempotency_key is null or length(idempotency_key) between 8 and 200);

create unique index user_watchlist_items_idempotency_uidx
  on public.user_watchlist_items (user_id, idempotency_key)
  where idempotency_key is not null;

alter table public.user_alerts
  add column idempotency_key text
    check (idempotency_key is null or length(idempotency_key) between 8 and 200);

alter table public.user_alerts
  drop constraint user_alerts_name_check,
  add constraint user_alerts_name_check
    check (length(btrim(name)) between 1 and 160);

create unique index user_alerts_idempotency_uidx
  on public.user_alerts (user_id, idempotency_key)
  where idempotency_key is not null;

-- ---------- Non-exposed authenticated-encryption envelopes ----------

create table private.provider_credentials (
  connection_id uuid primary key,
  user_id uuid not null,
  algorithm text not null default 'AES-256-GCM'
    check (algorithm = 'AES-256-GCM'),
  key_version text not null check (length(btrim(key_version)) between 1 and 80),
  nonce bytea not null check (octet_length(nonce) = 12),
  ciphertext bytea not null check (octet_length(ciphertext) >= 16),
  credential_fields text[] not null default '{}',
  credential_fingerprint text,
  created_at timestamptz not null default now(),
  rotated_at timestamptz not null default now(),
  foreign key (connection_id, user_id)
    references public.user_provider_connections(id, user_id) on delete cascade,
  check (cardinality(credential_fields) > 0),
  check (credential_fingerprint is null or length(credential_fingerprint) >= 16)
);

comment on table private.provider_credentials is
  'Server-only AES-GCM envelopes. Master keys and plaintext provider credentials are never stored in PostgreSQL.';

alter table private.provider_credentials enable row level security;
alter table private.provider_credentials force row level security;

-- No anon/authenticated policies or grants are defined for the private table.
-- service_role access supports a trusted backend using a non-exposed schema;
-- hosted deployments may instead connect as a dedicated least-privilege role.
revoke all on table private.provider_credentials from public, anon, authenticated;
grant usage on schema private to service_role;
grant select, insert, update, delete on private.provider_credentials to service_role;

-- ---------- Worker lease and heartbeat coordination ----------

create table public.ingestion_worker_leases (
  lease_key text primary key check (lease_key ~ '^[a-z0-9][a-z0-9:_-]{0,119}$'),
  holder_id text not null check (length(btrim(holder_id)) between 1 and 160),
  lease_token uuid not null unique default extensions.gen_random_uuid(),
  acquired_at timestamptz not null default now(),
  heartbeat_at timestamptz not null default now(),
  expires_at timestamptz not null,
  released_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object'),
  updated_at timestamptz not null default now(),
  check (heartbeat_at >= acquired_at),
  check (expires_at > heartbeat_at),
  check (released_at is null or released_at >= acquired_at)
);

create index ingestion_worker_leases_expiry_idx
  on public.ingestion_worker_leases (expires_at)
  where released_at is null;

create trigger ingestion_worker_leases_set_updated_at
before update on public.ingestion_worker_leases
for each row execute function private.set_updated_at();

alter table public.ingestion_worker_leases enable row level security;
revoke all on table public.ingestion_worker_leases from anon, authenticated;
grant all privileges on table public.ingestion_worker_leases to service_role;

alter table public.ingestion_runs
  add column adapter_version text not null default 'unknown',
  add column schema_version text not null default '1',
  add column worker_id text,
  add column heartbeat_at timestamptz;

alter table public.ingestion_runs
  add constraint ingestion_runs_heartbeat_after_start_check
  check (heartbeat_at is null or heartbeat_at >= started_at);

alter table public.raw_ingestion_records
  add column adapter_version text not null default 'unknown',
  add column schema_version text not null default '1',
  add column provider_observed_at timestamptz,
  add column fetched_at timestamptz not null default now(),
  add column ingested_at timestamptz not null default now();

alter table public.raw_ingestion_records
  add constraint raw_ingestion_record_timestamp_order_check
  check (
    (provider_observed_at is null or fetched_at >= provider_observed_at - interval '24 hours')
    and ingested_at >= fetched_at - interval '5 minutes'
  );

-- ---------- Versioned settlement evidence and invalidation ----------

alter table public.market_matches
  add column left_rule_fingerprint text,
  add column right_rule_fingerprint text,
  add column decision_fingerprint text,
  add column decision_version integer not null default 1
    check (decision_version > 0),
  add column decision_source_evidence jsonb not null default '{}'::jsonb
    check (jsonb_typeof(decision_source_evidence) = 'object'),
  add column invalidated_at timestamptz,
  add column invalidation_reason text;

alter table public.market_matches
  add constraint market_matches_invalidation_evidence_check
  check ((invalidated_at is null) = (invalidation_reason is null));

create index market_matches_invalidation_idx
  on public.market_matches (invalidated_at desc)
  where invalidated_at is not null;

alter table public.match_reviews
  add column decision_version integer,
  add column left_rule_fingerprint text,
  add column right_rule_fingerprint text,
  add column decision_fingerprint text,
  add column source_evidence jsonb not null default '{}'::jsonb
    check (jsonb_typeof(source_evidence) = 'object'),
  add column supersedes_review_id uuid references public.match_reviews(id) on delete set null,
  add column invalidated_at timestamptz;

with versioned as (
  select id,
         row_number() over (
           partition by market_match_id order by reviewed_at, id
         )::integer as decision_version
  from public.match_reviews
)
update public.match_reviews review
set decision_version = versioned.decision_version
from versioned
where review.id = versioned.id;

alter table public.match_reviews
  alter column decision_version set default 1,
  alter column decision_version set not null,
  add constraint match_reviews_decision_version_positive_check
    check (decision_version > 0);

update public.market_matches target_match
set decision_version = greatest(target_match.decision_version, reviews.latest_version)
from (
  select market_match_id, max(decision_version) as latest_version
  from public.match_reviews
  group by market_match_id
) reviews
where target_match.id = reviews.market_match_id;

create unique index match_reviews_match_decision_version_uidx
  on public.match_reviews (market_match_id, decision_version);
create index match_reviews_fingerprint_idx
  on public.match_reviews (decision_fingerprint)
  where decision_fingerprint is not null;

create or replace function private.invalidate_market_matches_for_rule_change()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  affected_market_id uuid;
  changed_rule_id uuid;
  matched record;
  next_version integer;
begin
  if tg_op = 'DELETE' then
    affected_market_id := old.market_id;
    changed_rule_id := old.id;
  else
    affected_market_id := new.market_id;
    changed_rule_id := new.id;
  end if;

  for matched in
    select m.id, m.classification, m.decision_version,
           m.left_rule_fingerprint, m.right_rule_fingerprint,
           m.decision_fingerprint
    from public.market_matches m
    where (m.left_market_id = affected_market_id or m.right_market_id = affected_market_id)
      and (m.classification = 'verified' or m.invalidated_at is null)
    for update
  loop
    next_version := matched.decision_version + 1;

    update public.market_matches
    set classification = 'review_required',
        settlement_confidence = 0,
        verified_at = null,
        decision_version = next_version,
        invalidated_at = statement_timestamp(),
        invalidation_reason = 'material_market_rule_change',
        updated_at = statement_timestamp()
    where id = matched.id;

    insert into public.match_reviews (
      market_match_id, previous_classification, decision, reviewer_kind,
      checks, rationale, decision_version, left_rule_fingerprint,
      right_rule_fingerprint, decision_fingerprint, source_evidence
    ) values (
      matched.id, matched.classification, 'review_required', 'system',
      jsonb_build_object('rule_change_operation', tg_op),
      'Material settlement-rule evidence changed; prior equivalence must be reviewed.',
      next_version, matched.left_rule_fingerprint, matched.right_rule_fingerprint,
      matched.decision_fingerprint,
      jsonb_build_object(
        'market_id', affected_market_id,
        'market_rule_id', changed_rule_id,
        'operation', tg_op,
        'invalidated_at', statement_timestamp()
      )
    );

    update public.opportunities
    set status = 'invalidated',
        last_valid_at = coalesce(last_valid_at, statement_timestamp()),
        hard_gate_failures = hard_gate_failures ||
          jsonb_build_array('settlement_rules_changed'),
        updated_at = statement_timestamp()
    where market_match_id = matched.id and status = 'active';
  end loop;

  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end;
$$;

revoke all on function private.invalidate_market_matches_for_rule_change()
  from public, anon, authenticated;

create trigger market_rules_invalidate_matches
after insert or update or delete on public.market_rules
for each row execute function private.invalidate_market_matches_for_rule_change();

-- ---------- RLS and explicit Data API privileges ----------

alter table public.user_provider_connections enable row level security;

create policy user_provider_connections_owner_select
on public.user_provider_connections
for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on table public.user_provider_connections from anon, authenticated;
grant select on table public.user_provider_connections to authenticated;
grant all privileges on table public.user_provider_connections to service_role;

-- Revoke is repeated after all new functions/tables to make the boundary clear
-- even when a deployment has different role defaults.
revoke all on schema private from public, anon, authenticated;
revoke all on table private.provider_credentials from public, anon, authenticated;

commit;
