-- EDGEOS database security and integrity assertions.
-- Run against a migrated local Supabase database:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/schema_assertions.sql

begin;

create or replace function pg_temp.assert_true(condition boolean, message text)
returns void
language plpgsql
as $$
begin
  if condition is distinct from true then
    raise exception 'ASSERTION FAILED: %', message;
  end if;
end;
$$;

-- The migration deliberately revokes PostgreSQL's default PUBLIC function
-- execution grant. This test-only helper must remain callable after SET ROLE
-- so the RLS assertions can report their results.
grant execute on function pg_temp.assert_true(boolean, text) to anon, authenticated;

-- Every ordinary/partitioned table in the exposed public schema must use RLS.
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and not c.relrowsecurity
  ),
  'every public table has RLS enabled'
);

-- All exposed views must execute with caller privileges rather than owner
-- privileges, otherwise they could silently bypass underlying RLS.
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'v'
      and not coalesce(c.reloptions, '{}'::text[]) @> array['security_invoker=true']
  ),
  'every public view is security_invoker'
);

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.profiles', 'SELECT'),
  'anonymous clients cannot read profiles'
);
select pg_temp.assert_true(
  not has_any_column_privilege('anon', 'public.provider_connections', 'SELECT'),
  'anonymous clients cannot read provider connection diagnostics'
);
select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.provider_health', 'SELECT'),
  'anonymous clients cannot read the provider health view'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.system_errors', 'SELECT'),
  'authenticated clients cannot read operational errors'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.raw_ingestion_records', 'SELECT'),
  'authenticated clients cannot read raw provider payloads'
);
select pg_temp.assert_true(
  (select count(*) = 4
   from public.providers
   where slug in ('the_odds_api', 'kalshi', 'polymarket_international', 'polymarket_us')
     and read_only and not is_demo)
  and (select count(*) = 1
       from public.providers
       where slug = 'edgeos_demo' and is_demo and feed_type = 'demo'),
  'official provider reference rows and the explicit demo provider are provisioned'
);
select pg_temp.assert_true(
  has_table_privilege('authenticated', 'public.user_provider_connections', 'SELECT')
  and not has_table_privilege('authenticated', 'public.user_provider_connections', 'INSERT')
  and not has_table_privilege('authenticated', 'public.user_provider_connections', 'UPDATE')
  and not has_table_privilege('authenticated', 'public.user_provider_connections', 'DELETE'),
  'provider connection metadata is owner-readable and backend-written'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.ingestion_worker_leases', 'SELECT')
  and not has_table_privilege('anon', 'public.ingestion_worker_leases', 'SELECT'),
  'worker leases are not exposed through the Data API'
);
select pg_temp.assert_true(
  not has_schema_privilege('anon', 'private', 'USAGE')
  and not has_schema_privilege('authenticated', 'private', 'USAGE')
  and has_schema_privilege('service_role', 'private', 'USAGE')
  and has_table_privilege('service_role', 'private.provider_credentials', 'SELECT,INSERT,UPDATE,DELETE'),
  'encrypted credential envelopes are available only to the trusted backend role'
);
select pg_temp.assert_true(
  (select relrowsecurity and relforcerowsecurity
   from pg_class c
   join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'private' and c.relname = 'provider_credentials')
  and not exists (
    select 1 from pg_policies
    where schemaname = 'private' and tablename = 'provider_credentials'
  ),
  'private credential storage uses forced RLS with no client policy'
);
select pg_temp.assert_true(
  not exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and column_name in ('ciphertext', 'nonce')
      and table_name like '%provider%connection%'
  ),
  'ciphertext and nonce are absent from public provider connection records'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.paper_ledger_entries', 'UPDATE'),
  'paper ledger entries are immutable through the Data API'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.paper_ledger_entries', 'DELETE'),
  'paper ledger entries cannot be deleted through the Data API'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.paper_fills', 'INSERT')
  and not has_table_privilege('authenticated', 'public.paper_settlements', 'INSERT')
  and not has_table_privilege('authenticated', 'public.paper_ledger_entries', 'INSERT'),
  'financial history is written only by the backend'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.paper_portfolios', 'INSERT')
  and not has_table_privilege('authenticated', 'public.paper_positions', 'INSERT')
  and not has_table_privilege('authenticated', 'public.paper_position_legs', 'INSERT'),
  'paper-trading records are created only by the backend'
);
select pg_temp.assert_true(
  has_column_privilege('authenticated', 'public.profiles', 'display_name', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.profiles', 'id', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.profiles', 'created_at', 'UPDATE'),
  'profile updates are restricted to editable columns'
);
select pg_temp.assert_true(
  has_column_privilege('authenticated', 'public.user_alert_events', 'read_at', 'UPDATE')
  and has_column_privilege('authenticated', 'public.user_alert_events', 'dismissed_at', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.user_alert_events', 'body', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.user_alert_events', 'lifecycle_status', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.user_alert_events', 'withdrawn_at', 'UPDATE')
  and not has_table_privilege('authenticated', 'public.user_alert_events', 'DELETE'),
  'alert events allow acknowledgement fields only'
);
select pg_temp.assert_true(
  exists (
    select 1 from information_schema.columns
     where table_schema = 'public' and table_name = 'user_alert_events'
       and column_name = 'lifecycle_status' and is_nullable = 'NO'
  )
  and exists (
    select 1 from information_schema.columns
     where table_schema = 'public' and table_name = 'user_alert_events'
       and column_name = 'withdrawn_at'
  ),
  'alert events expose a durable worker-managed withdrawal lifecycle'
);
select pg_temp.assert_true(
  has_column_privilege('authenticated', 'public.paper_portfolios', 'name', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.paper_portfolios', 'starting_balance', 'UPDATE')
  and has_column_privilege('authenticated', 'public.paper_positions', 'notes', 'UPDATE')
  and not has_column_privilege('authenticated', 'public.paper_positions', 'realized_pnl', 'UPDATE')
  and not has_any_column_privilege('authenticated', 'public.paper_position_legs', 'UPDATE'),
  'paper updates cannot rewrite calculated financial fields'
);
select pg_temp.assert_true(
  not has_function_privilege('authenticated', 'private.handle_new_user()', 'EXECUTE'),
  'Auth provisioning function is not API-callable'
);
select pg_temp.assert_true(
  not has_function_privilege(
    'authenticated', 'private.invalidate_market_matches_for_rule_change()', 'EXECUTE'
  ),
  'settlement invalidation trigger function is not API-callable'
);
select pg_temp.assert_true(
  not has_schema_privilege('anon', 'public', 'CREATE')
  and not has_schema_privilege('authenticated', 'public', 'CREATE')
  and not has_schema_privilege('anon', 'private', 'USAGE')
  and not has_schema_privilege('authenticated', 'private', 'USAGE'),
  'API roles cannot create public objects or use the private schema'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.prosecdef
  ),
  'the exposed public schema has no security-definer functions'
);

-- A probe created after the migration must inherit the hardened function
-- defaults rather than PostgreSQL's usual EXECUTE grant to PUBLIC.
create function public.edgeos_default_privilege_probe()
returns boolean
language sql
as 'select true';

select pg_temp.assert_true(
  not has_function_privilege('anon', 'public.edgeos_default_privilege_probe()', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.edgeos_default_privilege_probe()', 'EXECUTE'),
  'future public functions are not executable by API roles by default'
);

-- Minimal shared-data graph used to exercise provenance constraints. The
-- transaction is rolled back, so none of these fixtures persist.
insert into public.providers (id, slug, display_name, provider_kind)
values
  ('20000000-0000-0000-0000-000000000001', 'assert-provider-a', 'Assertion Provider A', 'sportsbook'),
  ('20000000-0000-0000-0000-000000000002', 'assert-provider-b', 'Assertion Provider B', 'prediction_market');

insert into public.markets (
  id, provider_id, external_market_id, title, market_type, mutually_exclusive,
  collectively_exhaustive
)
values
  (
    '21000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'assert-market-a', 'Assertion Market A', 'moneyline', true, true
  ),
  (
    '21000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000002',
    'assert-market-b', 'Assertion Market B', 'binary_contract', true, true
  );

insert into public.outcomes (
  id, market_id, external_outcome_id, canonical_key, name, outcome_type,
  payout_per_unit
)
values
  (
    '22000000-0000-0000-0000-000000000001',
    '21000000-0000-0000-0000-000000000001',
    'assert-outcome-a', 'home', 'Home', 'home', 2
  ),
  (
    '22000000-0000-0000-0000-000000000002',
    '21000000-0000-0000-0000-000000000002',
    'assert-outcome-b', 'yes', 'Yes', 'yes', 1
  );

insert into public.quotes (
  id, provider_id, market_id, outcome_id, ingestion_key, decimal_odds,
  feed_class, observed_at
)
values
  (
    '23000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '21000000-0000-0000-0000-000000000001',
    '22000000-0000-0000-0000-000000000001',
    'assert-quote-a', 2.10, 'historical', now()
  ),
  (
    '23000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000002',
    '21000000-0000-0000-0000-000000000002',
    '22000000-0000-0000-0000-000000000002',
    'assert-quote-b', 2.05, 'historical', now()
  );

insert into public.opportunities (
  id, opportunity_key, strategy_type, match_status, total_allocation,
  gross_profit, total_fees, worst_case_net_profit, best_case_net_profit,
  conservative_net_profit, execution_confidence, research_score,
  score_breakdown, detected_at
)
values (
  '24000000-0000-0000-0000-000000000001', 'assert-opportunity',
  'sportsbook_prediction', 'verified', 100, 2, 0, 1, 2, 1, 0.9, 80,
  '{}'::jsonb, now()
);

do $$
begin
  begin
    insert into public.quotes (
      provider_id, market_id, outcome_id, ingestion_key, decimal_odds,
      feed_class, observed_at
    ) values (
      '20000000-0000-0000-0000-000000000001',
      '21000000-0000-0000-0000-000000000002',
      '22000000-0000-0000-0000-000000000002',
      'invalid-quote-provider', 2, 'historical', now()
    );
    raise exception 'ASSERTION FAILED: quote accepted a market from another provider';
  exception when foreign_key_violation then null;
  end;

  begin
    insert into public.quotes (
      provider_id, market_id, outcome_id, ingestion_key, decimal_odds,
      feed_class, observed_at
    ) values (
      '20000000-0000-0000-0000-000000000001',
      '21000000-0000-0000-0000-000000000001',
      '22000000-0000-0000-0000-000000000002',
      'invalid-quote-outcome', 2, 'historical', now()
    );
    raise exception 'ASSERTION FAILED: quote accepted an outcome from another market';
  exception when foreign_key_violation then null;
  end;

  begin
    insert into public.orderbook_snapshots (
      provider_id, market_id, outcome_id, ingestion_key, observed_at,
      feed_class
    ) values (
      '20000000-0000-0000-0000-000000000001',
      '21000000-0000-0000-0000-000000000001',
      '22000000-0000-0000-0000-000000000002',
      'invalid-book-outcome', now(), 'historical'
    );
    raise exception 'ASSERTION FAILED: order book accepted an outcome from another market';
  exception when foreign_key_violation then null;
  end;

  begin
    insert into public.opportunity_legs (
      opportunity_id, leg_number, provider_id, market_id, outcome_id,
      quote_id, action, price, quantity, allocation, quote_observed_at
    ) values (
      '24000000-0000-0000-0000-000000000001', 1,
      '20000000-0000-0000-0000-000000000001',
      '21000000-0000-0000-0000-000000000001',
      '22000000-0000-0000-0000-000000000001',
      '23000000-0000-0000-0000-000000000002',
      'back', 2.10, 10, 21, now()
    );
    raise exception 'ASSERTION FAILED: opportunity leg accepted an unrelated quote';
  exception when foreign_key_violation then null;
  end;
end;
$$;

insert into public.market_rules (
  market_id, rule_version, rules_hash, effective_from, effective_to
)
values (
  '21000000-0000-0000-0000-000000000001', 1, 'assert-rules-v1',
  '2026-01-01 00:00:00+00', '2026-03-01 00:00:00+00'
);

insert into public.fee_schedules (
  provider_id, name, version, effective_from, effective_to,
  calculation_method
)
values (
  '20000000-0000-0000-0000-000000000001', 'assert-standard', 'v1',
  '2026-01-01 00:00:00+00', '2026-03-01 00:00:00+00', 'none'
);

do $$
begin
  begin
    insert into public.market_rules (
      market_id, rule_version, rules_hash, effective_from, effective_to
    ) values (
      '21000000-0000-0000-0000-000000000001', 2, 'assert-rules-v2',
      '2026-02-01 00:00:00+00', '2026-04-01 00:00:00+00'
    );
    raise exception 'ASSERTION FAILED: overlapping market rules were accepted';
  exception when exclusion_violation then null;
  end;

  begin
    insert into public.fee_schedules (
      provider_id, name, version, effective_from, effective_to,
      calculation_method
    ) values (
      '20000000-0000-0000-0000-000000000001', 'assert-standard', 'v2',
      '2026-02-01 00:00:00+00', '2026-04-01 00:00:00+00', 'none'
    );
    raise exception 'ASSERTION FAILED: overlapping fee schedules were accepted';
  exception when exclusion_violation then null;
  end;
end;
$$;

-- A material settlement-rule change invalidates a prior verified decision,
-- appends a versioned system review, and removes the linked opportunity from
-- the active feed rather than silently trusting stale evidence.
insert into public.market_matches (
  id, left_market_id, right_market_id, classification, candidate_score,
  settlement_confidence, equivalence_checks, explanation, engine_version,
  verified_at, left_rule_fingerprint, right_rule_fingerprint,
  decision_fingerprint, decision_source_evidence
) values (
  '25000000-0000-0000-0000-000000000001',
  '21000000-0000-0000-0000-000000000001',
  '21000000-0000-0000-0000-000000000002',
  'verified', 0.95, 1, '{"period":"compatible"}'::jsonb,
  'Explicit assertion match', 'assert-engine-v1', now(),
  'assert-left-rule-v1', 'assert-right-rule-v1', 'assert-decision-v1',
  '{"sources":["https://example.invalid/rules"]}'::jsonb
);

update public.opportunities
set market_match_id = '25000000-0000-0000-0000-000000000001'
where id = '24000000-0000-0000-0000-000000000001';

insert into public.market_rules (
  market_id, rule_version, rules_hash, effective_from, effective_to
) values (
  '21000000-0000-0000-0000-000000000001', 2, 'assert-rules-v2-current',
  '2026-03-01 00:00:00+00', '2026-05-01 00:00:00+00'
);

select pg_temp.assert_true(
  (select classification = 'review_required'
          and settlement_confidence = 0
          and verified_at is null
          and decision_version = 2
          and invalidated_at is not null
          and invalidation_reason = 'material_market_rule_change'
   from public.market_matches
   where id = '25000000-0000-0000-0000-000000000001')
  and (select status = 'invalidated'
       from public.opportunities
       where id = '24000000-0000-0000-0000-000000000001')
  and (select count(*) = 1
       from public.match_reviews
       where market_match_id = '25000000-0000-0000-0000-000000000001'
         and decision_version = 2
         and reviewer_kind = 'system'),
  'material rule changes invalidate matches and append versioned review evidence'
);

-- Insert two disposable Auth principals. The Auth trigger provisions profiles,
-- settings, and default paper portfolios atomically.
insert into auth.users (
  id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
)
values
  (
    '10000000-0000-0000-0000-000000000001', 'authenticated', 'authenticated',
    'edgeos-db-test-1@example.invalid', '', now(), '{}'::jsonb,
    '{"display_name":"DB Test One"}'::jsonb, now(), now()
  ),
  (
    '10000000-0000-0000-0000-000000000002', 'authenticated', 'authenticated',
    'edgeos-db-test-2@example.invalid', '', now(), '{}'::jsonb,
    '{"display_name":"DB Test Two"}'::jsonb, now(), now()
  );

select pg_temp.assert_true(
  (select count(*) = 2 from public.profiles where id in (
    '10000000-0000-0000-0000-000000000001'::uuid,
    '10000000-0000-0000-0000-000000000002'::uuid
  )),
  'Auth trigger creates profiles'
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.paper_portfolios where user_id in (
    '10000000-0000-0000-0000-000000000001'::uuid,
    '10000000-0000-0000-0000-000000000002'::uuid
  ) and is_default),
  'Auth trigger creates default paper portfolios'
);

insert into public.user_provider_connections (
  id, user_id, provider_id, status, selected_sports, selected_regions,
  selected_markets, credential_mask, capabilities
)
values
  (
    '38000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001', 'connected',
    '["basketball_nba"]'::jsonb, '["us"]'::jsonb, '["h2h"]'::jsonb,
    '{"api_key":"••••abcd"}'::jsonb, '{"events":true}'::jsonb
  ),
  (
    '38000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000002', 'connected',
    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
    '{"api_key":"••••wxyz"}'::jsonb, '{"orderbook":true}'::jsonb
  );

insert into private.provider_credentials (
  connection_id, user_id, key_version, nonce, ciphertext,
  credential_fields, credential_fingerprint
)
values
  (
    '38000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001', 'test-v1',
    decode(repeat('01', 12), 'hex'), decode(repeat('11', 32), 'hex'),
    array['api_key'], repeat('a', 64)
  ),
  (
    '38000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002', 'test-v1',
    decode(repeat('02', 12), 'hex'), decode(repeat('22', 32), 'hex'),
    array['api_key'], repeat('b', 64)
  );

do $$
begin
  begin
    insert into public.user_provider_connections (user_id, provider_id)
    values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001'
    );
    raise exception 'ASSERTION FAILED: duplicate user/provider connection was accepted';
  exception when unique_violation then null;
  end;

  begin
    insert into private.provider_credentials (
      connection_id, user_id, key_version, nonce, ciphertext, credential_fields
    ) values (
      '38000000-0000-0000-0000-000000000001',
      '10000000-0000-0000-0000-000000000002', 'test-v2',
      decode(repeat('03', 12), 'hex'), decode(repeat('33', 32), 'hex'),
      array['api_key']
    );
    raise exception 'ASSERTION FAILED: credential envelope crossed connection ownership';
  exception when unique_violation or foreign_key_violation then null;
  end;
end;
$$;

-- Backend-authored private fixtures prove that every nested RLS path contains
-- records for both users; a passing isolation assertion is therefore not a
-- vacuous empty-set result.
insert into public.paper_portfolios (
  id, user_id, name, starting_balance
)
values (
  '30000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  'Secondary Paper Portfolio', 5000
);

insert into public.paper_positions (
  id, user_id, portfolio_id, position_type, strategy_type, title, currency,
  planned_allocation
)
select
  fixture.id, fixture.user_id, portfolio.id, 'arbitrage_bundle',
  'assertion', fixture.title, 'USD', 100
from (
  values
    (
      '31000000-0000-0000-0000-000000000001'::uuid,
      '10000000-0000-0000-0000-000000000001'::uuid,
      'User one assertion position'
    ),
    (
      '31000000-0000-0000-0000-000000000002'::uuid,
      '10000000-0000-0000-0000-000000000002'::uuid,
      'User two assertion position'
    )
) as fixture(id, user_id, title)
join public.paper_portfolios portfolio
  on portfolio.user_id = fixture.user_id and portfolio.is_default;

insert into public.paper_position_legs (
  id, user_id, position_id, leg_number, provider_id, market_id, outcome_id,
  action, planned_price, planned_quantity, planned_stake
)
values
  (
    '32000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '31000000-0000-0000-0000-000000000001', 1,
    '20000000-0000-0000-0000-000000000001',
    '21000000-0000-0000-0000-000000000001',
    '22000000-0000-0000-0000-000000000001',
    'back', 2.10, 10, 21
  ),
  (
    '32000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '31000000-0000-0000-0000-000000000002', 1,
    '20000000-0000-0000-0000-000000000002',
    '21000000-0000-0000-0000-000000000002',
    '22000000-0000-0000-0000-000000000002',
    'buy_yes', 0.49, 10, 4.90
  );

insert into public.paper_fills (
  id, user_id, leg_id, fill_key, quantity, price, notional, filled_at
)
values
  (
    '33000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '32000000-0000-0000-0000-000000000001', 'assert-fill-one',
    10, 2.10, 21, now()
  ),
  (
    '33000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '32000000-0000-0000-0000-000000000002', 'assert-fill-two',
    10, 0.49, 4.90, now()
  );

insert into public.paper_settlements (
  id, user_id, position_id, settlement_key, outcome_key, gross_payout,
  net_pnl, settled_at
)
values
  (
    '34000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '31000000-0000-0000-0000-000000000001', 'assert-settlement-one',
    'home', 102, 2, now()
  ),
  (
    '34000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '31000000-0000-0000-0000-000000000002', 'assert-settlement-two',
    'yes', 101, 1, now()
  );

insert into public.paper_ledger_entries (
  id, user_id, portfolio_id, position_id, entry_group_id, idempotency_key,
  entry_type, direction, amount, currency, memo, occurred_at
)
select
  fixture.id, fixture.user_id, position.portfolio_id, position.id,
  fixture.entry_group_id, fixture.idempotency_key, 'adjustment', 'credit',
  fixture.amount, 'USD', 'Assertion fixture', now()
from (
  values
    (
      '35000000-0000-0000-0000-000000000001'::uuid,
      '10000000-0000-0000-0000-000000000001'::uuid,
      '31000000-0000-0000-0000-000000000001'::uuid,
      '35100000-0000-0000-0000-000000000001'::uuid,
      'assert-ledger-one', 2::numeric
    ),
    (
      '35000000-0000-0000-0000-000000000002'::uuid,
      '10000000-0000-0000-0000-000000000002'::uuid,
      '31000000-0000-0000-0000-000000000002'::uuid,
      '35100000-0000-0000-0000-000000000002'::uuid,
      'assert-ledger-two', 1::numeric
    )
) as fixture(id, user_id, position_id, entry_group_id, idempotency_key, amount)
join public.paper_positions position
  on position.id = fixture.position_id and position.user_id = fixture.user_id;

insert into public.performance_snapshots (
  user_id, portfolio_id, snapshot_at, cash_balance, reserved_capital,
  portfolio_value, realized_pnl, unrealized_pnl
)
select
  user_id, id, now(), starting_balance, 0, starting_balance, 0, 0
from public.paper_portfolios
where is_default and user_id in (
  '10000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000002'
);

insert into public.user_alerts (
  id, user_id, name, alert_type, conditions
)
values (
  '36000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  'Assertion Alert', 'roi_threshold', '{"minimum_roi":1}'::jsonb
);

insert into public.user_alert_events (
  id, user_id, alert_id, dedupe_key, title, body
)
values (
  '37000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '36000000-0000-0000-0000-000000000001',
  'assert-alert-event', 'Assertion event', 'Generated by the backend'
);

do $$
begin
  begin
    insert into public.paper_position_legs (
      user_id, position_id, leg_number, provider_id, market_id, outcome_id,
      action, planned_price, planned_quantity, planned_stake
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '31000000-0000-0000-0000-000000000001', 2,
      '20000000-0000-0000-0000-000000000001',
      '21000000-0000-0000-0000-000000000001',
      '22000000-0000-0000-0000-000000000002',
      'back', 2, 10, 20
    );
    raise exception 'ASSERTION FAILED: paper leg accepted an outcome from another market';
  exception when foreign_key_violation then null;
  end;

  begin
    insert into public.paper_ledger_entries (
      user_id, portfolio_id, position_id, entry_group_id, idempotency_key,
      entry_type, direction, amount, currency, memo, occurred_at
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '30000000-0000-0000-0000-000000000001',
      '31000000-0000-0000-0000-000000000001',
      '35200000-0000-0000-0000-000000000001', 'invalid-cross-portfolio',
      'adjustment', 'credit', 1, 'USD', 'Must fail', now()
    );
    raise exception 'ASSERTION FAILED: ledger linked a position to another portfolio';
  exception when foreign_key_violation then null;
  end;
end;
$$;

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-0000-0000-000000000001', true);
select set_config('request.jwt.claim.role', 'authenticated', true);

select pg_temp.assert_true(
  (select count(*) = 1 from public.profiles),
  'RLS exposes only the current profile'
);
select pg_temp.assert_true(
  (select bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
   from public.paper_portfolios),
  'nested portfolio data is isolated by owner'
);
select pg_temp.assert_true(
  (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
   from public.paper_positions)
  and (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
       from public.paper_position_legs)
  and (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
       from public.paper_fills)
  and (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
       from public.paper_settlements)
  and (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
       from public.paper_ledger_entries)
  and (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
       from public.performance_snapshots),
  'every nested paper table is isolated by owner'
);
select pg_temp.assert_true(
  (select count(*) = 1 and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
   from public.user_alert_events),
  'generated alert events are isolated by owner'
);
select pg_temp.assert_true(
  (select count(*) = 1
          and bool_and(user_id = '10000000-0000-0000-0000-000000000001'::uuid)
   from public.user_provider_connections),
  'safe provider connection metadata is isolated by owner'
);

update public.profiles
set display_name = 'DB Test One Updated'
where id = '10000000-0000-0000-0000-000000000001';

update public.paper_portfolios
set name = 'Renamed Secondary Portfolio'
where id = '30000000-0000-0000-0000-000000000001';

update public.paper_positions
set notes = 'Owner-authored research note'
where id = '31000000-0000-0000-0000-000000000001';

update public.user_alert_events
set read_at = now()
where id = '37000000-0000-0000-0000-000000000001';

select pg_temp.assert_true(
  (select display_name = 'DB Test One Updated' from public.profiles)
  and (select name = 'Renamed Secondary Portfolio' from public.paper_portfolios
       where id = '30000000-0000-0000-0000-000000000001')
  and (select notes = 'Owner-authored research note' from public.paper_positions)
  and (select read_at is not null from public.user_alert_events),
  'safe owner-editable columns remain writable'
);

insert into public.user_watchlists (user_id, name, is_default)
values ('10000000-0000-0000-0000-000000000001', 'Own list', true);

do $$
begin
  begin
    insert into public.user_watchlists (user_id, name)
    values ('10000000-0000-0000-0000-000000000002', 'Cross-account list');
    raise exception 'ASSERTION FAILED: cross-account watchlist insert succeeded';
  exception
    when insufficient_privilege then null;
  end;
end;
$$;

do $$
begin
  begin
    update public.paper_portfolios
    set starting_balance = 999999
    where id = '30000000-0000-0000-0000-000000000001';
    raise exception 'ASSERTION FAILED: client rewrote a starting balance';
  exception when insufficient_privilege then null;
  end;

  begin
    update public.paper_positions
    set realized_pnl = 999999
    where id = '31000000-0000-0000-0000-000000000001';
    raise exception 'ASSERTION FAILED: client rewrote realized P&L';
  exception when insufficient_privilege then null;
  end;

  begin
    update public.user_alert_events
    set body = 'Rewritten generated payload'
    where id = '37000000-0000-0000-0000-000000000001';
    raise exception 'ASSERTION FAILED: client rewrote a generated alert';
  exception when insufficient_privilege then null;
  end;

  begin
    update public.user_provider_connections
    set credential_mask = '{"api_key":"plaintext-must-not-be-client-written"}'::jsonb
    where id = '38000000-0000-0000-0000-000000000001';
    raise exception 'ASSERTION FAILED: client rewrote provider credential metadata';
  exception when insufficient_privilege then null;
  end;

  begin
    insert into public.paper_positions (
      user_id, portfolio_id, position_type, strategy_type, title, currency,
      planned_allocation
    )
    select
      '10000000-0000-0000-0000-000000000001', id, 'single_leg',
      'assertion', 'Unauthorized direct insert', 'USD', 1
    from public.paper_portfolios
    where user_id = '10000000-0000-0000-0000-000000000001' and is_default;
    raise exception 'ASSERTION FAILED: client inserted a paper position directly';
  exception when insufficient_privilege then null;
  end;
end;
$$;

do $$
begin
  begin
    update public.profiles
    set id = '10000000-0000-0000-0000-000000000002'
    where id = '10000000-0000-0000-0000-000000000001';
    raise exception 'ASSERTION FAILED: profile ownership reassignment succeeded';
  exception
    when insufficient_privilege or unique_violation or foreign_key_violation then null;
  end;
end;
$$;

reset role;
rollback;
