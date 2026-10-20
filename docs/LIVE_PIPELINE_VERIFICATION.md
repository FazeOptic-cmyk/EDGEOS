# Live pipeline verification record

Last source audit: 2026-10-20  
Scope: read-only market research, strict opportunity generation, in-app alert
events, and foreground UI refresh

This note separates a connected code path from evidence that a production
provider and deployment have actually exercised it. EDGEOS remains a research
and paper-trading system. It does not submit orders, confirm accepted stakes, or
promise realized profit. The advised real-money allocation for this release is
**$0**.

## Status at this revision

| Component | Status | What that means |
| --- | --- | --- |
| Ingestion to opportunity pipeline | **REAL POSTGRES TRANSACTION PATH VERIFIED WITH SYNTHETIC FIXTURES; DEPLOYMENT REQUIRED** | An opt-in PostgreSQL 16 test exercises ingestion, canonical/rule materialization, quotes, matching/calculation, persisted opportunities, alerts, the actual API DTO, withdrawal, and restoration. The evidence is synthetic, not a provider-backed opportunity. |
| Strict live scanner rows | **CONNECTED, FAIL-CLOSED** | Only current, non-demo records that satisfy every evidence and cash-flow gate are persisted as active opportunities. A correctly configured scanner may remain empty. |
| Match audits | **CONNECTED; V3 REGRESSION TESTED** | Candidate decisions and explanations are persisted for cross-market pairs. `edgeos.settlement.v3` preserves participant/outcome identity, requires cross-market proposition evidence, and invalidates older review fingerprints. |
| In-app alert evaluation | **CONNECTED; HOSTED WORKER REQUIRED** | The worker evaluates supported enabled rules for newly persisted strict opportunities, deduplicates events, observes cooldowns, and withdraws events when their opportunity ceases to qualify. |
| Browser refresh | **WORKING** | Live dashboard, scanner/detail, alert, and provider-health queries refresh every 15 seconds while the page is visible and refetch on focus. Demo data does not poll. |
| Public provider retrieval | **VERIFIED AT THE RECORDED PROBE TIME** | On 2026-10-20, the actual Kalshi, Polymarket International, and Polymarket US adapters returned HTTP 200 for bounded public event/market requests. This does not demonstrate a complete provider-backed strict opportunity. |
| Public provider ingestion to PostgreSQL | **VERIFIED AT THE RECORDED WORKER RUN** | At 2026-10-20 17:55:59–17:56:01 UTC, one real worker run persisted 100 events and 100 markets from each of the three public providers with zero rejected records and two HTTP requests per provider. No evaluable strict markets, opportunities, or alerts resulted. |
| The Odds API retrieval | **FIXTURE TESTED; CREDENTIALED PROBE REQUIRED** | No paid/keyed probe is recorded here. An authorized key, official sport keys, and quota remain required. |
| The Odds API strict execution support | **INDICATIVE ONLY** | Periodic aggregator odds normally do not prove accepted stake or executable depth. Those rows can support transparent theoretical research but cannot pass the strict executable gate. |
| Desktop connection modes | **IMPLEMENTED; DEPLOYMENT AND WINDOWS ACCEPTANCE REQUIRED** | Local demo uses bundled services and device-local SQLite. Connection → Server settings can select a separately deployed HTTPS live web application after checking its live/authentication contract. Hosted mode starts no local API or ingestion worker. |
| Windows installer artifact | **NOT VERIFIED IN THIS AUDIT** | No installed and launched `EDGEOS Setup.exe` was produced by the Linux source verification recorded here. The Windows workflow must publish and acceptance-test the artifact. |

## Connected data flow

One live worker cycle performs this sequence:

```text
authorized provider REST poll
  -> normalized-adapter capture and validation
  -> idempotent provider event/market/outcome/quote persistence
  -> exact-evidence canonical event link and versioned market rules
  -> fresh non-demo market snapshot load
  -> fuzzy candidate nomination
  -> explicit event + settlement equivalence decision
  -> Decimal allocation and complete scenario cash-flow evaluation
  -> execution, freshness, fee, depth, and risk gates
  -> cross-market match audit persistence
  -> strict active opportunity + legs + outcomes + history persistence
  -> scoped withdrawal and quote/market expiry
  -> owner-scoped in-app alert evaluation
  -> live API read path and 15-second foreground UI refresh
```

The worker records provider health and emits a structured
`post_ingestion_cycle_complete` report with loaded/snapshot/candidate/evaluation,
theoretical/strict/persisted/withdrawn/expired, alert, rejection, and issue
counts. A provider poll failure remains visible in health/run telemetry. The
outer worker loop retries failed cycles with bounded exponential backoff; it does
not replace failed live data with demo data.

The supervised loop retains adapter instances across cycles, preserving their
cache, health, and retry state. A positive `Retry-After` creates an adapter-wide
monotonic cooldown; values above four seconds stop the current retry rather than
holding the request open. Further calls, including other routes and cached
lookups, issue no HTTP request until that deadline. This cooldown lasts for the
adapter instance and is not persisted across process restarts.

Request budgeting also uses persisted
`ingestion_runs` from the trailing hour: a run reserves its maximum retry
attempts before polling and records actual HTTP attempts when it finishes.
Unfinished reservations remain counted after a restart. A budget pause still
runs the opportunity lifecycle cycle. A failed provider poll records failed
health and immediately withdraws opportunities that depend on that provider,
even if an earlier discovery request in the same poll succeeded.

The capture stored by the current ingestion layer is a stable, versioned
`normalized_adapter_contract` envelope rather than a byte-for-byte copy of the
upstream HTTP response. Provider observation, fetch/receipt, and ingestion times
remain separate.

## What may become an active strict opportunity

The database loader first requires an open, non-demo market with all open
outcomes represented by current `streaming` or `polled` quotes. The quote must:

- contain an upstream/provider observation timestamp rather than a substituted
  fetch timestamp;
- be no later than the evaluation time and no older than the configured limit;
- have a non-expired `stale_at` value;
- retain provider, market, outcome, currency, source, and provenance identity.

The normalizer and matcher then require, without guessing:

- a materialized canonical event and the same canonical event ID for every
  selected leg;
- sport, league, home participant, away participant, scheduled start, provider
  event/market IDs, market type, period, line and unit where applicable;
- exact participant/outcome identity after case, whitespace, and Unicode
  composition normalization. Candidate aliases, accent removal, and FC/CF token
  deletion cannot establish equivalence;
- a shared explicit `proposition_id` for automatic cross-market Yes/No and
  non-winner matching. Identical Yes/No labels do not identify the question.
  A market can establish its own proposition through the same provider/native
  market ID; that identity is included in its fingerprint when no shared
  proposition has been resolved. The persistence mapper reads only explicit
  `canonical_proposition_id` metadata for the shared ID, never the title or an
  unrelated provider market ID;
- compatible orientation, complete distinct outcomes, overtime/shootout,
  tie/draw, push/refund, void/cancellation/postponement,
  retirement/abandonment, and resolution-source semantics;
- current rule fingerprints, with a persisted operator override accepted only
  when its engine version, fingerprints, and source evidence still match;
- mutually exclusive and collectively exhaustive outcome evidence;
- explicit payout vectors for every modeled scenario, including supported push,
  void, cancellation, postponement, retirement, and abandonment cash flows.

Moneylines can have no numeric threshold or line unit; the matcher does not
invent a zero line to fill that absence. Different native line markets still
need their material line evidence. Explicit two-/three-way structure and
separate-draw rules must agree with the outcome set. The pipeline independently
rejects omitted refund/loss scenarios even if a snapshot claims completeness,
and validates normal-outcome and refund/loss payouts against the quote and rule.

The financial/execution gate additionally requires:

- a current live origin (`live_poll`, `live_stream`, or `realtime_stream`), a
  provider timestamp, source URL, connected provider health, and acceptable
  cross-leg timestamp skew;
- executable order-book asks for prediction-contract purchases. The engine
  contract can represent a direct sportsbook offer, but the connected worker has
  no direct sportsbook source and marks aggregator decimal odds indicative;
  `indicative`, cached, historical, and demo prices never receive executable
  support;
- known quantity increment/minimum, observed maximum quantity or ask depth, and
  enough depth for the configured minimum;
- a verified, source-linked, effective-dated, supported fee schedule. Unknown or
  expired material fees are not treated as zero. The calculation supports the
  explicit `ceiling`, `floor`, `half_up`, and `half_even` rounding modes.
  The mapper preserves that mode and rejects malformed/non-finite numeric
  parameters, invalid rounding increments, and unsupported modes rather than
  substituting a free fee or default increment;
- one shared explicit currency. Transfer delay, withdrawal friction, FX, tax, and
  jurisdiction costs are not discovered automatically; if material and not
  represented in the supplied cost evidence, the operator must reject the row;
- a feasible discrete allocation within capital and provider/depth caps;
- complete scenario evaluation after fee rounding and configured slippage, with
  a strictly positive worst-case net result.

The pure engine can retain a rejected result as `theoretical` for transparent
diagnostics. The post-ingestion writer persists only
`executable_quote_supported` results as active scanner opportunities. It also
records `execution_is_not_guaranteed=true`: current evidence cannot ensure that
multiple legs will remain available or fill simultaneously.

Ranking cannot bypass a hard gate. Optional measured fill probability, update
frequency, quote stability, and lockup inputs improve the transparent ranking
breakdown; leaving those measurements unset does not fabricate them.

The worker defaults `EDGEOS_PIPELINE_SLIPPAGE_BPS` to `10` (0.10%). This is a
recorded adverse-price assumption, not a measured fill estimate. Selection and
calculation use the greater of that configured floor and an offer's existing
slippage; source offers remain unchanged. The pure engine's default remains zero
for callers that supply their own assumptions.

## Lifecycle and alert behavior

After evaluations have been persisted successfully, the cycle:

1. invalidates previously active opportunities in the evaluated market scope
   that are absent from the new strict result;
2. marks records stale or expired when quote/market expiry is reached;
3. writes lifecycle snapshots for audit;
4. withdraws active in-app alert events tied to invalidated, stale, or expired
   opportunities;
5. evaluates supported rules only for the strict opportunity IDs persisted in
   the current cycle.

An empty evaluable input expires already-stale records but deliberately does not
perform a broad absence withdrawal. Persistence failures propagate before the
absence-withdrawal step, so a failed write cannot silently invalidate the prior
feed. Idempotency keys make retries safe at the match/opportunity/snapshot layer.
Provider-failure withdrawal is separate and explicitly scoped to the failed
provider; it does not depend on receiving a new evaluable snapshot.
The default verified scanner excludes stale rows immediately; their detail and
history remain readable. Restoration records a new active lifecycle snapshot
even when the executable quotes are unchanged.

Supported live alert types are `new_verified_opportunity` and the UI's
`net_roi_above` rule (stored as `roi_threshold`).
Configured criteria are conjunctive and can include minimum conservative net
profit, minimum net ROI (stored as a ratio), minimum known liquidity, and maximum
quote age. The evaluator recognizes provider, market-type, sport, and league
condition keys, but the current rule DTO/editor does not expose them. Other
displayed alert types are persisted configuration only and do not emit events in
this release. Alert/opportunity keys deduplicate repeated cycles; cooldowns limit
repeated triggers. A withdrawn event can reactivate if the strict opportunity
becomes valid again. Withdrawn records remain durable audit evidence but are
omitted from the active user event list.

Delivery is currently the owner-scoped in-app event table and UI only. There is
no Windows native notification, email, SMS, mobile push, webhook, or real-money
action. Foreground UI refresh is presentation, not ingestion: the hosted worker
must continue running even when every browser is closed.

## Provider implications

- **Kalshi, Polymarket International, and Polymarket US:** bounded public
  event/market retrieval succeeded on 2026-10-20. The adapters are
  separate read-only products and ID spaces. A list-market response can supply
  usable best ask/size evidence only when the provider actually returns it and
  all event, rule, fee, timestamp, source, and scenario evidence also passes.
  Public access does not establish eligibility to trade.
- **The Odds API:** requires the deployment-level key and explicit official sport
  keys. Bookmakers are only those returned by the authorized aggregator. Its
  periodic prices remain indicative because the current integration does not
  prove accepted stake/depth; it should not create a strict active row.
- **Individual sportsbooks:** there is no claimed direct DraftKings, FanDuel,
  BetMGM, Caesars, bet365, Fanatics, or ESPN BET integration. Their labels can
  appear only when the aggregator returns those bookmaker keys.
- **Demo:** deterministic fixtures are isolated from live loading and never
  substitute for a provider failure.

The current worker uses public adapters plus the deployment-level
`THE_ODDS_API_KEY`. It does not schedule collection from per-user credentials
saved in Settings. The authenticated connection test is bounded and useful for
diagnosis, but one success is not continuous ingestion or proof of entitlement.

## Runtime controls

These are the live worker controls read by the current source:

| Variable | Meaning |
| --- | --- |
| `EDGEOS_MODE=live` | Enables live API/worker behavior; the web must also use `NEXT_PUBLIC_APP_MODE=live`. |
| `DATABASE_URL`, `DATABASE_SSLMODE` | psycopg connection and TLS mode for ingestion, pipeline persistence, private state, and API reads. |
| `INGESTION_POLL_SECONDS` | Requested worker cadence; the process enforces a 15-second minimum and defaults to 60 seconds when unset. |
| `INGESTION_REQUEST_BUDGET_PER_HOUR` | Per-provider budget checked against both requested cadence and persisted trailing-hour attempts/reservations. `INGESTION_<PROVIDER_CODE>_REQUEST_BUDGET_PER_HOUR` overrides one provider. |
| `THE_ODDS_API_KEY` | Server-only deployment key for The Odds API worker. |
| `THE_ODDS_API_SPORTS` | Comma-separated official sport keys. Empty means no Odds API requests and no quota consumption. |
| `QUOTE_STALE_AFTER_SECONDS` | Primary maximum quote age/stale interval. If unset, `EDGEOS_MAX_QUOTE_AGE_SECONDS` is accepted; the code fallback is 30 seconds. The checked-in `.env.example` explicitly sets 90 seconds. |
| `EDGEOS_PIPELINE_TOTAL_ALLOCATION` | Simulated allocation evaluated per candidate; default `1000`. This is research capital, not a wagering recommendation. |
| `EDGEOS_MAX_QUOTE_SKEW_SECONDS` | Maximum cross-leg provider-time skew; default `5`. |
| `EDGEOS_PIPELINE_MINIMUM_LIQUIDITY` | Minimum observed executable allocation/depth; default `1`. |
| `EDGEOS_PIPELINE_SLIPPAGE_BPS` | Non-negative adverse-price floor; default `10` bps. Recorded as an assumption and never reduces a larger offer-level slippage input. |
| `EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY` | Optional measured input in `[0,1]`; omitted means unknown, not zero. |
| `EDGEOS_MEASURED_QUOTE_UPDATE_HZ` | Optional non-negative measured update frequency. |
| `EDGEOS_MEASURED_QUOTE_STABILITY` | Optional measured input in `[0,1]`. |
| `EDGEOS_EXPECTED_LOCKUP_SECONDS` | Optional non-negative modeled settlement lockup. |

Deploy the worker as a continuously supervised process, not a browser task.
Monitor the lease/heartbeat, cycle completion, provider health and timestamps,
request budgets/429 backoff, load issues, rejection counts, strict survival,
withdrawals/expiry, and alert create/deduplicate/filter counts. A running API or
visible browser alone does not constitute monitoring.

## Recorded verification evidence

The final combined backend run on 2026-10-20 completed **290 passed**, including
the actual PostgreSQL acceptance test and its immediate scanner withdrawal and
restored active-history assertions. Ruff passed and mypy passed for 37 source
files. The focused counts below describe narrower earlier checks.

The v3 matching and financial hardening pass ran:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/edgeos-uv-cache uv run pytest \
  tests/test_matching.py \
  tests/test_arbitrage.py \
  tests/test_opportunity_pipeline.py
```

Result on 2026-10-20: **76 passed**. Ruff and mypy also passed on
`matching.py`, `normalization.py`, `domain.py`, `arbitrage.py`, and
`opportunity_pipeline.py`. This is a focused result, not the final whole-repository
acceptance count.

The orchestration/persistence regression command is:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/edgeos-uv-cache uv run pytest \
  tests/test_opportunity_pipeline.py \
  tests/test_post_ingestion_cycle.py \
  tests/test_pipeline_persistence.py \
  tests/test_live_pipeline_recorded_integration.py \
  tests/test_ingestion.py \
  tests/test_worker.py
```

Final combined verification of that command is recorded separately when the
integration pass finishes; the 76-test result above covers only the stated
matching/arbitrage/pipeline files.

Those fixtures verify, among other cases:

- recorded normalized records reaching strict calculation, opportunity
  persistence, and alert evaluation;
- fuzzy nomination followed by explicit verified/review-required/rejected
  settlement classification; FC/CF and alias collisions cannot verify identity;
- missing/mismatched cross-market proposition IDs, native-market identity
  fingerprint changes, and inconsistent outcome structures;
- an overtime-rule mismatch and unknown settlement evidence being rejected;
- missing or mismatched canonical event links blocking active persistence;
- fees turning a gross-positive price difference into a negative full-scenario
  result;
- missing or inflated refund cash flows, including postponement, retirement,
  and abandonment, preventing a false strict-profit classification;
- discrete prediction-contract sizing, fee rounding, known depth, and supported
  order-book ask evidence; all four rounding modes and explicit conservative
  slippage without source mutation;
- stale, future/skewed, provider-timestamp-unknown, disconnected, indicative,
  cached, historical, and demo quotes staying outside the strict feed;
- incomplete outcome/scenario coverage and missing executable depth failing
  closed;
- scoped opportunity withdrawal plus alert withdrawal/reactivation on a recorded
  disconnect/reconnect sequence;
- operator-review engine-version and material-fingerprint invalidation;
- persistence SQL contracts for idempotent match/opportunity/history writes,
  lifecycle updates, alert thresholds, cooldowns, and deduplication.

The provider cooldown/failure-health pass completed **10 focused tests** and
Ruff. It covered long `Retry-After` deadlines, cross-route/cached-call blocking,
recovery at expiry, actual attempt counts, HTTP-date parsing, non-finite retry
values, and a later failure overriding earlier successful-request health.

The recorded integration suite subsequently completed **6 tests** after the
fee mapper began preserving rounding modes. Its fee-erasure fixture explicitly
uses ceiling rounding; a separate fractional-order regression confirms that
half-up can legitimately round a sub-half-cent fee to zero under the supplied
synthetic contract.

The foreground refresh contract was also run directly:

```bash
npm test --workspace=@edgeos/web -- --run lib/live-refresh.test.ts
```

Result on 2026-10-20: **2 passed**. It verifies a 15-second visible-live cadence,
focus refetch, no background interval, and no automatic demo polling.

The real local web/API browser workflow was rerun on 2026-10-20:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
UV_CACHE_DIR=/tmp/edgeos-uv-cache \
npm run test:e2e --workspace=@edgeos/web
```

Result: **5 passed, 1 expected skip**. The new paper workflow uses synthetic
demo inputs against running Next.js and FastAPI: `$210` at odds `2.20`/`2.00`,
partial and complete simulated fills, then `$10` realized only on settlement;
browser reload reads the saved server state. This does not establish durability
across a server restart, production-provider data, or an installed desktop
runtime. The loopback-origin regression that had rejected legitimate mutations
was corrected before this run.

The wider web pass also completed **131 unit/component tests**, lint,
TypeScript checking, and a production Next.js build.

The actual adapters also completed bounded, unauthenticated public probes at
**2026-10-20 17:46:34–17:46:35 UTC**:

| Provider | Requests | Result | Returned evidence |
| --- | --- | --- | --- |
| Kalshi | `list_events(limit=2)` and `list_markets(limit=2)` | HTTP 200; 2 events, 2 markets | Provider timestamps known for both markets; 2 outcome ask sizes known. |
| Polymarket US | `list_events(limit=2)` and `list_markets(limit=2)` | HTTP 200; 2 events, 2 markets | Provider timestamps known for both markets; no outcome ask sizes supplied. |
| Polymarket International | `list_events(limit=2)` and `list_markets(limit=2)` | HTTP 200; 2 events, 2 markets | Provider timestamps known for both markets; no outcome ask sizes supplied. |

These were public read-only retrievals: no authentication, paid API quota, or
orders were used. They establish retrieval and normalization at that time, not
continuous availability, complete fee/rule/depth evidence, or a provider-backed
strict opportunity. The Odds API was not part of this probe.

At **2026-10-20 17:55:59–17:56:01 UTC**, a subsequent actual public worker cycle
persisted **100 events and 100 markets per provider** for Kalshi, Polymarket
International, and Polymarket US in PostgreSQL, using two HTTP requests per
provider and recording **zero rejected records**. The Odds API was skipped
because no sport/key was configured. This verifies current public retrieval
through real database ingestion. The post-ingestion report was
`no_evaluable_markets`: **0 loaded evaluable markets, 0 opportunities, 0 alerts**.
No missing execution evidence was replaced to make those records qualify.

The recorded integration suite uses normalized fixtures and an in-memory
persistence double; persistence unit tests use controlled database cursor
doubles. Separately, on 2026-10-20, the opt-in
`tests/test_postgres_live_pipeline.py` passed against real PostgreSQL 16 after
all four migrations and the local Auth test prelude were applied. Schema/RLS
assertions also passed on the local database.

That test uses the actual ingestion, pipeline, and research repositories and the
FastAPI ASGI scanner route. It covers provider request-budget reservations and
health SQL, raw adapter captures, canonical event links, versioned rules and
quotes, a missing-canonical-link rejection, Decimal matching/calculation,
opportunity/leg/outcome/history writes, alert deduplication, provider disconnect,
withdrawal, reconnect, and alert reactivation. Fixture URLs, rule text, and
metadata explicitly mark the evidence synthetic; source-linked zero fees are
fixture evidence only. It neither fetches a provider nor proves a production
strict opportunity.

To run it against a migrated disposable local database:

```bash
cd apps/api
EDGEOS_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/edgeos_test \
  .venv/bin/pytest tests/test_postgres_live_pipeline.py
```

The test skips without the variable, requires the exact database name
`edgeos_test` and a loopback address, and rolls back all fixture writes. It never
truncates tables or deletes existing data. CI runs this test in its database job.
This establishes local SQL integration; hosted Supabase authentication/RLS and
the target production deployment still require independent acceptance.

## Desktop hosted-live boundary

The desktop's native **Connection → Server settings** accepts an HTTPS web
origin, not an API path or provider credential. It checks
`/api/edgeos/api/v1/health` for matching live-mode headers, the API identity,
Supabase authentication, and paper-only execution, then saves the selection and
restarts. Each hosted origin has a separate persistent browser session. A failed
connection offers retry/settings/local-demo/quit choices and does not silently
replace live data with demo data.

Hosted mode loads the deployed web UI and starts neither bundled API/web
services nor an ingestion worker. Local demo data remains on the device; it is
not uploaded or merged into the hosted account. Switching to local demo does not
erase a saved hosted sign-in. This mode-contract check does not establish worker
health, provider availability, quote freshness, or a non-empty strict scanner.
Windows installation and hosted login/reconnect acceptance remain separate.

## Not established by this audit

The following require separate, dated deployment evidence:

- an authorized The Odds API request using the deployment's key and budget;
- provider uptime, plan access, schema stability, official fee coverage,
  executable order-book depth, accepted sportsbook stake, or simultaneous fills;
- at least one real provider snapshot surviving every strict gate; an empty
  scanner may be the correct production result;
- applying all migrations and exercising the full transaction/RLS path against
  the target Supabase Postgres project;
- a supervised multi-cycle soak test covering real 429s, disconnects, restarts,
  quota reset, retention, backup, and restore;
- external notification delivery (none is implemented);
- a built, downloaded, installed, launched, signed, and uninstalled Windows
  `EDGEOS Setup.exe`.

For production acceptance, apply every migration, start one supervised worker,
observe multiple successful `post_ingestion_cycle_complete` records, trace any
strict row back to exact provider IDs/timestamps/rules/fees/depth and every
scenario payout, verify alert create/deduplicate/withdraw behavior, and record
the provider/date/result without secrets. Do not populate the scanner with demo
rows or weaken evidence gates merely to make it non-empty.
