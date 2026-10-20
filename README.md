# EDGEOS

EDGEOS is an account-based sports-market pricing research and simulated
paper-trading application. It combines read-only provider adapters, strict
settlement matching, a Python Decimal cash-flow engine, transparent opportunity
ranking, paper accounting, analytics, and a premium responsive Next.js interface.

> **Research and paper trading only.** EDGEOS does not place bets or trades,
> hold funds, confirm accepted stakes, or guarantee profit. Prices move, rules can
> differ, and legs can fail. The advised real-money allocation for this release is
> **$0**.

Start with the exhaustive [product/engineering handoff](docs/HANDOFF.md). It
identifies every workflow as **WORKING**, **NEEDS CONFIGURATION**, **DEMO ONLY**,
or **UNAVAILABLE**, includes exact model limitations, and explains conservative
paper-capital use.

## Included

- Next.js App Router, React, TypeScript, Tailwind, TanStack Query, Recharts, Zod,
  Inter/Manrope, and Lucide UI.
- Dashboard, scanner/detail drawer, Edge Lab, markets, paper portfolio, analytics,
  watchlist, alerts, and settings/API Connections.
- FastAPI provider, normalization, matching, Decimal calculation, ranking, paper
  accounting, replay, persistence, and ingestion modules.
- Supabase Auth/Postgres migrations with indexes, constraints, audit fields,
  precise numeric types, and RLS/grants.
- Read-only REST adapters for The Odds API, Kalshi, Polymarket International, and
  the distinct Polymarket US product; plus an unmistakable local demo adapter.
- AES-256-GCM provider credential envelopes with owner/provider/key-version
  binding, masking, and redaction.
- Server filtering/sorting/pagination and a web/API demo/live mode handshake.
- Electron + PyInstaller + NSIS Windows packaging pipeline.
- Python, web, browser, database, desktop, and packaging tests.

No direct sportsbook API is claimed. A named sportsbook appears only if the
configured authorized aggregator actually returns it.

## Prerequisites

- Node.js 22+ and npm 10+
- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Docker 24+ only for the Compose workflow
- Supabase CLI/project for hosted authentication and Postgres
- Windows x64 + PowerShell 7 only when building the installer

Check tools without printing secret values:

```bash
./scripts/doctor.sh
```

## Quick start: explicit demo mode

```bash
cp .env.example .env
npm install
cd apps/api && uv sync --all-groups && cd ../..
```

Terminal 1:

```bash
make api
```

Terminal 2:

```bash
make web
```

Open <http://localhost:3000>. OpenAPI is at <http://localhost:8000/docs>; health
is <http://localhost:8000/api/v1/health>.

Demo opportunities are deterministic fixtures, not provider observations. Demo
paper/watchlist/alert/settings state in this two-process development workflow is
API process memory and resets on restart. The installed Windows application is
different: it supplies an application-owned SQLite path and retains its local
paper and workspace records across launches. If the portfolio API is unavailable,
the UI shows an error rather than generating browser-only positions or CSV.

## Docker development

```bash
cp .env.example .env
docker compose up --build
```

Compose starts Postgres, API, worker, and web. Its Postgres uses a clearly
test-only `auth.users` shim so migrations can run on vanilla PostgreSQL; it is not
a Supabase Auth emulator. Delete the named volume only when you deliberately want
to erase local data.

## Supabase setup

1. Create a Supabase project.
2. Copy the project URL and current publishable key to `.env`:

   ```dotenv
   NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
   SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   ```

3. Keep `SUPABASE_SECRET_KEY` server-only. It is used only for privileged server
   work such as exact account deletion; never put it in `NEXT_PUBLIC_*`.
4. Apply all migrations in lexical order:

   ```bash
   npx supabase login
   npx supabase link --project-ref YOUR_PROJECT_REF
   npx supabase db push
   ```

   Or run `supabase/migrations/*.sql` in lexical order in the SQL editor.
5. In Authentication → URL Configuration set the hosted Site URL and allow:

   ```text
   http://localhost:3000/auth/callback
   http://localhost:3000/auth/confirm
   http://localhost:3000/auth/update-password
   https://YOUR_WEB_HOST/auth/callback
   https://YOUR_WEB_HOST/auth/confirm
   https://YOUR_WEB_HOST/auth/update-password
   ```

6. Keep email confirmation enabled, configure production SMTP, and use this SSR
   confirmation template destination:

   ```text
   {{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email
   ```

7. Optional Google: create Google OAuth credentials, enable the Supabase provider,
   and add the Supabase callback displayed by the dashboard.
8. Run Supabase security/performance advisors and stop if any exposed table lacks
   the expected RLS/grant posture.

The hosted web supports signup, verification, reset, optional Google, persistent
sessions, profile/preferences, export, and account deletion. The desktop can
connect to a configured hosted web application; see the Windows section for
local-demo versus hosted-live behavior.

## Database and encrypted provider connections

Use a psycopg URI:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
DATABASE_SSLMODE=require
```

Do not use `postgresql+asyncpg://`; this API uses psycopg. For local Docker,
`DATABASE_SSLMODE=prefer` is appropriate.

Generate a 32-byte credential key outside the repository and base64-encode it.
Store it in the hosting secret manager under the exact variable documented by
`.env.example`. Never store the master key in Postgres. Keep its version value
stable until a controlled re-encryption rotation is complete.

Settings → API Connections contains the masked owner-scoped setup, test,
replacement, enable/disable, delete, selection, budget, quota, and diagnostic UI.
The registered FastAPI routes require live mode, an authenticated account,
applied migrations, `DATABASE_URL`, and the server-only credential master key.
Secrets are write-only: responses return masks and status, never the original
value. The test action makes one bounded no-retry read-only request; success is
not proof of continuing uptime, entitlement, or accepted size.

## Read-only providers

### The Odds API

Obtain a key directly from the provider. The authenticated API Connections form
can store and test an owner-scoped key. The current ingestion worker does not yet
consume saved user connections, so configure its separate server-only key:

```dotenv
THE_ODDS_API_KEY=...
THE_ODDS_API_SPORTS=basketball_nba,americanfootball_nfl
```

Use current official sport keys. An empty list prevents accidental polling/quota
use. The feed is periodic aggregator data and normally has no accepted-stake or
book-depth evidence.

### Kalshi

Implemented public Trade API v2 REST discovery/market/book reads do not use a
private trading key. Authenticated WebSocket, accounts, and order entry are not
implemented.

### Polymarket

International Gamma/CLOB public market data and Polymarket US Gateway are
separate adapters and ID spaces. They are never interchanged. Institutional gRPC,
private trading, and geographic/eligibility workarounds are absent.

Official sources and exact implemented/unsupported capability are in
[the provider matrix](docs/PROVIDER_MATRIX.md). Fixture tests do not constitute a
live provider check.

## Live read-only operation

Set matching modes and server origins:

```dotenv
EDGEOS_MODE=live
NEXT_PUBLIC_APP_MODE=live
EDGEOS_INTERNAL_API_ORIGIN=http://api:8000
WEB_ORIGIN=https://YOUR_WEB_HOST
```

The worker's principal runtime controls are:

```dotenv
INGESTION_POLL_SECONDS=60
INGESTION_REQUEST_BUDGET_PER_HOUR=120
QUOTE_STALE_AFTER_SECONDS=90
EDGEOS_PIPELINE_TOTAL_ALLOCATION=1000
EDGEOS_MAX_QUOTE_SKEW_SECONDS=5
EDGEOS_PIPELINE_MINIMUM_LIQUIDITY=1
```

`INGESTION_<PROVIDER_CODE>_REQUEST_BUDGET_PER_HOUR` overrides the shared budget
for one adapter. `EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY`,
`EDGEOS_MEASURED_QUOTE_UPDATE_HZ`, `EDGEOS_MEASURED_QUOTE_STABILITY`, and
`EDGEOS_EXPECTED_LOCKUP_SECONDS` are optional measured inputs; leave them unset
when no defensible observation exists. They are never synthesized. The worker
also accepts `EDGEOS_MAX_QUOTE_AGE_SECONDS` as the quote-age fallback when
`QUOTE_STALE_AFTER_SECONDS` is absent.

Apply migrations and configure auth/database/provider environment. The intended
durable flow is:

```text
provider response -> normalized-contract capture -> validated normalization -> canonical mapping
-> settlement classification -> Decimal cash flow -> gates/ranking -> persistence
-> lifecycle/alert evaluation -> scanner
```

The current worker runs that chain after each bounded provider-poll cycle. It
persists provider events, exact-evidence canonical links, markets, rules,
outcomes, quotes, match audits, strict opportunities, lifecycle changes, health,
and qualifying in-app alert events under a database lease and versioned run
record. Its capture is a stable normalized-adapter envelope rather than the
byte-for-byte upstream body. Writes are idempotent; a database failure propagates
before absence-based withdrawal, and a cycle with no evaluable markets expires
already-stale records without broadly withdrawing prior rows.

Only `executable_quote_supported` results are active in the strict feed. Every
selected leg must share a materialized canonical event and carry current provider
time, source, executable-price basis, known depth, effective-dated fee evidence,
compatible currency, verified settlement rules, and a complete all-scenario
payout vector that remains positive after modeled costs. Missing evidence is a
blocker, not a zero-cost or unlimited-liquidity assumption. Cached, historical,
demo, midpoint/last-trade, and otherwise indicative inputs remain theoretical.
In particular, The Odds API is indicative because it normally cannot prove an
accepted stake or executable depth.

Therefore an empty live scanner can be the correct healthy result. Never seed it
with demo rows or weaken evidence gates. No production-provider request was
completed in this source verification, so adapters remain fixture-tested and
runtime-dependent. See [live-pipeline verification](docs/LIVE_PIPELINE_VERIFICATION.md),
[deployment](docs/DEPLOYMENT.md), and the [operator guide](docs/OPERATOR_GUIDE.md).

## Financial reference

With compatible exhaustive two-way outcomes, no costs, odds `2.20` and `2.00`,
and `$210` simulated capital, the authoritative fixture allocates `$100` and
`$110`. Either outcome pays `$220`: `$10` gross profit and approximately
`4.7619%` gross ROI. Fees, rounding, stale prices, missing depth, partial fills,
or settlement differences can eliminate that edge.

The engine uses `edgeos.cashflow.v2`, Python Decimal, complete payout vectors,
discrete quantities, fee rounding, slippage, depth/size/capital caps, quote
age/skew, and settlement gates. Large discrete state spaces use a deterministic
bounded search and are not represented as a proof of global optimum. See
[model assumptions](docs/MODEL_ASSUMPTIONS.md).

## Tests

Run the maintained verification workflow:

```bash
./scripts/verify.sh
```

Or individual suites:

```bash
make test-api
make test-web
npm run desktop:test
npm run typecheck --workspace=@edgeos/desktop
npx playwright install chromium
npm run test:e2e
```

Database assertions run in CI against clean PostgreSQL 16 or can run against the
healthy Compose database. Provider tests use official-shaped mocked payloads;
optional live checks require explicit owner configuration and request budgets and
must be reported separately.

## Production builds

```bash
npm run build
docker build -t edgeos-api ./apps/api
docker build -t edgeos-web ./apps/web
```

Both Dockerfiles support an optional BuildKit CA secret named
`edgeos_build_ca` for dependency downloads; it is not copied into the image.

Deploy Supabase, web, API, and a continuously supervised worker from the same
release. Verify the lease/run, captures, canonical links, rule/fee evidence,
timestamps, health, budgets, match audits, opportunity lifecycle, alert counts,
and idempotent writes. The browser polls persisted live views every 15 seconds
only while visible and refreshes on focus; that presentation refresh is not
ingestion and cannot replace the worker. A 24/7 scanning claim is valid only for
a deployed, monitored worker, not for the local Windows package or an open tab.

## Windows installer

The repository has an x64 per-user NSIS build pipeline. Standard artifacts bundle
the web UI and local API in explicitly labeled demo market-data mode. The installed app keeps
paper positions/fills/settlements, saved opportunities, watchlists, alert rules,
and settings in device-local SQLite at
`%APPDATA%\EDGEOS\data\edgeos-local.sqlite3`; those records survive an ordinary
close/reopen and upgrade. Uninstall intentionally retains `%APPDATA%\EDGEOS` so a
reinstall does not silently erase the simulated workspace. The installer does
not add a continuous-ingestion worker, hosted database, updater, or live provider
secret.

Connection → Server settings can select a separately deployed HTTPS EDGEOS web
origin. Hosted-live mode verifies the server's live/authentication contract,
uses an origin-isolated persistent session, and starts no bundled demo services.
It never silently falls back to demo when the hosted server is unavailable.
The hosted deployment still requires Supabase, FastAPI, the web/BFF, and a
supervised ingestion worker. No hosted deployment is provisioned by installing
the desktop application.

The local database is not encrypted at rest and must not contain provider, Auth,
or signing secrets. Settings exposes a versioned `.edgeos-backup` export and an
explicit, integrity-checked, transactional restore for the simulated local
workspace; backup scheduling, encryption, and rotation remain operator tasks.
Each desktop launch uses a new server-only API credential between the packaged
Next.js and FastAPI processes, and remembered window bounds are validated and
clamped to an available display.
The Windows packaged-runtime smoke test writes a local setting, restarts the
bundled API against the same database, and verifies that the setting survives.

Build on Windows with PowerShell 7, Node 22, Python 3.12, and `uv`:

```powershell
pwsh -File .\scripts\build-windows-installer.ps1
```

Verified outputs are staged under `artifacts/windows`. Tagged releases must be
Authenticode-signed and signer-pinned; unsigned PR/manual artifacts are
development-only. The verified installer output is
`artifacts/windows/EDGEOS Setup <version>.exe`. This Linux audit did not produce
an `.exe`; PyInstaller requires the Windows build/CI path.

The packaged local-demo interface is not a cloud account. In hosted-live mode,
the selected web application's authentication and user data are used; browser
email/OAuth handoffs are not implemented as native desktop deep links. Never
embed a secret key. There is no auto-updater; install new verified versions
manually. See [Windows installer guide](docs/WINDOWS_INSTALLER.md).

## Capability snapshot

| Capability | Status |
| --- | --- |
| UI, demo scanner, detail, authoritative Edge Lab | **WORKING / DEMO LABELLED** |
| Decimal math, matching, ranking, scenario/replay engines | **WORKING** |
| Supabase auth/profile/preferences | **NEEDS CONFIGURATION** |
| Provider REST adapters | **FIXTURE TESTED; RUNTIME DEPENDENT** |
| API Connections encrypted storage/test | **WORKING; NEEDS LIVE AUTH/DB/KEY/PROVIDER** |
| Durable ingestion through matching/opportunity lifecycle | **WORKING PIPELINE; NEEDS DATABASE/WORKER AND CURRENT PROVIDER EVIDENCE** |
| Live Markets browser | **UNAVAILABLE UNTIL PERSISTED SNAPSHOT READS ARE CONNECTED** |
| Live scanner opportunity generation | **WORKING FAIL-CLOSED; MAY CORRECTLY BE EMPTY; LIVE PROVIDER RUN NOT DEMONSTRATED HERE** |
| Saved/favorite opportunities | **WORKING; WINDOWS LOCAL SQLITE / LIVE NEEDS DATABASE/AUTH** |
| Development demo paper portfolio/analytics | **WORKING; PROCESS MEMORY** |
| Installed Windows local paper/workspace state | **WORKING; DEVICE-LOCAL SQLITE** |
| Live paper portfolio/ledger/analytics | **WORKING; NEEDS AUTH/DB/MIGRATIONS** |
| Live watchlist/alert-rule/settings/dashboard data | **WORKING; NEEDS AUTH/DB/MIGRATIONS** |
| Alert evaluation and in-app event lifecycle | **WORKING IN WORKER; NEEDS AUTH/DB/MIGRATIONS/CONTINUOUS WORKER** |
| Email/SMS/push/native Windows alert delivery | **UNAVAILABLE** |
| Windows installer pipeline | **CONFIGURED; WINDOWS BUILD/INSTALL ACCEPTANCE PENDING** |
| Real-money execution | **INTENTIONALLY UNAVAILABLE** |

## Documentation

- [Detailed handoff](docs/HANDOFF.md)
- [API](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data provenance](docs/DATA_PROVENANCE.md)
- [Provider matrix](docs/PROVIDER_MATRIX.md)
- [Model assumptions](docs/MODEL_ASSUMPTIONS.md)
- [Security](docs/SECURITY.md)
- [Operator guide](docs/OPERATOR_GUIDE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Live pipeline verification](docs/LIVE_PIPELINE_VERIFICATION.md)
- [Windows installer](docs/WINDOWS_INSTALLER.md)
- [Desktop release notes](docs/RELEASE_NOTES.md)
- [Supabase database](supabase/README.md)

No provider affiliation is implied. Review current laws, eligibility, terms,
licensing, attribution, age, and geographic requirements. Do not use EDGEOS to
bypass access controls.
