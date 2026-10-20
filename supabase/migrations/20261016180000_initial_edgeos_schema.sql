-- EDGEOS initial database schema.
-- PostgreSQL 15 / Supabase. All money, prices, probabilities, and quantities
-- use exact NUMERIC values; all instants use TIMESTAMPTZ (stored in UTC).

begin;

create schema if not exists extensions;
revoke all on schema extensions from public;
grant usage on schema extensions to postgres, anon, authenticated, service_role;
create extension if not exists pgcrypto with schema extensions;
create extension if not exists btree_gist with schema extensions;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
revoke create on schema public from public, anon, authenticated;

-- Functions default to EXECUTE for PUBLIC in PostgreSQL. This revoke must be
-- global for the creating role: a per-schema default cannot override the
-- built-in global PUBLIC grant. Later migrations can grant selected helpers.
alter default privileges for role postgres
  revoke execute on functions from public;

-- Trigger helpers live outside the exposed public schema. The Auth trigger is
-- necessarily SECURITY DEFINER; it has an empty search_path, fully-qualified
-- names, a fixed operation, and no EXECUTE grant to API roles.
create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := statement_timestamp();
  return new;
end;
$$;

revoke all on function private.set_updated_at() from public, anon, authenticated;

-- ---------- Shared provider and canonical market reference data ----------

create table public.providers (
  id uuid primary key default extensions.gen_random_uuid(),
  slug text not null unique check (slug = lower(slug) and slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  display_name text not null check (length(btrim(display_name)) between 1 and 120),
  provider_kind text not null check (provider_kind in ('sportsbook', 'prediction_market', 'odds_aggregator', 'results', 'internal')),
  jurisdiction text,
  official_url text,
  docs_url text,
  feed_type text not null default 'unavailable' check (feed_type in ('streaming', 'polling', 'cached', 'historical', 'demo', 'unavailable')),
  availability_status text not null default 'requires_configuration' check (availability_status in ('connected', 'delayed', 'disconnected', 'requires_configuration', 'not_supported', 'demo_only')),
  requires_api_key boolean not null default false,
  read_only boolean not null default true check (read_only),
  refresh_interval_seconds integer check (refresh_interval_seconds is null or refresh_interval_seconds > 0),
  data_delay_seconds integer check (data_delay_seconds is null or data_delay_seconds >= 0),
  data_delay_known boolean not null default false,
  is_enabled boolean not null default true,
  is_demo boolean not null default false,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.provider_connections (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null unique references public.providers(id) on delete cascade,
  status text not null default 'disconnected' check (status in ('connected', 'delayed', 'disconnected', 'requires_api_key', 'not_supported', 'demo_only')),
  last_attempt_at timestamptz,
  last_success_at timestamptz,
  last_error_at timestamptz,
  last_error_code text,
  last_error_summary text,
  rate_limit_state text not null default 'unknown' check (rate_limit_state in ('ok', 'near_limit', 'limited', 'exhausted', 'unknown')),
  quota_limit bigint check (quota_limit is null or quota_limit >= 0),
  quota_remaining bigint check (quota_remaining is null or quota_remaining >= 0),
  quota_resets_at timestamptz,
  active_instruments integer not null default 0 check (active_instruments >= 0),
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  diagnostics jsonb not null default '{}'::jsonb check (jsonb_typeof(diagnostics) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (quota_limit is null or quota_remaining is null or quota_remaining <= quota_limit)
);

create table public.sports (
  id uuid primary key default extensions.gen_random_uuid(),
  slug text not null unique check (slug = lower(slug) and slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  name text not null unique,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.leagues (
  id uuid primary key default extensions.gen_random_uuid(),
  sport_id uuid not null references public.sports(id) on delete restrict,
  slug text not null check (slug = lower(slug) and slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  name text not null,
  country_code text check (country_code is null or country_code ~ '^[A-Z]{2}$'),
  timezone text not null default 'UTC',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (sport_id, slug)
);

create table public.participants (
  id uuid primary key default extensions.gen_random_uuid(),
  sport_id uuid not null references public.sports(id) on delete restrict,
  canonical_name text not null,
  normalized_name text not null,
  participant_type text not null default 'team' check (participant_type in ('team', 'player', 'competitor', 'field')),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (sport_id, participant_type, normalized_name)
);

create table public.participant_aliases (
  id uuid primary key default extensions.gen_random_uuid(),
  participant_id uuid not null references public.participants(id) on delete cascade,
  provider_id uuid references public.providers(id) on delete cascade,
  alias text not null,
  normalized_alias text not null,
  created_at timestamptz not null default now(),
  unique nulls not distinct (participant_id, provider_id, normalized_alias)
);

create table public.events (
  id uuid primary key default extensions.gen_random_uuid(),
  canonical_key text not null unique,
  sport_id uuid not null references public.sports(id) on delete restrict,
  league_id uuid references public.leagues(id) on delete restrict,
  home_participant_id uuid references public.participants(id) on delete restrict,
  away_participant_id uuid references public.participants(id) on delete restrict,
  name text not null,
  scheduled_start timestamptz not null,
  status text not null default 'scheduled' check (status in ('scheduled', 'live', 'suspended', 'final', 'cancelled', 'postponed', 'unknown')),
  venue text,
  neutral_site boolean,
  result_data jsonb check (result_data is null or jsonb_typeof(result_data) = 'object'),
  result_confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (home_participant_id is null or away_participant_id is null or home_participant_id <> away_participant_id),
  check ((result_data is null) = (result_confirmed_at is null))
);

create table public.provider_events (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete cascade,
  external_event_id text not null,
  canonical_event_id uuid references public.events(id) on delete set null,
  title text not null,
  scheduled_start timestamptz,
  status text not null default 'unknown' check (status in ('scheduled', 'live', 'suspended', 'final', 'cancelled', 'postponed', 'unknown')),
  source_url text,
  raw_fingerprint text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  is_demo boolean not null default false,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider_id, external_event_id),
  unique (id, provider_id),
  check (last_seen_at >= first_seen_at)
);

create table public.markets (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete restrict,
  provider_event_id uuid references public.provider_events(id) on delete cascade,
  event_id uuid references public.events(id) on delete set null,
  external_market_id text not null,
  title text not null,
  market_type text not null,
  period text not null default 'full_game',
  line numeric(24,8),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  status text not null default 'open' check (status in ('open', 'suspended', 'closed', 'settled', 'cancelled', 'unknown')),
  live boolean not null default false,
  mutually_exclusive boolean,
  collectively_exhaustive boolean,
  minimum_increment numeric(24,8) check (minimum_increment is null or minimum_increment > 0),
  opens_at timestamptz,
  closes_at timestamptz,
  settled_at timestamptz,
  source_url text,
  last_seen_at timestamptz not null default now(),
  is_demo boolean not null default false,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider_id, external_market_id),
  unique (id, provider_id),
  foreign key (provider_event_id, provider_id)
    references public.provider_events(id, provider_id) on delete cascade,
  check (closes_at is null or opens_at is null or closes_at >= opens_at),
  check (settled_at is null or status in ('settled', 'cancelled'))
);

create table public.market_rules (
  id uuid primary key default extensions.gen_random_uuid(),
  market_id uuid not null references public.markets(id) on delete cascade,
  rule_version integer not null default 1 check (rule_version > 0),
  includes_overtime boolean,
  tie_treatment text check (tie_treatment is null or tie_treatment in ('separate_outcome', 'push', 'loss', 'not_applicable', 'unknown')),
  push_treatment text check (push_treatment is null or push_treatment in ('refund', 'half_win', 'half_loss', 'loss', 'not_applicable', 'unknown')),
  void_treatment text check (void_treatment is null or void_treatment in ('refund', 'loss', 'regrade', 'unknown')),
  cancellation_treatment text check (cancellation_treatment is null or cancellation_treatment in ('refund', 'action', 'reschedule', 'unknown')),
  settlement_source text,
  rules_url text,
  rules_text text,
  rules_hash text not null,
  effective_from timestamptz not null default now(),
  effective_to timestamptz,
  verified_at timestamptz,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (market_id, rule_version),
  unique (market_id, rules_hash),
  constraint market_rules_effective_period_no_overlap exclude using gist (
    market_id with =,
    tstzrange(effective_from, effective_to, '[)') with &&
  ),
  check (effective_to is null or effective_to > effective_from)
);

create table public.outcomes (
  id uuid primary key default extensions.gen_random_uuid(),
  market_id uuid not null references public.markets(id) on delete cascade,
  external_outcome_id text not null,
  canonical_key text not null,
  name text not null,
  outcome_type text not null,
  participant_id uuid references public.participants(id) on delete restrict,
  line numeric(24,8),
  payout_per_unit numeric(24,8) not null check (payout_per_unit >= 0),
  status text not null default 'open' check (status in ('open', 'suspended', 'won', 'lost', 'push', 'void', 'unknown')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (market_id, external_outcome_id),
  unique (market_id, canonical_key),
  unique (id, market_id)
);

create table public.fee_schedules (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete cascade,
  name text not null,
  version text not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  calculation_method text not null check (calculation_method in ('none', 'flat', 'percentage_notional', 'percentage_profit', 'contract_curve', 'custom')),
  parameters jsonb not null default '{}'::jsonb check (jsonb_typeof(parameters) = 'object'),
  rounding_mode text not null default 'half_up' check (rounding_mode in ('half_up', 'half_even', 'floor', 'ceiling')),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  official_source_url text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  unique (provider_id, version),
  constraint fee_schedules_effective_period_no_overlap exclude using gist (
    provider_id with =,
    name with =,
    currency with =,
    tstzrange(effective_from, effective_to, '[)') with &&
  ),
  check (effective_to is null or effective_to > effective_from)
);

create table public.quotes (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete restrict,
  market_id uuid not null references public.markets(id) on delete cascade,
  outcome_id uuid not null references public.outcomes(id) on delete cascade,
  ingestion_key text not null,
  decimal_odds numeric(24,10) check (decimal_odds is null or decimal_odds >= 1),
  probability_price numeric(24,10) check (probability_price is null or probability_price between 0 and 1),
  best_bid numeric(24,10) check (best_bid is null or best_bid between 0 and 1),
  best_ask numeric(24,10) check (best_ask is null or best_ask between 0 and 1),
  bid_size numeric(24,8) check (bid_size is null or bid_size >= 0),
  ask_size numeric(24,8) check (ask_size is null or ask_size >= 0),
  available_size numeric(24,8) check (available_size is null or available_size >= 0),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  feed_class text not null check (feed_class in ('streaming', 'polled', 'cached', 'historical', 'demo')),
  observed_at timestamptz not null,
  received_at timestamptz not null default now(),
  stale_at timestamptz,
  source_sequence text,
  is_in_play boolean not null default false,
  is_demo boolean not null default false,
  raw_fingerprint text,
  created_at timestamptz not null default now(),
  unique (provider_id, ingestion_key),
  unique (id, provider_id, market_id, outcome_id),
  foreign key (market_id, provider_id)
    references public.markets(id, provider_id) on delete cascade,
  foreign key (outcome_id, market_id)
    references public.outcomes(id, market_id) on delete cascade,
  check (decimal_odds is not null or probability_price is not null or best_bid is not null or best_ask is not null),
  check (best_bid is null or best_ask is null or best_bid <= best_ask),
  check (received_at >= observed_at - interval '24 hours'),
  check (stale_at is null or stale_at >= observed_at),
  check ((feed_class = 'demo') = is_demo)
);

create table public.orderbook_snapshots (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete restrict,
  market_id uuid not null references public.markets(id) on delete cascade,
  outcome_id uuid references public.outcomes(id) on delete cascade,
  ingestion_key text not null,
  observed_at timestamptz not null,
  received_at timestamptz not null default now(),
  sequence_number bigint,
  depth_complete boolean not null default false,
  feed_class text not null check (feed_class in ('streaming', 'polled', 'cached', 'historical', 'demo')),
  is_demo boolean not null default false,
  checksum text,
  created_at timestamptz not null default now(),
  unique (provider_id, ingestion_key),
  foreign key (market_id, provider_id)
    references public.markets(id, provider_id) on delete cascade,
  foreign key (outcome_id, market_id)
    references public.outcomes(id, market_id) on delete cascade,
  check ((feed_class = 'demo') = is_demo)
);

create table public.orderbook_levels (
  id uuid primary key default extensions.gen_random_uuid(),
  snapshot_id uuid not null references public.orderbook_snapshots(id) on delete cascade,
  side text not null check (side in ('bid', 'ask')),
  level_number integer not null check (level_number >= 0),
  price numeric(24,10) not null check (price between 0 and 1),
  quantity numeric(24,8) not null check (quantity > 0),
  order_count integer check (order_count is null or order_count >= 0),
  unique (snapshot_id, side, level_number),
  unique (snapshot_id, side, price)
);

-- ---------- Matching, opportunity detection, and immutable snapshots ----------

create table public.market_matches (
  id uuid primary key default extensions.gen_random_uuid(),
  left_market_id uuid not null references public.markets(id) on delete cascade,
  right_market_id uuid not null references public.markets(id) on delete cascade,
  classification text not null check (classification in ('verified', 'review_required', 'rejected')),
  candidate_score numeric(8,6) check (candidate_score is null or candidate_score between 0 and 1),
  settlement_confidence numeric(8,6) not null default 0 check (settlement_confidence between 0 and 1),
  equivalence_checks jsonb not null check (jsonb_typeof(equivalence_checks) = 'object'),
  explanation text not null check (length(btrim(explanation)) > 0),
  engine_version text not null,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (left_market_id <> right_market_id),
  check (classification <> 'verified' or (settlement_confidence = 1 and verified_at is not null))
);

create unique index market_matches_unordered_pair_uidx
  on public.market_matches (least(left_market_id, right_market_id), greatest(left_market_id, right_market_id), engine_version);

create table public.match_reviews (
  id uuid primary key default extensions.gen_random_uuid(),
  market_match_id uuid not null references public.market_matches(id) on delete cascade,
  previous_classification text check (previous_classification is null or previous_classification in ('verified', 'review_required', 'rejected')),
  decision text not null check (decision in ('verified', 'review_required', 'rejected')),
  reviewer_kind text not null check (reviewer_kind in ('system', 'operator')),
  reviewer_id uuid references auth.users(id) on delete set null,
  checks jsonb not null check (jsonb_typeof(checks) = 'object'),
  rationale text not null check (length(btrim(rationale)) > 0),
  reviewed_at timestamptz not null default now()
);

create table public.opportunities (
  id uuid primary key default extensions.gen_random_uuid(),
  opportunity_key text not null unique,
  market_match_id uuid references public.market_matches(id) on delete set null,
  event_id uuid references public.events(id) on delete set null,
  strategy_type text not null check (strategy_type in ('sportsbook_sportsbook', 'sportsbook_prediction', 'prediction_prediction', 'multi_outcome', 'complementary_contracts')),
  status text not null default 'active' check (status in ('active', 'stale', 'invalidated', 'expired', 'settled')),
  match_status text not null check (match_status in ('verified', 'review_required', 'rejected')),
  total_allocation numeric(24,8) not null check (total_allocation >= 0),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  gross_profit numeric(24,8) not null,
  total_fees numeric(24,8) not null check (total_fees >= 0),
  worst_case_net_profit numeric(24,8) not null,
  best_case_net_profit numeric(24,8) not null,
  gross_roi numeric(18,10),
  net_roi numeric(18,10),
  maximum_depth numeric(24,8) check (maximum_depth is null or maximum_depth >= 0),
  conservative_net_profit numeric(24,8) not null,
  execution_confidence numeric(8,6) not null check (execution_confidence between 0 and 1),
  research_score numeric(8,4) not null check (research_score between 0 and 100),
  score_breakdown jsonb not null check (jsonb_typeof(score_breakdown) = 'object'),
  hard_gate_failures jsonb not null default '[]'::jsonb check (jsonb_typeof(hard_gate_failures) = 'array'),
  detected_at timestamptz not null,
  last_valid_at timestamptz,
  expires_at timestamptz,
  is_demo boolean not null default false,
  assumptions jsonb not null default '{}'::jsonb check (jsonb_typeof(assumptions) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (best_case_net_profit >= worst_case_net_profit),
  check (match_status = 'verified' or status <> 'active'),
  check (last_valid_at is null or last_valid_at >= detected_at),
  check (expires_at is null or expires_at >= detected_at)
);

create table public.opportunity_legs (
  id uuid primary key default extensions.gen_random_uuid(),
  opportunity_id uuid not null references public.opportunities(id) on delete cascade,
  leg_number smallint not null check (leg_number > 0),
  provider_id uuid not null references public.providers(id) on delete restrict,
  market_id uuid not null references public.markets(id) on delete restrict,
  outcome_id uuid not null references public.outcomes(id) on delete restrict,
  quote_id uuid references public.quotes(id) on delete restrict,
  action text not null check (action in ('back', 'lay', 'buy_yes', 'buy_no', 'sell_yes', 'sell_no')),
  price numeric(24,10) not null check (price > 0),
  quantity numeric(24,8) not null check (quantity > 0),
  allocation numeric(24,8) not null check (allocation >= 0),
  fee numeric(24,8) not null default 0 check (fee >= 0),
  available_size numeric(24,8) check (available_size is null or available_size >= 0),
  expected_slippage numeric(24,10) not null default 0 check (expected_slippage >= 0),
  quote_observed_at timestamptz not null,
  unique (opportunity_id, leg_number),
  foreign key (market_id, provider_id)
    references public.markets(id, provider_id) on delete restrict,
  foreign key (outcome_id, market_id)
    references public.outcomes(id, market_id) on delete restrict,
  foreign key (quote_id, provider_id, market_id, outcome_id)
    references public.quotes(id, provider_id, market_id, outcome_id) on delete restrict
);

create table public.opportunity_outcomes (
  id uuid primary key default extensions.gen_random_uuid(),
  opportunity_id uuid not null references public.opportunities(id) on delete cascade,
  scenario_key text not null,
  label text not null,
  gross_payout numeric(24,8) not null,
  refunds numeric(24,8) not null default 0 check (refunds >= 0),
  fees numeric(24,8) not null default 0 check (fees >= 0),
  net_profit numeric(24,8) not null,
  scenario_data jsonb not null default '{}'::jsonb check (jsonb_typeof(scenario_data) = 'object'),
  unique (opportunity_id, scenario_key)
);

create table public.opportunity_snapshots (
  id uuid primary key default extensions.gen_random_uuid(),
  opportunity_id uuid not null references public.opportunities(id) on delete cascade,
  ingestion_key text not null unique,
  observed_at timestamptz not null,
  status text not null check (status in ('active', 'stale', 'invalidated', 'expired', 'settled')),
  total_allocation numeric(24,8) not null check (total_allocation >= 0),
  gross_profit numeric(24,8) not null,
  net_profit numeric(24,8) not null,
  net_roi numeric(18,10),
  available_liquidity numeric(24,8) check (available_liquidity is null or available_liquidity >= 0),
  execution_confidence numeric(8,6) not null check (execution_confidence between 0 and 1),
  quote_ids uuid[] not null default '{}',
  snapshot_data jsonb not null default '{}'::jsonb check (jsonb_typeof(snapshot_data) = 'object'),
  is_demo boolean not null default false,
  created_at timestamptz not null default now()
);

-- ---------- Auth-linked private user data ----------

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text check (display_name is null or length(btrim(display_name)) between 1 and 80),
  avatar_url text check (avatar_url is null or (length(avatar_url) <= 2048 and avatar_url ~ '^https://')),
  timezone text not null default 'UTC',
  base_currency text not null default 'USD' check (base_currency ~ '^[A-Z]{3}$'),
  onboarding_completed boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_settings (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  theme text not null default 'dark' check (theme in ('dark', 'light', 'system')),
  compact_mode boolean not null default true,
  sidebar_collapsed boolean not null default false,
  default_date_range text not null default '30d' check (default_date_range in ('today', '7d', '30d', '90d', 'ytd', 'all', 'custom')),
  max_quote_age_seconds integer not null default 120 check (max_quote_age_seconds > 0),
  default_capital numeric(24,8) not null default 1000 check (default_capital >= 0),
  notification_preferences jsonb not null default '{"in_app":true}'::jsonb check (jsonb_typeof(notification_preferences) = 'object'),
  analytics_preferences jsonb not null default '{}'::jsonb check (jsonb_typeof(analytics_preferences) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_saved_filters (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  page_key text not null check (page_key in ('scanner', 'markets', 'edge_lab', 'portfolio', 'analytics')),
  name text not null check (length(btrim(name)) between 1 and 80),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  is_default boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, page_key, name),
  unique (id, user_id)
);

create unique index user_saved_filters_one_default_uidx
  on public.user_saved_filters (user_id, page_key) where is_default;

create table public.user_watchlists (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 80),
  description text,
  is_default boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name),
  unique (id, user_id)
);

create unique index user_watchlists_one_default_uidx
  on public.user_watchlists (user_id) where is_default;

create table public.user_watchlist_items (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  watchlist_id uuid not null,
  target_type text not null check (target_type in ('participant', 'sport', 'league', 'event', 'market', 'provider', 'strategy')),
  participant_id uuid references public.participants(id) on delete cascade,
  sport_id uuid references public.sports(id) on delete cascade,
  league_id uuid references public.leagues(id) on delete cascade,
  event_id uuid references public.events(id) on delete cascade,
  market_id uuid references public.markets(id) on delete cascade,
  provider_id uuid references public.providers(id) on delete cascade,
  strategy_key text,
  created_at timestamptz not null default now(),
  foreign key (watchlist_id, user_id) references public.user_watchlists(id, user_id) on delete cascade,
  unique (id, user_id),
  check (num_nonnulls(participant_id, sport_id, league_id, event_id, market_id, provider_id, strategy_key) = 1),
  check (
    (target_type = 'participant' and participant_id is not null) or
    (target_type = 'sport' and sport_id is not null) or
    (target_type = 'league' and league_id is not null) or
    (target_type = 'event' and event_id is not null) or
    (target_type = 'market' and market_id is not null) or
    (target_type = 'provider' and provider_id is not null) or
    (target_type = 'strategy' and strategy_key is not null)
  )
);

create unique index user_watchlist_items_identity_uidx on public.user_watchlist_items (
  watchlist_id,
  target_type,
  coalesce(participant_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(sport_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(league_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(event_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(market_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(provider_id, '00000000-0000-0000-0000-000000000000'::uuid),
  coalesce(strategy_key, '')
);

create table public.user_alerts (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 100),
  alert_type text not null check (alert_type in ('new_verified_opportunity', 'roi_threshold', 'liquidity_increase', 'spread_widens', 'quote_stale', 'market_closed', 'market_settled', 'provider_offline')),
  conditions jsonb not null check (jsonb_typeof(conditions) = 'object'),
  enabled boolean not null default true,
  cooldown_seconds integer not null default 300 check (cooldown_seconds >= 0),
  last_triggered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id),
  unique (user_id, name)
);

create table public.user_alert_events (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  alert_id uuid not null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  provider_id uuid references public.providers(id) on delete set null,
  dedupe_key text not null,
  title text not null,
  body text not null,
  severity text not null default 'info' check (severity in ('info', 'warning', 'critical')),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  triggered_at timestamptz not null default now(),
  read_at timestamptz,
  dismissed_at timestamptz,
  foreign key (alert_id, user_id) references public.user_alerts(id, user_id) on delete cascade,
  unique (alert_id, dedupe_key),
  check (dismissed_at is null or read_at is not null)
);

create table public.user_saved_opportunities (
  user_id uuid not null references public.profiles(id) on delete cascade,
  opportunity_id uuid not null references public.opportunities(id) on delete cascade,
  favorited boolean not null default true,
  saved_at timestamptz not null default now(),
  primary key (user_id, opportunity_id)
);

create table public.user_research_notes (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  opportunity_id uuid references public.opportunities(id) on delete cascade,
  event_id uuid references public.events(id) on delete cascade,
  market_id uuid references public.markets(id) on delete cascade,
  title text,
  body text not null check (length(btrim(body)) > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (num_nonnulls(opportunity_id, event_id, market_id) = 1)
);

-- ---------- Simulated portfolio and auditable accounting ----------

create table public.paper_portfolios (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 80),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  starting_balance numeric(24,8) not null default 10000 check (starting_balance >= 0),
  status text not null default 'active' check (status in ('active', 'archived')),
  is_default boolean not null default false,
  account_kind text not null default 'paper' check (account_kind = 'paper'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name),
  unique (id, user_id)
);

create unique index paper_portfolios_one_default_uidx
  on public.paper_portfolios (user_id) where is_default and status = 'active';

create table public.paper_positions (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  portfolio_id uuid not null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  external_reference text,
  position_type text not null check (position_type in ('arbitrage_bundle', 'single_leg', 'positive_ev')),
  strategy_type text not null,
  title text not null,
  status text not null default 'draft' check (status in ('draft', 'open', 'partially_filled', 'filled', 'closed', 'settled', 'cancelled')),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  planned_allocation numeric(24,8) not null check (planned_allocation >= 0),
  committed_capital numeric(24,8) not null default 0 check (committed_capital >= 0),
  realized_pnl numeric(24,8) not null default 0,
  unrealized_pnl numeric(24,8) not null default 0,
  worst_case_exposure numeric(24,8) not null default 0 check (worst_case_exposure >= 0),
  settlement_outcome text,
  opened_at timestamptz,
  closed_at timestamptz,
  settled_at timestamptz,
  notes text,
  source_assumptions jsonb not null default '{}'::jsonb check (jsonb_typeof(source_assumptions) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (portfolio_id, user_id) references public.paper_portfolios(id, user_id) on delete cascade,
  unique (id, user_id),
  unique (id, portfolio_id, user_id),
  check (closed_at is null or opened_at is null or closed_at >= opened_at),
  check (settled_at is null or opened_at is null or settled_at >= opened_at),
  check (settled_at is null or status = 'settled')
);

create table public.paper_position_legs (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  position_id uuid not null,
  leg_number smallint not null check (leg_number > 0),
  provider_id uuid not null references public.providers(id) on delete restrict,
  market_id uuid not null references public.markets(id) on delete restrict,
  outcome_id uuid not null references public.outcomes(id) on delete restrict,
  action text not null check (action in ('back', 'lay', 'buy_yes', 'buy_no', 'sell_yes', 'sell_no')),
  planned_price numeric(24,10) not null check (planned_price > 0),
  planned_quantity numeric(24,8) not null check (planned_quantity > 0),
  planned_stake numeric(24,8) not null check (planned_stake >= 0),
  filled_quantity numeric(24,8) not null default 0 check (filled_quantity >= 0),
  average_fill_price numeric(24,10) check (average_fill_price is null or average_fill_price > 0),
  simulated_fee numeric(24,8) not null default 0 check (simulated_fee >= 0),
  status text not null default 'pending' check (status in ('pending', 'partially_filled', 'filled', 'cancelled', 'settled', 'void')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (position_id, user_id) references public.paper_positions(id, user_id) on delete cascade,
  unique (id, user_id),
  unique (position_id, leg_number),
  foreign key (market_id, provider_id)
    references public.markets(id, provider_id) on delete restrict,
  foreign key (outcome_id, market_id)
    references public.outcomes(id, market_id) on delete restrict,
  check (filled_quantity <= planned_quantity)
);

create table public.paper_fills (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  leg_id uuid not null,
  fill_key text not null,
  quantity numeric(24,8) not null check (quantity > 0),
  price numeric(24,10) not null check (price > 0),
  notional numeric(24,8) not null check (notional >= 0),
  fee numeric(24,8) not null default 0 check (fee >= 0),
  slippage numeric(24,10) not null default 0,
  execution_delay_ms integer not null default 0 check (execution_delay_ms >= 0),
  quote_id uuid references public.quotes(id) on delete set null,
  filled_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (leg_id, user_id) references public.paper_position_legs(id, user_id) on delete cascade,
  unique (user_id, fill_key)
);

create table public.paper_settlements (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  position_id uuid not null,
  settlement_key text not null,
  outcome_key text not null,
  gross_payout numeric(24,8) not null check (gross_payout >= 0),
  refunds numeric(24,8) not null default 0 check (refunds >= 0),
  fees numeric(24,8) not null default 0 check (fees >= 0),
  net_pnl numeric(24,8) not null,
  result_source_url text,
  settlement_data jsonb not null default '{}'::jsonb check (jsonb_typeof(settlement_data) = 'object'),
  settled_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (position_id, user_id) references public.paper_positions(id, user_id) on delete cascade,
  unique (user_id, settlement_key)
);

create table public.paper_ledger_entries (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  portfolio_id uuid not null,
  position_id uuid,
  entry_group_id uuid not null,
  idempotency_key text not null,
  entry_type text not null check (entry_type in ('opening_balance', 'reserve', 'release', 'fill_cost', 'fee', 'payout', 'refund', 'realized_pnl', 'adjustment')),
  direction text not null check (direction in ('debit', 'credit')),
  amount numeric(24,8) not null check (amount >= 0),
  signed_amount numeric(24,8) generated always as (case when direction = 'credit' then amount else -amount end) stored,
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  memo text,
  occurred_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (portfolio_id, user_id) references public.paper_portfolios(id, user_id) on delete cascade,
  foreign key (position_id, portfolio_id, user_id)
    references public.paper_positions(id, portfolio_id, user_id) on delete cascade,
  unique (user_id, idempotency_key),
  check (entry_type <> 'adjustment' or memo is not null)
);

create table public.performance_snapshots (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  portfolio_id uuid not null,
  snapshot_at timestamptz not null,
  cash_balance numeric(24,8) not null,
  reserved_capital numeric(24,8) not null check (reserved_capital >= 0),
  portfolio_value numeric(24,8) not null,
  realized_pnl numeric(24,8) not null,
  unrealized_pnl numeric(24,8) not null,
  net_roi numeric(18,10),
  active_positions integer not null default 0 check (active_positions >= 0),
  maximum_drawdown numeric(18,10) check (maximum_drawdown is null or maximum_drawdown >= 0),
  dimensions jsonb not null default '{}'::jsonb check (jsonb_typeof(dimensions) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (portfolio_id, user_id) references public.paper_portfolios(id, user_id) on delete cascade,
  unique (portfolio_id, snapshot_at)
);

-- ---------- Research models, historical simulation, and operations ----------

create table public.model_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  model_name text not null,
  model_version text not null,
  sport_id uuid references public.sports(id) on delete set null,
  status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed', 'cancelled', 'insufficient_data')),
  methodology text not null,
  configuration jsonb not null default '{}'::jsonb check (jsonb_typeof(configuration) = 'object'),
  training_start_at timestamptz,
  training_end_at timestamptz,
  validation_end_at timestamptz,
  test_end_at timestamptz,
  information_cutoff_at timestamptz,
  sample_count integer check (sample_count is null or sample_count >= 0),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id),
  check (training_end_at is null or training_start_at is null or training_end_at > training_start_at),
  check (validation_end_at is null or training_end_at is null or validation_end_at > training_end_at),
  check (test_end_at is null or validation_end_at is null or test_end_at > validation_end_at),
  check (completed_at is null or started_at is null or completed_at >= started_at),
  check (status <> 'insufficient_data' or sample_count is not null)
);

create table public.backtest_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  model_run_id uuid,
  name text not null check (length(btrim(name)) between 1 and 120),
  status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed', 'cancelled', 'insufficient_data')),
  simulation_mode text not null check (simulation_mode in ('zero_latency', 'fixed_delay', 'randomized_delay')),
  simulation_start_at timestamptz not null,
  simulation_end_at timestamptz not null,
  fixed_delay_ms integer check (fixed_delay_ms is null or fixed_delay_ms >= 0),
  random_delay_parameters jsonb check (random_delay_parameters is null or jsonb_typeof(random_delay_parameters) = 'object'),
  slippage_parameters jsonb not null default '{}'::jsonb check (jsonb_typeof(slippage_parameters) = 'object'),
  partial_fill_enabled boolean not null default true,
  quote_expiration_seconds integer not null check (quote_expiration_seconds > 0),
  configuration jsonb not null default '{}'::jsonb check (jsonb_typeof(configuration) = 'object'),
  historical_depth_available boolean not null default false,
  assumptions jsonb not null default '{}'::jsonb check (jsonb_typeof(assumptions) = 'object'),
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (model_run_id, user_id) references public.model_runs(id, user_id) on delete restrict,
  unique (id, user_id),
  check (simulation_end_at > simulation_start_at),
  check (completed_at is null or started_at is null or completed_at >= started_at),
  check ((simulation_mode = 'fixed_delay' and fixed_delay_ms is not null) or simulation_mode <> 'fixed_delay'),
  check ((simulation_mode = 'randomized_delay' and random_delay_parameters is not null) or simulation_mode <> 'randomized_delay')
);

create table public.backtest_signals (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  backtest_run_id uuid not null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  signal_key text not null,
  signal_at timestamptz not null,
  latest_source_observed_at timestamptz not null,
  attempted_at timestamptz,
  outcome text not null check (outcome in ('detected', 'captured', 'expired', 'partial_fill', 'one_sided', 'rejected')),
  gross_return numeric(24,8) not null default 0,
  net_return numeric(24,8) not null default 0,
  worst_case_exposure numeric(24,8) not null default 0 check (worst_case_exposure >= 0),
  quote_ids uuid[] not null default '{}',
  details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (backtest_run_id, user_id) references public.backtest_runs(id, user_id) on delete cascade,
  unique (backtest_run_id, signal_key),
  check (latest_source_observed_at <= signal_at),
  check (attempted_at is null or attempted_at >= signal_at)
);

create table public.backtest_results (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  backtest_run_id uuid not null,
  result_scope text not null check (result_scope in ('overall', 'sport', 'league', 'provider', 'market_type', 'strategy')),
  scope_key text not null default 'all',
  total_signals integer not null check (total_signals >= 0),
  valid_opportunities integer not null check (valid_opportunities >= 0),
  captured_opportunities integer not null check (captured_opportunities >= 0),
  failed_captures integer not null check (failed_captures >= 0),
  gross_return numeric(24,8) not null,
  net_return numeric(24,8) not null,
  worst_case_exposure numeric(24,8) not null check (worst_case_exposure >= 0),
  maximum_drawdown numeric(18,10) not null check (maximum_drawdown >= 0),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (backtest_run_id, user_id) references public.backtest_runs(id, user_id) on delete cascade,
  unique (backtest_run_id, result_scope, scope_key),
  check (valid_opportunities <= total_signals),
  check (captured_opportunities + failed_captures <= valid_opportunities)
);

create table public.ingestion_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete cascade,
  run_key text not null,
  status text not null default 'running' check (status in ('running', 'completed', 'partial', 'failed', 'rate_limited')),
  endpoint_class text not null,
  request_count integer not null default 0 check (request_count >= 0),
  records_received integer not null default 0 check (records_received >= 0),
  records_created integer not null default 0 check (records_created >= 0),
  records_updated integer not null default 0 check (records_updated >= 0),
  records_rejected integer not null default 0 check (records_rejected >= 0),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  diagnostics jsonb not null default '{}'::jsonb check (jsonb_typeof(diagnostics) = 'object'),
  created_at timestamptz not null default now(),
  unique (provider_id, run_key),
  check (completed_at is null or completed_at >= started_at)
);

create table public.raw_ingestion_records (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid not null references public.providers(id) on delete cascade,
  ingestion_run_id uuid references public.ingestion_runs(id) on delete set null,
  record_type text not null check (record_type in ('event', 'market', 'quote', 'orderbook', 'settlement', 'fee_schedule')),
  external_id text,
  idempotency_key text not null,
  payload jsonb not null,
  payload_hash text not null,
  observed_at timestamptz not null,
  received_at timestamptz not null default now(),
  validation_status text not null default 'pending' check (validation_status in ('pending', 'valid', 'invalid', 'quarantined')),
  validation_errors jsonb not null default '[]'::jsonb check (jsonb_typeof(validation_errors) = 'array'),
  created_at timestamptz not null default now(),
  unique (provider_id, idempotency_key)
);

create table public.system_errors (
  id uuid primary key default extensions.gen_random_uuid(),
  provider_id uuid references public.providers(id) on delete set null,
  ingestion_run_id uuid references public.ingestion_runs(id) on delete set null,
  correlation_id text,
  component text not null,
  severity text not null check (severity in ('debug', 'info', 'warning', 'error', 'critical')),
  error_code text,
  summary text not null,
  details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
  occurred_at timestamptz not null default now(),
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  check (resolved_at is null or resolved_at >= occurred_at)
);

-- ---------- Query indexes ----------

create index leagues_sport_idx on public.leagues (sport_id) where active;
create index participants_sport_name_idx on public.participants (sport_id, normalized_name);
create index participant_aliases_lookup_idx on public.participant_aliases (provider_id, normalized_alias);
create index events_schedule_idx on public.events (scheduled_start desc);
create index events_league_schedule_idx on public.events (league_id, scheduled_start desc);
create index events_status_schedule_idx on public.events (status, scheduled_start desc);
create index provider_events_canonical_idx on public.provider_events (canonical_event_id);
create index provider_events_last_seen_idx on public.provider_events (provider_id, last_seen_at desc);
create index markets_event_idx on public.markets (event_id, market_type, period);
create index markets_provider_event_idx on public.markets (provider_event_id);
create index markets_provider_status_idx on public.markets (provider_id, status, last_seen_at desc);
create index markets_open_close_idx on public.markets (closes_at) where status = 'open';
create index market_rules_active_idx on public.market_rules (market_id, effective_from desc) where effective_to is null;
create index outcomes_market_status_idx on public.outcomes (market_id, status);
create index fee_schedules_effective_idx on public.fee_schedules (provider_id, effective_from desc);
create index quotes_outcome_time_idx on public.quotes (outcome_id, observed_at desc);
create index quotes_market_time_idx on public.quotes (market_id, observed_at desc);
create index quotes_provider_time_idx on public.quotes (provider_id, observed_at desc);
create index quotes_stale_idx on public.quotes (stale_at) where stale_at is not null;
create index quotes_observed_brin_idx on public.quotes using brin (observed_at);
create index orderbook_snapshots_market_time_idx on public.orderbook_snapshots (market_id, observed_at desc);
create index orderbook_snapshots_outcome_time_idx on public.orderbook_snapshots (outcome_id, observed_at desc);
create index orderbook_snapshots_observed_brin_idx on public.orderbook_snapshots using brin (observed_at);
create index orderbook_levels_snapshot_side_idx on public.orderbook_levels (snapshot_id, side, level_number);
create index market_matches_classification_idx on public.market_matches (classification, updated_at desc);
create index market_matches_left_idx on public.market_matches (left_market_id);
create index market_matches_right_idx on public.market_matches (right_market_id);
create index match_reviews_match_time_idx on public.match_reviews (market_match_id, reviewed_at desc);
create index opportunities_active_score_idx on public.opportunities (research_score desc, detected_at desc) where status = 'active' and match_status = 'verified';
create index opportunities_event_time_idx on public.opportunities (event_id, detected_at desc);
create index opportunities_strategy_time_idx on public.opportunities (strategy_type, detected_at desc);
create index opportunities_expires_idx on public.opportunities (expires_at) where status = 'active';
create index opportunity_legs_opportunity_idx on public.opportunity_legs (opportunity_id);
create index opportunity_legs_quote_idx on public.opportunity_legs (quote_id);
create index opportunity_outcomes_opportunity_idx on public.opportunity_outcomes (opportunity_id);
create index opportunity_snapshots_opportunity_time_idx on public.opportunity_snapshots (opportunity_id, observed_at desc);
create index opportunity_snapshots_observed_brin_idx on public.opportunity_snapshots using brin (observed_at);
create index user_saved_filters_user_idx on public.user_saved_filters (user_id, page_key);
create index user_watchlist_items_user_idx on public.user_watchlist_items (user_id, watchlist_id);
create index user_alerts_user_enabled_idx on public.user_alerts (user_id, enabled) where enabled;
create index user_alert_events_user_time_idx on public.user_alert_events (user_id, triggered_at desc);
create index user_alert_events_unread_idx on public.user_alert_events (user_id, triggered_at desc) where read_at is null and dismissed_at is null;
create index user_saved_opportunities_user_time_idx on public.user_saved_opportunities (user_id, saved_at desc);
create index user_research_notes_user_time_idx on public.user_research_notes (user_id, updated_at desc);
create index paper_portfolios_user_status_idx on public.paper_portfolios (user_id, status);
create index paper_positions_portfolio_status_idx on public.paper_positions (portfolio_id, status, created_at desc);
create index paper_positions_user_time_idx on public.paper_positions (user_id, created_at desc);
create unique index paper_positions_external_reference_uidx on public.paper_positions (user_id, external_reference)
  where external_reference is not null;
create index paper_position_legs_position_idx on public.paper_position_legs (position_id, leg_number);
create index paper_position_legs_market_idx on public.paper_position_legs (market_id);
create index paper_fills_leg_time_idx on public.paper_fills (leg_id, filled_at);
create index paper_fills_user_time_idx on public.paper_fills (user_id, filled_at desc);
create index paper_settlements_position_idx on public.paper_settlements (position_id, settled_at desc);
create index paper_ledger_entries_portfolio_time_idx on public.paper_ledger_entries (portfolio_id, occurred_at, id);
create index paper_ledger_entries_position_idx on public.paper_ledger_entries (position_id) where position_id is not null;
create index paper_ledger_entries_user_time_idx on public.paper_ledger_entries (user_id, occurred_at desc);
create index performance_snapshots_user_time_idx on public.performance_snapshots (user_id, snapshot_at desc);
create index model_runs_user_time_idx on public.model_runs (user_id, created_at desc);
create index backtest_runs_user_time_idx on public.backtest_runs (user_id, created_at desc);
create index backtest_signals_run_time_idx on public.backtest_signals (backtest_run_id, signal_at);
create index backtest_results_run_idx on public.backtest_results (backtest_run_id);
create index ingestion_runs_provider_time_idx on public.ingestion_runs (provider_id, started_at desc);
create index raw_ingestion_records_run_idx on public.raw_ingestion_records (ingestion_run_id);
create index raw_ingestion_records_provider_time_idx on public.raw_ingestion_records (provider_id, observed_at desc);
create index raw_ingestion_records_pending_idx on public.raw_ingestion_records (received_at) where validation_status = 'pending';
create index raw_ingestion_records_observed_brin_idx on public.raw_ingestion_records using brin (observed_at);
create index system_errors_component_time_idx on public.system_errors (component, occurred_at desc);
create index system_errors_unresolved_idx on public.system_errors (severity, occurred_at desc) where resolved_at is null;

-- ---------- Mutable-row timestamps and safe Auth provisioning ----------

create trigger providers_set_updated_at before update on public.providers
  for each row execute function private.set_updated_at();
create trigger provider_connections_set_updated_at before update on public.provider_connections
  for each row execute function private.set_updated_at();
create trigger sports_set_updated_at before update on public.sports
  for each row execute function private.set_updated_at();
create trigger leagues_set_updated_at before update on public.leagues
  for each row execute function private.set_updated_at();
create trigger participants_set_updated_at before update on public.participants
  for each row execute function private.set_updated_at();
create trigger events_set_updated_at before update on public.events
  for each row execute function private.set_updated_at();
create trigger provider_events_set_updated_at before update on public.provider_events
  for each row execute function private.set_updated_at();
create trigger markets_set_updated_at before update on public.markets
  for each row execute function private.set_updated_at();
create trigger outcomes_set_updated_at before update on public.outcomes
  for each row execute function private.set_updated_at();
create trigger market_matches_set_updated_at before update on public.market_matches
  for each row execute function private.set_updated_at();
create trigger opportunities_set_updated_at before update on public.opportunities
  for each row execute function private.set_updated_at();
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function private.set_updated_at();
create trigger user_settings_set_updated_at before update on public.user_settings
  for each row execute function private.set_updated_at();
create trigger user_saved_filters_set_updated_at before update on public.user_saved_filters
  for each row execute function private.set_updated_at();
create trigger user_watchlists_set_updated_at before update on public.user_watchlists
  for each row execute function private.set_updated_at();
create trigger user_alerts_set_updated_at before update on public.user_alerts
  for each row execute function private.set_updated_at();
create trigger user_research_notes_set_updated_at before update on public.user_research_notes
  for each row execute function private.set_updated_at();
create trigger paper_portfolios_set_updated_at before update on public.paper_portfolios
  for each row execute function private.set_updated_at();
create trigger paper_positions_set_updated_at before update on public.paper_positions
  for each row execute function private.set_updated_at();
create trigger paper_position_legs_set_updated_at before update on public.paper_position_legs
  for each row execute function private.set_updated_at();
create trigger model_runs_set_updated_at before update on public.model_runs
  for each row execute function private.set_updated_at();
create trigger backtest_runs_set_updated_at before update on public.backtest_runs
  for each row execute function private.set_updated_at();

create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  requested_name text;
begin
  requested_name := nullif(btrim(new.raw_user_meta_data ->> 'display_name'), '');

  insert into public.profiles (id, display_name, avatar_url)
  values (
    new.id,
    case when requested_name is null then null else left(requested_name, 80) end,
    case
      when (new.raw_user_meta_data ->> 'avatar_url') ~ '^https://'
        then left(new.raw_user_meta_data ->> 'avatar_url', 2048)
      else null
    end
  )
  on conflict (id) do nothing;

  insert into public.user_settings (user_id)
  values (new.id)
  on conflict (user_id) do nothing;

  insert into public.paper_portfolios (user_id, name, starting_balance, is_default)
  values (new.id, 'Paper Portfolio', 10000, true)
  on conflict (user_id, name) do nothing;

  return new;
end;
$$;

revoke all on function private.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

-- ---------- Security-invoker API views ----------

create view public.latest_quotes
with (security_invoker = true)
as
select distinct on (q.outcome_id)
  q.id,
  q.provider_id,
  q.market_id,
  q.outcome_id,
  q.decimal_odds,
  q.probability_price,
  q.best_bid,
  q.best_ask,
  q.bid_size,
  q.ask_size,
  q.available_size,
  q.currency,
  q.feed_class,
  q.observed_at,
  q.received_at,
  q.stale_at,
  (q.stale_at is not null and q.stale_at <= now()) as is_stale,
  q.is_in_play,
  q.is_demo
from public.quotes q
order by q.outcome_id, q.observed_at desc, q.received_at desc, q.id desc;

create view public.active_opportunity_feed
with (security_invoker = true)
as
select
  o.id,
  o.opportunity_key,
  o.event_id,
  o.strategy_type,
  o.total_allocation,
  o.currency,
  o.gross_profit,
  o.total_fees,
  o.worst_case_net_profit,
  o.best_case_net_profit,
  o.gross_roi,
  o.net_roi,
  o.maximum_depth,
  o.conservative_net_profit,
  o.execution_confidence,
  o.research_score,
  o.score_breakdown,
  o.detected_at,
  o.last_valid_at,
  o.expires_at,
  o.is_demo,
  (o.expires_at is not null and o.expires_at <= now()) as has_expired
from public.opportunities o
where o.status = 'active'
  and o.match_status = 'verified';

create view public.provider_health
with (security_invoker = true)
as
select
  p.id as provider_id,
  p.slug,
  p.display_name,
  p.provider_kind,
  p.feed_type,
  p.availability_status,
  p.requires_api_key,
  p.refresh_interval_seconds,
  p.data_delay_seconds,
  p.data_delay_known,
  p.is_demo,
  c.status as connection_status,
  c.last_attempt_at,
  c.last_success_at,
  c.last_error_at,
  c.last_error_code,
  c.rate_limit_state,
  c.quota_limit,
  c.quota_remaining,
  c.quota_resets_at,
  c.active_instruments,
  c.consecutive_failures
from public.providers p
left join public.provider_connections c on c.provider_id = p.id;

create view public.paper_portfolio_balances
with (security_invoker = true)
as
select
  p.id as portfolio_id,
  p.user_id,
  p.name,
  p.currency,
  p.starting_balance,
  coalesce(
    sum(l.signed_amount) filter (where l.entry_type = 'opening_balance'),
    p.starting_balance
  ) + coalesce(
    sum(l.signed_amount) filter (where l.entry_type <> 'opening_balance'),
    0
  ) as cash_balance,
  coalesce(sum(l.amount) filter (where l.entry_type = 'reserve' and l.direction = 'debit'), 0)
    - coalesce(sum(l.amount) filter (where l.entry_type = 'release' and l.direction = 'credit'), 0)
    as reserved_capital,
  max(l.occurred_at) as last_ledger_activity_at
from public.paper_portfolios p
left join public.paper_ledger_entries l on l.portfolio_id = p.id and l.user_id = p.user_id
group by p.id, p.user_id, p.name, p.currency, p.starting_balance;

-- ---------- Row-level security ----------

alter table public.providers enable row level security;
alter table public.provider_connections enable row level security;
alter table public.sports enable row level security;
alter table public.leagues enable row level security;
alter table public.participants enable row level security;
alter table public.participant_aliases enable row level security;
alter table public.events enable row level security;
alter table public.provider_events enable row level security;
alter table public.markets enable row level security;
alter table public.market_rules enable row level security;
alter table public.outcomes enable row level security;
alter table public.fee_schedules enable row level security;
alter table public.quotes enable row level security;
alter table public.orderbook_snapshots enable row level security;
alter table public.orderbook_levels enable row level security;
alter table public.market_matches enable row level security;
alter table public.match_reviews enable row level security;
alter table public.opportunities enable row level security;
alter table public.opportunity_legs enable row level security;
alter table public.opportunity_outcomes enable row level security;
alter table public.opportunity_snapshots enable row level security;
alter table public.profiles enable row level security;
alter table public.user_settings enable row level security;
alter table public.user_saved_filters enable row level security;
alter table public.user_watchlists enable row level security;
alter table public.user_watchlist_items enable row level security;
alter table public.user_alerts enable row level security;
alter table public.user_alert_events enable row level security;
alter table public.user_saved_opportunities enable row level security;
alter table public.user_research_notes enable row level security;
alter table public.paper_portfolios enable row level security;
alter table public.paper_positions enable row level security;
alter table public.paper_position_legs enable row level security;
alter table public.paper_fills enable row level security;
alter table public.paper_settlements enable row level security;
alter table public.paper_ledger_entries enable row level security;
alter table public.performance_snapshots enable row level security;
alter table public.model_runs enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.backtest_signals enable row level security;
alter table public.backtest_results enable row level security;
alter table public.ingestion_runs enable row level security;
alter table public.raw_ingestion_records enable row level security;
alter table public.system_errors enable row level security;

-- Shared normalized data is readable but never writable through client roles.
create policy providers_public_read on public.providers for select to anon, authenticated using (true);
create policy provider_connections_authenticated_read on public.provider_connections for select to authenticated using (true);
create policy sports_public_read on public.sports for select to anon, authenticated using (true);
create policy leagues_public_read on public.leagues for select to anon, authenticated using (true);
create policy participants_public_read on public.participants for select to anon, authenticated using (true);
create policy participant_aliases_public_read on public.participant_aliases for select to anon, authenticated using (true);
create policy events_public_read on public.events for select to anon, authenticated using (true);
create policy provider_events_public_read on public.provider_events for select to anon, authenticated using (true);
create policy markets_public_read on public.markets for select to anon, authenticated using (true);
create policy market_rules_public_read on public.market_rules for select to anon, authenticated using (true);
create policy outcomes_public_read on public.outcomes for select to anon, authenticated using (true);
create policy fee_schedules_public_read on public.fee_schedules for select to anon, authenticated using (true);
create policy quotes_public_read on public.quotes for select to anon, authenticated using (true);
create policy orderbook_snapshots_public_read on public.orderbook_snapshots for select to anon, authenticated using (true);
create policy orderbook_levels_public_read on public.orderbook_levels for select to anon, authenticated using (true);
create policy market_matches_public_read on public.market_matches for select to anon, authenticated using (true);
create policy opportunities_public_read on public.opportunities for select to anon, authenticated using (true);
create policy opportunity_legs_public_read on public.opportunity_legs for select to anon, authenticated using (true);
create policy opportunity_outcomes_public_read on public.opportunity_outcomes for select to anon, authenticated using (true);
create policy opportunity_snapshots_public_read on public.opportunity_snapshots for select to anon, authenticated using (true);

-- A profile row is provisioned by the Auth trigger. Users can read and update
-- only their row; profile deletion is handled by deleting the Auth account.
create policy profiles_owner_select on public.profiles for select to authenticated
  using ((select auth.uid()) = id);
create policy profiles_owner_update on public.profiles for update to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

create policy user_settings_owner_all on public.user_settings for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_saved_filters_owner_all on public.user_saved_filters for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_watchlists_owner_all on public.user_watchlists for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_watchlist_items_owner_all on public.user_watchlist_items for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_alerts_owner_all on public.user_alerts for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_alert_events_owner_select on public.user_alert_events for select to authenticated
  using ((select auth.uid()) = user_id);
create policy user_alert_events_owner_update on public.user_alert_events for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_saved_opportunities_owner_all on public.user_saved_opportunities for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy user_research_notes_owner_all on public.user_research_notes for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

-- Paper records are created and financially mutated only by the authenticated
-- application backend. Clients may read their own records and edit narrowly
-- scoped presentation fields; fills, settlements, and ledger entries remain
-- append-only server records. Manual corrections are validated by the backend
-- and appended as adjustment ledger entries rather than rewriting history.
create policy paper_portfolios_owner_select on public.paper_portfolios for select to authenticated
  using ((select auth.uid()) = user_id);
create policy paper_portfolios_owner_update on public.paper_portfolios for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy paper_positions_owner_select on public.paper_positions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy paper_positions_owner_update on public.paper_positions for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy paper_position_legs_owner_select on public.paper_position_legs for select to authenticated
  using ((select auth.uid()) = user_id);
create policy paper_fills_owner_select on public.paper_fills for select to authenticated
  using ((select auth.uid()) = user_id);
create policy paper_settlements_owner_select on public.paper_settlements for select to authenticated
  using ((select auth.uid()) = user_id);
create policy paper_ledger_entries_owner_select on public.paper_ledger_entries for select to authenticated
  using ((select auth.uid()) = user_id);
create policy performance_snapshots_owner_select on public.performance_snapshots for select to authenticated
  using ((select auth.uid()) = user_id);

create policy model_runs_owner_all on public.model_runs for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy backtest_runs_owner_all on public.backtest_runs for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy backtest_signals_owner_select on public.backtest_signals for select to authenticated
  using ((select auth.uid()) = user_id);
create policy backtest_results_owner_select on public.backtest_results for select to authenticated
  using ((select auth.uid()) = user_id);

-- No client policy is intentionally defined for match_reviews, ingestion_runs,
-- raw_ingestion_records, or system_errors. RLS therefore denies client access;
-- trusted backend workers use the server-only service role.

-- ---------- Explicit Data API grants ----------

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
grant usage on schema public to anon, authenticated;
grant usage on schema public to service_role;
grant all privileges on all tables in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;

grant select on table
  public.providers,
  public.sports,
  public.leagues,
  public.participants,
  public.participant_aliases,
  public.events,
  public.provider_events,
  public.markets,
  public.market_rules,
  public.outcomes,
  public.fee_schedules,
  public.quotes,
  public.orderbook_snapshots,
  public.orderbook_levels,
  public.market_matches,
  public.opportunities,
  public.opportunity_legs,
  public.opportunity_outcomes,
  public.opportunity_snapshots,
  public.latest_quotes,
  public.active_opportunity_feed
to anon, authenticated;

grant select (
  id, provider_id, status, last_attempt_at, last_success_at, last_error_at,
  last_error_code, rate_limit_state, quota_limit, quota_remaining,
  quota_resets_at, active_instruments, consecutive_failures, created_at, updated_at
) on public.provider_connections to authenticated;
grant select on public.provider_health to authenticated;

grant select on public.profiles to authenticated;
grant update (display_name, avatar_url, timezone, base_currency, onboarding_completed)
  on public.profiles to authenticated;
grant select, insert, update, delete on
  public.user_settings,
  public.user_saved_filters,
  public.user_watchlists,
  public.user_watchlist_items,
  public.user_alerts,
  public.user_saved_opportunities,
  public.user_research_notes,
  public.model_runs,
  public.backtest_runs
to authenticated;
grant select on public.user_alert_events to authenticated;
grant update (read_at, dismissed_at) on public.user_alert_events to authenticated;
grant select on
  public.paper_portfolios,
  public.paper_positions,
  public.paper_position_legs,
  public.paper_fills,
  public.paper_settlements,
  public.paper_ledger_entries
to authenticated;
grant update (name, status, is_default) on public.paper_portfolios to authenticated;
grant update (title, notes) on public.paper_positions to authenticated;
grant select on
  public.performance_snapshots,
  public.backtest_signals,
  public.backtest_results,
  public.paper_portfolio_balances
to authenticated;

commit;




