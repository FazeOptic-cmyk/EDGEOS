# Deployment and continuous operation

EDGEOS needs four separately operated resources for a hosted live research
deployment:

1. Supabase Auth + Postgres;
2. Next.js web/BFF;
3. FastAPI;
4. background ingestion, opportunity, lifecycle, and in-app alert worker.

The Windows installer provides a bundled local-demo workspace and an optional
connection to a separately deployed HTTPS live web application. It does not
replace the hosted worker/database and does not provide 24/7 collection.

## Release preflight

1. Review `HANDOFF.md` and the provider matrix; do not promote unavailable work.
2. Run `./scripts/verify.sh` from a clean release workspace.
3. Build immutable web/API images from the same revision.
4. Apply every Supabase migration in lexical order before new application code.
5. Run schema assertions and Supabase security/performance advisors.
6. Confirm `NEXT_PUBLIC_APP_MODE=live` and `EDGEOS_MODE=live`.
7. Confirm exact web, API, internal API, CORS, and Supabase origins.
8. Store database/provider/encryption/admin secrets in the platform secret manager.
9. Review current provider documentation/terms and explicit request budgets.
10. Prepare rollback images, database backup, and a non-destructive forward-fix
    plan. Avoid destructive down-migrations during an incident.

## Database and Supabase

Use a standard psycopg URI:

```text
postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

Do not use a SQLAlchemy-only `postgresql+asyncpg://` scheme. Hosted live mode
defaults to `DATABASE_SSLMODE=require`; local Docker may use `prefer`. Choose the
current Supabase direct/pooler endpoint appropriate to the process lifetime and
IPv4/network support.

Apply migrations:

```bash
npx supabase login
npx supabase link --project-ref YOUR_PROJECT_REF
npx supabase db push
```

Then verify RLS/grants, credential private schema, worker leases, rule-change
invalidation, and indexes. The local Compose Auth schema is a test shim, not a
Supabase Auth emulator.

Configure Supabase Site URL and exact hosted callback, confirmation, and password
update URLs. Keep email confirmation enabled and configure production SMTP.

## Web

Requirements: Node.js 22+. Build with `npm ci` and `npm run build`, then run the
Next production server on a Node-capable host.

Build-time browser variables are public by definition:

- `NEXT_PUBLIC_APP_MODE`
- `NEXT_PUBLIC_API_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`

`EDGEOS_INTERNAL_API_ORIGIN` is server-only and points the same-origin BFF to
FastAPI. Never use a provider key, database URL, credential master key, or
Supabase secret in `NEXT_PUBLIC_*`.

The BFF must receive the original HTTPS origin correctly from the platform. Test
same-origin mutation enforcement and the mode handshake behind the production
proxy/CDN.

## API

Build `apps/api/Dockerfile`, expose port 8000 only through the intended private
network/public HTTPS gateway, and health-check `/api/v1/health`. A process-health
200 does not establish provider or worker health.

Configure at least:

- mode/environment and exact CORS origin;
- Supabase URL and server-only secret only where account deletion is enabled;
- `DATABASE_URL`/TLS mode;
- provider credential master key/version;
- explicit reviewer user IDs;
- maximum quote age;
- provider credentials only through trusted server storage/environment.

Scale API instances only after confirming transactions/idempotency and database
pool limits. Do not run migrations automatically in every API replica.

## Worker

Run the same Python release with:

```bash
cd apps/api
uv run python -m edgeos.worker
```

The checked-in live worker acquires a database lease, heartbeats it, writes a
versioned ingestion run, enforces request budgets, polls provider events/markets,
persists stable normalized-contract captures and idempotent normalized rows,
then runs the strict match → calculation → lifecycle → in-app alert cycle.
It updates health and releases the lease after the shared cycle. Configure
provider universe, poll intervals, request budgets, database, retention, and
structured logging. It must:

- acquire/renew a partition lease before polling;
- avoid concurrent ownership of the same provider/universe;
- write run/attempt/heartbeat state;
- preserve its explicit `normalized_adapter_contract` capture and separate
  provider/fetch/ingestion times (it is not byte-for-byte upstream raw);
- validate/normalize/upsert idempotently;
- materialize canonical events only from exact normalized identity evidence;
- persist cross-market match audits and only fully evidenced strict opportunities;
- expire stale/closed rows and withdraw opportunities absent from the evaluated
  scope only after successful persistence;
- evaluate qualifying enabled alert rules with deduplication and cooldown;
- honor provider/connection request budgets and `429` backoff;
- expose/record failure without turning it into an empty success.

Start one replica first. Confirm a lease, run record, captures/normalized writes,
deduplication, and freshness before adding partitions. A scheduler invocation may
be used for slow feeds only if the UI advertises its real cadence. A process that
is not running is not monitoring.

The worker does not consume user-saved connection credentials. It uses public
adapters plus deployment `THE_ODDS_API_KEY`. The post-ingestion cycle fails
closed when canonical identity, settlement scenarios, provider timestamp,
source, current executable basis, effective fee evidence, depth, currency, or a
positive full-scenario minimum is missing. It is valid for a healthy run to
persist match audits but zero strict opportunities or alerts.

Configure the execution and research boundaries explicitly:

```dotenv
INGESTION_POLL_SECONDS=60
INGESTION_REQUEST_BUDGET_PER_HOUR=120
QUOTE_STALE_AFTER_SECONDS=90
EDGEOS_PIPELINE_TOTAL_ALLOCATION=1000
EDGEOS_MAX_QUOTE_SKEW_SECONDS=5
EDGEOS_PIPELINE_MINIMUM_LIQUIDITY=1
EDGEOS_PIPELINE_SLIPPAGE_BPS=10
```

Slippage is an explicit adverse-price assumption, not a measured fill guarantee.
Leave optional measured fill-probability, update-frequency, and quote-stability
settings unset until legitimate measurements are available.

- `INGESTION_<PROVIDER_CODE>_REQUEST_BUDGET_PER_HOUR` overrides the shared
  hourly request budget for one adapter.
- `EDGEOS_MAX_QUOTE_AGE_SECONDS` is the quote-age fallback when
  `QUOTE_STALE_AFTER_SECONDS` is absent.
- `EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY`,
  `EDGEOS_MEASURED_QUOTE_UPDATE_HZ`, `EDGEOS_MEASURED_QUOTE_STABILITY`, and
  `EDGEOS_EXPECTED_LOCKUP_SECONDS` are optional measured inputs. Leave them
  unset when no defensible measurement exists; do not invent values to improve
  a rank.

The worker retries failed cycles with bounded exponential backoff. Supervise the
process with the hosting platform and restart it on process/machine failure.
Running the API or keeping a browser tab open does not run ingestion.

## Provider configuration

The Odds API consumes quota. Start with one official sport key, `h2h`, one region,
a conservative interval, and a low hourly budget. Add coverage only after reading
actual quota headers. Its sportsbook prices are indicative in the strict engine
because the aggregator normally does not prove accepted stake or executable
depth. Public Kalshi/Polymarket REST does not need a private trading credential
but still needs budgets, backoff, terms review, and actual rule/fee/depth evidence
for any strict row.

Do not purchase or upgrade a plan automatically. Never promise individual
sportsbook coverage; record only bookmaker keys actually returned.
Fixture-tested adapters are not evidence that a production provider responded in
this deployment. Record bounded live-probe results, date, provider/product, and
request scope separately without logging secrets.

## Rollout order

1. Apply additive/backward-compatible migrations.
2. Deploy API with the worker stopped; verify health, auth, mode, DB, and OpenAPI.
3. Deploy one worker; verify lease/heartbeat, budgets, run status, provider
   health, normalized-contract captures, canonical links, and normalized records.
4. Inspect the post-ingestion diagnostic: loaded/evaluable markets, candidate
   audits, combinations, theoretical/strict counts, persistence, withdrawals,
   expiry, alert create/deduplicate/filter counts, and rejection reasons.
5. Reproduce any strict row from provider IDs, rule/fee versions, quotes, depth,
   full scenario payouts, and calculation version. A zero-row result is valid.
6. Verify provider/fetch/ingestion timestamps, idempotency, stale/withdrawal
   handling, and that failed persistence cannot trigger broad withdrawal.
7. Deploy web; smoke-test sign-in, mode block, API Connections' masked CRUD and
   bounded test, markets, scanner, Edge Lab, durable paper workflow/export,
   watchlist/alert-rule/settings persistence, dashboard summary, and account
   deletion policy.
8. Create a narrowly scoped in-app alert rule and verify threshold filtering,
   deduplication/cooldown, and withdrawal when the opportunity expires or changes.
9. Observe errors/age/quota/database load through at least several polling cycles
   before increasing coverage.

An empty live scanner can be correct. Do not insert demo rows or weaken gates to
make it look populated.

The live web refreshes persisted scanner, dashboard, markets/health, and alert
views every 15 seconds while the document is visible and again on focus. It does
not poll provider APIs and stops foreground polling when hidden. Freshness still
comes from provider timestamps and the supervised worker, not the browser clock.

## Continuous monitoring

Alert operators on:

- API/web 5xx and latency;
- worker lease expiry, missed heartbeats, and run duration;
- provider last attempt/success/market observation and age;
- provider request count, retries, 429/backoff, quota/budget remaining;
- schema validation and raw/normalized rejection count;
- duplicate/conflict count;
- candidates surviving each matching/calculation gate;
- quote-age/skew and unknown-depth/unknown-fee rates;
- opportunity create/update/withdraw/expire rates;
- in-app alert create/deduplicate/filter/withdraw rates;
- database connection/pool, storage growth, backup failure;
- authentication/RLS denial anomalies;
- credential decrypt failures without logging the envelope/key.

The worker evaluates supported user rules and stores in-app alert events. It does
not deliver email, SMS, push, or native Windows notifications. Infrastructure
alerts above remain a separate responsibility of the hosting/observability
system; a user opportunity rule must not be treated as an operations pager.

## Retention and backup

Choose raw payload, quote/book, opportunity-history, error, and audit retention
according to provider licensing and research needs. Use UTC. Preserve rule/fee
versions needed to reproduce calculations. Never silently rewrite earlier market
evidence; corrections create later revisions.

Back up private paper/research records and credential ciphertext, but remember
that ciphertext is useless without the separately protected key/version. Test a
restore in an isolated environment. A credential-key rotation must re-encrypt and
verify all envelopes before retiring the prior key.

## Rollback

Roll back web/API/worker together when contracts changed. Stop new worker writes
if a normalizer/calculator regression threatens evidence. Preserve raw records,
leases, review history, and ledgers. Prefer a forward additive repair over a
destructive schema rollback.

After rollback, verify mode headers, parser versions, worker lease ownership,
idempotency, quote freshness, and scanner reproduction before reopening live
research.

## Windows release

Build Windows artifacts only on Windows through
`scripts/build-windows-installer.ps1` or the checked-in workflow. A production
artifact must be Authenticode-signed, timestamped, signer-pinned, hash-verified,
installed, and smoke-tested. The Linux source audit did not produce an `.exe`.
See `WINDOWS_INSTALLER.md`.
