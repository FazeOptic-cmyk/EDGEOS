# EDGEOS Supabase database

This directory contains the versioned PostgreSQL schema for EDGEOS. It targets
PostgreSQL 15 and Supabase Auth. All client-exposed tables use row-level
security (RLS), public market data is client-read-only, and private records are
isolated by `auth.uid()`.

## What is included

- Canonical sports, leagues, participants, events, provider events, markets,
  outcomes, versioned settlement rules, and fee schedules.
- Idempotent quote, order-book, raw-ingestion, opportunity, and historical
  snapshot storage.
- Explicit `verified`, `review_required`, and `rejected` market matching with
  retained checks and explanations.
- User profiles, settings, saved filters, watchlists, alerts, saved research,
  and notes.
- Paper portfolios, bundled positions, legs, fills, settlements, append-only
  ledger entries, and performance snapshots.
- Model and backtest runs, aggregate results, and signal-level source-time
  checks that reject look-ahead records.
- Operational ingestion/error tables available only to trusted server workers.

Money, prices, odds, probabilities, quantities, fees, and returns use exact
`numeric` columns. Instants use `timestamptz`; PostgreSQL stores them as UTC and
clients should render them in `profiles.timezone`.

## Local setup

Install Docker and a current Supabase CLI, then from the repository root run:

```bash
npx supabase@latest start
npx supabase@latest db reset
npx supabase@latest status
```

`db reset` applies every file in `migrations/` and then `seed.sql`. The default
seed is intentionally empty: EDGEOS never inserts fabricated production data.
Use application demo fixtures only when demo mode is explicitly enabled and
keep `is_demo = true` / `feed_class = 'demo'` on every demo quote or snapshot.

Create later migrations with the CLI rather than editing an applied migration:

```bash
npx supabase@latest migration new descriptive_change_name
npx supabase@latest db reset
npx supabase@latest migration list --local
```

Generate application types after migrations:

```bash
npx supabase@latest gen types typescript --local > packages/types/supabase.ts
```

## Database assertions

The assertion script checks RLS coverage, security-invoker views, restricted
operational tables, immutable ledger permissions, Auth provisioning, and
cross-user isolation. Run it against local Supabase (the URL comes from
`supabase status`):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/tests/schema_assertions.sql
```

The script runs in a transaction and rolls back all test principals and data.

The repository's plain PostgreSQL Docker service can syntax-check the migration
only when `tests/local_auth_prelude.sql` is mounted first. That file provides a
minimal local stand-in for Supabase-owned Auth objects. It is deliberately kept
outside `migrations/` and **must never be applied to a Supabase project**.

## Required Dashboard configuration

For each deployed project, configure these in the Supabase Dashboard:

1. **Authentication > URL Configuration:** set the production Site URL and
   exact localhost/preview/production redirect allow-list. Never use a broad
   wildcard for the production domain.
2. **Authentication > Providers > Email:** keep email confirmation enabled.
   Configure a production SMTP provider; the local Inbucket service is only for
   development. Set password and abuse/rate-limit controls appropriate to the
   deployment.
3. **Authentication > Providers > Google (optional):** create the provider's
   OAuth credentials, enter them in the Dashboard, and add the Supabase callback
   URL at Google. Local development can instead set
   `SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID` and
   `SUPABASE_AUTH_EXTERNAL_GOOGLE_SECRET` before `supabase start`, then set
   `[auth.external.google].enabled = true` in `config.toml`.
4. **API > Data API Settings:** expose `public` only. Do not expose `private` or
   `auth`. The migration includes explicit table grants in addition to RLS.
5. **Authentication > Sessions:** use a short access-token lifetime for
   sensitive deployments. EDGEOS implements account deletion server-side; it must
   remain server-side, revoke/sign out active sessions, then call the Admin user
   deletion API with a server-only key. Deleting only `profiles` does not delete
   an Auth account.
6. **Realtime (optional):** explicitly add only the tables the UI truly needs to
   the `supabase_realtime` publication. RLS still applies; do not publish raw
   ingestion or operational error tables.

## Application credentials

Frontend code may receive only the project URL and publishable key (or legacy
anon key). A typical frontend configuration is:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://PROJECT_REF.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

Backend ingestion workers need server-only database access. Never use it in a
browser bundle, log it, or prefix it with `NEXT_PUBLIC_`. Owner connection
metadata is stored in the RLS-protected public table; provider credential values
are AES-256-GCM envelopes in the non-exposed `private` schema. The encryption
master key remains in the deployment secret manager, never Postgres.

## Security model

- Shared reference and normalized market tables have `SELECT` policies for
  `anon` and `authenticated`; no client write policy exists. Backend ingestion
  uses the service role.
- Private tables carry `user_id` on every nesting level. Composite foreign keys
  bind nested rows to a parent owned by the same user, while RLS checks
  `(select auth.uid()) = user_id` for both existing and proposed rows.
- Fills, settlements, and ledger records cannot be updated or deleted through
  the Data API. Corrections are new `adjustment` ledger entries with a memo.
- Public views are declared `security_invoker = true`, so underlying RLS is not
  bypassed.
- The Auth trigger is the sole security-definer function. It lives in the
  unexposed `private` schema, uses an empty `search_path` and qualified names,
  and has no execution grant for API roles.
- `match_reviews`, `raw_ingestion_records`, `ingestion_runs`, and
  `system_errors` deliberately have RLS enabled with no client policy or grant.

Run backend ingestion with a supervised worker service (container worker,
systemd, or equivalent). A web deployment alone does not poll continuously. The
worker uses a database lease, run/health records, request budgets, a stable
`normalized_adapter_contract` capture, and idempotent normalized event/market/
outcome/quote writes. That capture is not the byte-for-byte upstream HTTP body.
Normalized records retain adapter-produced event and settlement evidence in
metadata, and the demo provider is stored under the explicit `edgeos_demo` slug.
The worker does not yet generate matches/opportunities or alert events and does
not consume per-user saved connection credentials. It records a blocked post-
ingestion diagnostic and zero created opportunities/alerts rather than implying
that the missing stages ran. A production deployment still needs explicit
provider limits/backoff, retention/partitioning, and monitoring appropriate to
its volume.
