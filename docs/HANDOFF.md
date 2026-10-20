# EDGEOS product, engineering, and operating handoff

Last source audit: 2026-10-20  
Audience: product owner, reviewer, operator, and the next engineering team

## Read this first

EDGEOS is a read-only sports-market research and simulated paper-trading system.
It does not place a wager, submit an exchange order, hold user funds, confirm that
a sportsbook will accept a stake, or establish anyone's legal eligibility to use
a provider. The appropriate real-money allocation for this release is **$0**.

This document describes the code in this repository. It is not a sales roadmap,
and it does not convert a schema, user interface, or mocked adapter test into a
claim of live operation.

- `DEMO` means deterministic sample data, visibly labelled as such.
- `WORKING` means a connected implementation can be exercised without inventing
  an external dependency.
- `NEEDS CONFIGURATION` means code exists but needs credentials, migrations,
  deployment, or a supported upstream response.
- `UNAVAILABLE` means the workflow is absent or intentionally disabled.
- `TESTED WITH FIXTURES` means deterministic official-shaped responses were used;
  it is not a successful request to a current production provider.

Provider access, schemas, prices, terms, entitlements, and uptime can change. The
runtime health, persisted source evidence, and timestamps are the operational
source of truth.

## Executive capability status

| Area | Status | Honest scope |
| --- | --- | --- |
| Responsive application shell | **WORKING** | Premium dark UI, collapsible desktop navigation, mobile drawer, loading/error/empty states, keyboard scanner rows, accessible dialogs, and chart tables. |
| Demo research workspace | **WORKING / DEMO ONLY** | Deterministic, visibly labelled fixture; it is not a current market. |
| Runtime mode isolation | **WORKING** | Web/API mode headers are checked. Disagreement blocks results. Live paths do not substitute demo prices or performance. |
| Supabase authentication | **NEEDS CONFIGURATION** | Email/password signup, confirmation, sign-in/out, reset/update, SSR session refresh, optional Google OAuth, profile, export, and deletion paths exist. |
| Cross-device profile/preferences | **NEEDS CONFIGURATION** | Stored through Supabase under owner-scoped RLS after migrations. |
| Public provider adapters | **PUBLIC RETRIEVAL VERIFIED; STRICT PIPELINE ACCEPTANCE PENDING** | Actual Kalshi, Polymarket International, and Polymarket US adapters completed bounded HTTP 200 event/market requests on 2026-10-20. The Odds API remains fixture-tested pending an authorized keyed probe. |
| Settings → API Connections | **WORKING / NEEDS CONFIGURATION** | Live authenticated add/test/replace/disable/delete flows use the same-origin BFF and owner-scoped Postgres rows. Secrets are write-only and AES-256-GCM encrypted. Postgres, migrations, the credential key, and provider access are required. |
| Durable provider-to-opportunity pipeline | **WORKING / NEEDS CONFIGURATION AND CURRENT EVIDENCE** | Live worker uses a DB lease, budgets, run/health records, versioned captures, idempotent canonical/market writes, strict matching/calculation, opportunity lifecycle, and in-app alert evaluation. It needs Postgres/migrations and an always-on process. |
| Live consolidated scanner | **WORKING FAIL-CLOSED / LIVE PROVIDER RUN NOT DEMONSTRATED HERE** | Live mode reads worker-produced strict opportunities with server filters/sort/paging. Missing canonical, rule, fee, provider-time, depth, executable-basis, currency, or scenario evidence correctly leaves the feed empty. |
| Strict settlement matching | **WORKING; V3 REGRESSION TESTED** | Fuzzy aliases only nominate candidates. Strict identities, cross-market proposition evidence, outcome structure, applicable line, and settlement checks classify them; v3 invalidates older review fingerprints. |
| Settlement review queue | **NEEDS CONFIGURATION** | Markets includes an evidence/audit UI backed by versioned DB/API decisions and rule-change invalidation; writes require an allowlisted reviewer. Demo correctly shows an empty queue. |
| Decimal cash-flow engine | **WORKING** | Versioned all-outcome discrete sizing with costs, increments, caps, depth, evidence gates, break-even, and stress outputs. |
| Scanner/detail drawer | **WORKING IN DEMO; LIVE NEEDS DATA** | Compact feed, filters, sorting, provenance, assumptions, outcome P&L, failure reasons, history, and paper workflow. |
| Saved/favorite opportunities | **WORKING** | Scanner actions use the owner-scoped API. Installed Windows demo/local saves persist in device-local SQLite; ordinary development demo saves remain process memory; configured live saves use Postgres. Failure never creates a browser-only bookmark. |
| Edge Lab / calculator | **WORKING FOR RESEARCH** | Calls the Python authoritative simulator for two- or three-way decimal, American, fractional, and binary-contract inputs, caps, fees, slippage, and auto-balanced stakes. Manual inputs remain `REVIEW REQUIRED`; the calculator cannot self-certify equivalence or official provider fees. |
| Paper portfolio | **WORKING / LIVE NEEDS CONFIGURATION** | Installed Windows demo/local records persist in SQLite; ordinary development demo uses process memory. Configured live mode uses owner-scoped transactional Postgres for bundle creation, fills, partial-fill exposure, settlement, cancellation, notes, balances, ledger export, and analytics. API failure is visible; there is no fabricated browser fallback. |
| Watchlist/alert-rule/settings editing | **WORKING / LIVE NEEDS CONFIGURATION** | Installed Windows demo/local state persists in SQLite; ordinary development demo uses process memory. Configured live mode uses owner-scoped Postgres. Canonical watch targets reject unknown/ambiguous values; retry keys protect creates. |
| Alert evaluation/in-app delivery | **WORKING / NEEDS LIVE DATABASE AND CONTINUOUS WORKER** | Supported rules are evaluated against newly persisted strict opportunities with conjunctive thresholds, deduplication, cooldown, and withdrawal lifecycle. Delivery is an owner-scoped in-app record only; no email/SMS/push/native Windows notification exists. |
| Analytics/dashboard summary | **WORKING / LIVE NEEDS CONFIGURATION** | Date-bounded metrics use recorded demo-ledger activity or the configured durable paper repository. The live dashboard joins durable paper summary with persisted opportunity counts and fails visibly if a repository is unavailable. |
| Backtesting | **WORKING BASELINE** | Stateless timestamp-ordered replay models delay, expiry, slippage, partial fills, failure, and drawdown. Input `conservative_profit` must already reflect fees; replay has no separate fee parameter. |
| Desktop connection modes | **IMPLEMENTED; HOSTED DEPLOYMENT REQUIRED** | Local demo uses bundled Next/FastAPI and device-local SQLite. Native Connection settings can verify and open a separately deployed HTTPS live web application in its own session. Hosted mode starts no local API or worker. |
| Windows installer | **BUILD PATH PRESENT; INSTALL/LAUNCH ACCEPTANCE NOT ESTABLISHED HERE** | Electron packages Next standalone + PyInstaller API into x64 NSIS on Windows. Release artifact and installation evidence must be reported separately from Linux source/browser tests. |
| Real-money execution | **INTENTIONALLY UNAVAILABLE** | No order placement, automated wagering, sportsbook login, funding, or withdrawals. |

## End-to-end workflows

### Labelled demo research to settlement

1. Run web and API with both modes set to `demo`.
2. Dashboard shows the demo badge and ledger-backed summary.
3. Open Arbitrage Scanner and inspect the deterministic two-leg reference case.
4. Search, filter, sort, change phase, and paginate through the API contract.
5. Open the row drawer to inspect source, times, origin, settlement audit, each
   leg and outcome, fees, liquidity, assumptions, failure reasons, and history.
6. Create a paper position with an exact simulated allocation and notes.
7. Record each fill with quantity, price, fee, and retry key.
8. Observe unmatched exposure when only one leg or a partial quantity is filled.
9. Settle or cancel. Only settlement ledger entries become realized P&L.
10. Review date-filtered Analytics or export portfolio CSV.

In the ordinary web/API development workflow, demo private state belongs to the
running API process and resets on restart. In the installed Windows application,
the same demo-labelled paper/workspace workflow is stored in device-local SQLite
and survives close/reopen. Neither mode syncs those local records to another
device. A portfolio API failure renders an explicit error; it does not silently
display invented positions or export them.

### Configured live research pipeline

1. A supervised worker obtains the `market-ingestion` lease and polls each
   deployment-configured read-only adapter within its request budget.
2. It records provider health/run telemetry, validates the adapter envelope, and
   idempotently persists provider IDs, exact canonical identity evidence, rules,
   outcomes, quotes, origin, source, and distinct provider/fetch/ingestion times.
3. The post-ingestion cycle loads a bounded current evidence set. Records with
   missing event identity, scenarios, provider time, rule/fee/depth evidence, or
   supported execution semantics are reported as issues rather than repaired
   with assumptions.
4. Fuzzy similarity nominates cross-provider pairs; explicit event, proposition,
   outcome, period, applicable line, overtime/draw/push/void, and resolution checks decide
   `VERIFIED`, `REVIEW REQUIRED`, or `REJECTED`. Same-market complementary
   contracts are also evaluated where complete.
5. For verified candidates the Decimal engine selects feasible discrete
   quantities, applies fees/rounding/slippage/capital/depth limits, and calculates
   cash flow in every declared settlement scenario.
6. The worker persists cross-market audit decisions and only active
   `executable_quote_supported` opportunities: all legs must share the canonical
   event, use current provider-timestamped source-linked executable quotes, and
   remain strictly positive in the worst modeled scenario. Theoretical results
   stay outside the active feed.
7. Only after successful writes does the cycle withdraw absent opportunities,
   expire stale/closed rows, withdraw related alert events, and evaluate supported
   user alert rules for strict IDs. Empty evaluable input never triggers a broad
   absence withdrawal.
8. The live UI reads persisted data; while visible it refreshes every 15 seconds
   and refetches on focus. It never calls providers directly. A stale or withdrawn
   detail remains audit-only and cannot create a paper position.
9. The user may save a current row or create a simulated bundle. That action does
   not place an order, reserve real provider capacity, or convert modeled profit
   into realized P&L.

This complete connection has deterministic recorded-fixture coverage and a
passing real PostgreSQL 16 end-to-end test using explicitly synthetic evidence.
That test exercises actual ingestion, canonical links/rules, quotes, pure
matching/math, opportunity persistence, alerts, the FastAPI scanner DTO, provider
withdrawal, and reconnect/reactivation. Separate
bounded public provider probes succeeded, but no real provider snapshot has yet
been demonstrated through every strict gate and active persistence step in this
audit. A live deployment may correctly show zero rows.

Provider failures also have a separate immediate withdrawal path scoped to that
provider. A successful event-discovery request followed by failed market
discovery cannot leave the provider marked connected or retain its active
opportunities solely because earlier quotes are still fresh.

### Desktop local demo or hosted live

The native **Connection → Server settings** dialog accepts the HTTPS origin of
an existing EDGEOS web deployment. **Verify, save & restart** checks the public
web/API live-mode and authentication contract, saves the origin, and restarts
into the hosted web application. Sign in there with the hosted account. **Use
local demo** explicitly returns to the bundled offline demo workflow.

Hosted mode starts no bundled API/web services and no local ingestion worker.
Its backend, authentication, database, and supervised worker must already exist.
The local SQLite workspace is preserved and is not uploaded or merged into the
hosted account. Separate persistent browser sessions isolate each hosted origin;
switching back to local demo does not sign out of those hosted sessions.

An unavailable or invalid hosted connection offers retry, settings, local-demo,
or quit choices. It never silently shows demo opportunities as live. The health
check establishes the mode/authentication contract only; it does not prove
provider availability, worker progress, quote freshness, or strict opportunities.
Installed Windows login/reconnect behavior still needs acceptance evidence.

### Edge Lab calculation

1. Enter capital, prices, fees, slippage, and size caps. Manual offers deliberately
   carry no provider quote timestamp.
2. The client validates and sends the request to `POST /api/v1/simulate`.
3. The Python Decimal engine chooses feasible discrete quantities.
4. The response displays requested/deployed/unallocated capital, each leg, every
   outcome, fees, gross/net ROI, minimum/maximum result, unknown age/skew, lockup,
   break-even movement, unmatched exposure, version, and rejection gates.
5. Treat it as research: manual input contains no operator-reviewed settlement
   evidence and therefore cannot enter the strict verified feed.

### Provider event/market browsing

In packaged/demo mode, Markets browses only the unmistakably synthetic demo
adapter and displays provider/product, origin, IDs, sources, state/times,
outcomes, price kind, available quote fields, and rule evidence. Unknown values
remain unknown. In live mode, user-triggered direct provider polling is disabled:
it would bypass the centralized worker cache and let browser traffic exhaust
shared quotas. The adapters are exercised by the worker and authenticated
connection test, but a persisted-snapshot Markets read surface is not connected
in this release. No universal interactive book or sportsbook stake acceptance
is claimed.

### Authenticated web deployment

After migrations and environment setup, users can create and verify accounts,
keep cookie-backed sessions, reset/update passwords, edit profile/preferences,
export RLS-visible data, and delete an account after recent authentication. The
API validates Supabase tokens using project signing metadata.

Live paper, watchlist, alert, settings, saved-opportunity, analytics, and dashboard
routes use configured Postgres repositories. They require applied migrations,
`DATABASE_URL`, and a valid authenticated subject, and fail closed rather than
falling back to demo or browser-local state. The separately deployed worker
evaluates supported alert rules and stores in-app events; this does not provide
email/SMS/push delivery, and it is not running merely because the API or browser
is open.

### Provider connections

Settings → API Connections supports:

- The Odds API key in a password field;
- credential-free public Kalshi, Polymarket International, and Polymarket US;
- selected sports, regions, markets, polling interval, and hourly request budget;
- add/replace, test, enable/disable, and delete;
- masked secret status only—never retrieval of the original value;
- last test, last success, last market update, coverage/capabilities, quota, and
  categorized actionable errors.

The FastAPI routes require live mode, an authenticated session, Postgres,
migrations, and a valid server-side credential master key. A saved connection is
not proof that the provider is reachable: the explicit test action performs one
bounded, no-retry read-only check and reports a safe categorized result. The web
route is a bounded same-origin proxy to allowlisted internal paths, not a
user-controlled URL fetcher. The ingestion worker does not yet consume these
per-user configurations; it uses public adapters and the deployment-level The
Odds API key.

## Architecture and trust boundaries

```text
Browser
  ├─ Supabase SSR/browser client ──> Auth + RLS profile/preferences
  └─ same-origin /api/edgeos BFF ──> FastAPI
                                      ├─ Decimal matching/ranking/cash flow
                                      ├─ read-only provider adapters
                                      ├─ Postgres public research repository
                                      ├─ Postgres private/paper repositories
                                      ├─ encrypted provider connections
                                      └─ demo ledger/private store
                                           ├─ process memory (ordinary development)
                                           └─ SQLite (packaged Windows Local Mode)

Worker ──> provider adapters ──> normalized-contract capture + canonical
                                   event/market/outcome/quote + health rows
       └──> strict match/calculation ──> opportunity lifecycle
                                   └──> owner-scoped in-app alert events

Windows Electron
  ├─ local-demo: ephemeral-loopback Next + bundled FastAPI
  │    └─ %APPDATA%\EDGEOS\data\edgeos-local.sqlite3 (private simulated state)
  └─ hosted-live: verified HTTPS web origin + origin-specific browser session
       └─ existing hosted web/API/Auth/database/worker; no local services
```

The browser does not call market providers directly. This centralizes limits,
normalization, provenance, and secrets. Public research data can be shared;
profile, settings, provider-connection metadata, credentials, paper records,
saved items, notes, and alert configuration are user-scoped.

### Runtime mode contract

- API: `EDGEOS_MODE=demo|live`.
- Web: `NEXT_PUBLIC_APP_MODE=demo|live`.
- Requests carry `X-EDGEOS-Client-Mode`.
- Responses carry `X-EDGEOS-Mode` and `X-EDGEOS-Mode-Contract: 1`.
- Disagreement produces HTTP 409 and the UI blocks the result.
- Missing live data produces an empty/error state, never demo profit.

This is a data-integrity control, not only a badge.

In live mode the web polls persisted scanner, dashboard, markets/health, and
alert data every 15 seconds only while visible and refetches on focus. Demo mode
does not perform this live polling. The UI preserves stale/withdrawn rows as
audit-only and disables paper-position creation from them. This cadence never
calls providers directly and is not evidence that the worker is running.

## Data provenance and modes

| Field/concept | Meaning |
| --- | --- |
| Provider/product | Exact upstream product; US and International Polymarket remain distinct. |
| Origin/feed class | Demo, current REST poll, cached, or historical/replay. |
| Provider/observation time | Time the provider says the quote applied, if supplied. |
| Fetch/received time | When EDGEOS obtained the response; it cannot make an old quote fresh. |
| Ingestion time | When validation/persistence completed. |
| Quote age | Derived from provider/observation time; missing stays `null`. |
| Depth/size | Executable size evidence if supplied; missing is neither zero nor infinity. |
| Fee evidence | Effective-dated source/assumption; unknown material cost blocks strict use. |
| Settlement evidence | Explicit rules and source; title similarity is insufficient. |
| Versions | Adapter, schema, rule fingerprint, matcher, and calculation reproduction boundary. |
| Source identity | Provider-specific event/market/outcome ID and official reference when present. |

The current worker's immutable capture is a versioned
`normalized_adapter_contract` envelope plus hash, not the byte-for-byte upstream
HTTP body. Canonical rows use provider identities and timestamps for
idempotency. Where opportunities exist, their records retain leg quotes, outcome
cash flows, assumptions, failures, and calculation version. See
`DATA_PROVENANCE.md` for detailed handling.

## Provider matrix

No row promises uptime or access. “Fixture tested” means deterministic payloads
shaped like documented interfaces.

| Provider | Implemented interface | Credential | Capabilities | Material limitation | Evidence level |
| --- | --- | --- | --- | --- | --- |
| The Odds API | `/v4` REST | API key | Sports/events, bookmaker outcomes, upstream update times, quota headers | Normally no accepted-stake evidence or exchange depth; books/regions depend on plan and response. | Fixture tested; a live probe needs the owner's key/budget. |
| Kalshi | Public Trade API v2 REST | None for public routes | Event/market discovery and public books/rules when returned | Authenticated WebSocket, private account, and trading are absent. | Fixture tested; bounded public event/market HTTP 200 probe on 2026-10-20. |
| Polymarket International | Gamma + CLOB public REST | None | Informational discovery and public CLOB prices/books | International IDs/products; no private trade, eligibility bypass, or WebSocket. | Fixture tested; bounded public event/market HTTP 200 probe on 2026-10-20. |
| Polymarket US | US Gateway public REST | None | Separate US events/markets/BBO/books where returned | Never joined by ID to International; institutional gRPC/trading absent. | Fixture tested; bounded public event/market HTTP 200 probe on 2026-10-20. |
| Demo | Local adapter | None | Deterministic reference workflow | Never current/live. | Deterministic. |
| Individual sportsbooks | Only when an authorized aggregator returns them | Aggregator dependent | Normalized bookmaker outcomes | No direct DraftKings/FanDuel/BetMGM/Caesars/bet365/Fanatics/ESPN BET API is claimed. | Not independently supported. |

Official documentation starting points used by provider descriptors:

- <https://docs.kalshi.com>
- <https://docs.polymarket.us>
- <https://docs.polymarket.com>
- <https://the-odds-api.com/liveapi/guides/v4/>

`PROVIDER_MATRIX.md` records endpoint-level scope and unknowns.

## Durable ingestion and operation

```text
official response
  -> immutable normalized-adapter capture + hash        [working]
  -> schema validation and normalization
  -> canonical event/market/outcome/quote upsert         [working]
  -> rule evidence and fingerprints                     [working; unknown fails]
  -> candidate match                                    [working]
  -> settlement classification/review                   [working]
  -> Decimal all-scenario cash-flow evaluation          [working]
  -> hard gates + transparent ranking                   [working]
  -> opportunity/leg/outcome/history persistence        [strict rows only]
  -> lifecycle + in-app alert evaluation                [working]
  -> scanner/dashboard/alerts read paths                 [working]
```

The live worker acquires a `market-ingestion` database lease, heartbeats it,
creates a versioned ingestion run, enforces conservative per-provider hourly
request estimates plus a persisted trailing-hour budget, polls events/markets,
persists idempotent provider events,
markets, outcomes and quote snapshots, updates health, finishes the run, and
releases the lease. After the provider polls, it loads a bounded evidence set,
runs the strict post-ingestion cycle, and reports loaded/evaluable markets,
candidate audits, combinations, theoretical/strict counts, persisted match and
opportunity counts, lifecycle changes, alert outcomes, rejection counts, and
issues. Provider, fetch, and ingestion times remain separate; a missing provider
timestamp is immediately stale. Its “raw” record is explicitly a canonical
`normalized_adapter_contract` capture—not the byte-for-byte upstream HTTP body.

Before polling, the run reserves the maximum HTTP retry attempts it may use.
Completed runs replace that reservation with actual attempted requests;
unfinished reservations continue to count after a worker restart. When the
budget is exhausted, polling pauses but the lifecycle cycle still runs. The
supervised loop keeps its adapter instances across cycles, preserving cache,
health, and retry state, and closes them on shutdown.
A positive `Retry-After` establishes an adapter-wide monotonic cooldown.
Deadlines above four seconds stop the current retry; later requests across
routes, including cache lookups, make no HTTP call before expiry. Adapter
cooldowns survive worker cycles, while only the database request budget survives
a process restart.

The worker does not consume per-user saved connection credentials; The Odds API
uses the deployment environment key. Database errors propagate before
absence-based withdrawal. A cycle with no evaluable markets expires already
stale records but deliberately avoids broad withdrawal. Retry is safe because
match, opportunity, and snapshot writes are idempotent.
Failed provider polls explicitly mark failed health and withdraw that provider's
dependent active opportunities before the shared cycle continues.

The candidate gate requires one shared materialized canonical event plus explicit
evidence for rules, complete outcome/scenario coverage, provider times/freshness
and skew, source, current executable price/depth, effective-dated fees, and
currency compatibility. Every required normal/push/void/cancellation/postponement/
retirement/abandonment cash-flow scenario must be covered and the minimum net
result must remain positive. Cached, historical,
demo, indicative, or incompletely evidenced results remain theoretical and are
not active strict rows. The Odds API is indicative because it normally cannot
prove accepted stake or executable depth. The worker must be supervised before
any continuous claim.

Before calling a deployment operational, confirm migrations, a single current
lease owner, renewed heartbeat, increasing raw/normalized records, deduplication,
separate provider/received/ingested times, bounded quota usage, redacted errors,
stale-source preservation, retention policy, and reproducible scanner snapshots.

## Canonical matching and settlement verification

Fuzzy text similarity may propose a candidate; it cannot verify it.

The matching engine is `edgeos.settlement.v3`. Identity comparison preserves
punctuation, accents, and meaningful tokens such as FC/CF; only case, whitespace,
and Unicode canonical composition are normalized. Candidate aliases cannot
establish participant or outcome identity.

Event checks include sport, competition, participants, home/away orientation,
scheduled-start tolerance, and retained provider IDs. Market
checks include type, complete distinct mutually exclusive outcomes, outcome
orientation, period, spread/total threshold and units, overtime/shootout,
draw/qualification, push/refund, void/cancellation/postponement,
retirement/abandonment, and resolution source/evidence.

Automatic cross-market matching of binary Yes/No and non-winner propositions
requires a shared explicit `proposition_id`; identical outcome labels do not
prove that the questions match.
The persistence mapper reads that identity only from explicit
`canonical_proposition_id` metadata, not from titles or unrelated provider IDs.
The same provider/native market establishes its own proposition for complementary
contracts. When no shared proposition is resolved, that native identity is
included in its fingerprint so changing the source market invalidates review.
Moneylines can have no numeric threshold or line unit. Identical native markets
can establish self-equivalence without those optional fields; other line-market
pairs still require their material line evidence. A three-way structure or
separate-draw rule cannot verify an outcome set that omits the required outcome.

- `VERIFIED`: every material field required for equivalence is known/compatible,
  with exhaustive outcome coverage.
- `REVIEW REQUIRED`: potentially related, but a material fact is missing or needs
  human evidence.
- `REJECTED`: a material event, outcome, orientation, period, line, or rule differs.

Only `VERIFIED` matches enter the strict feed. Rule fingerprints bind a versioned
decision to reviewed evidence. The engine-version and material fingerprint
changes invalidate older v2 reviews. Material rule changes invalidate affected
decisions. Review writes require a verified user included in server-side
`EDGEOS_REVIEWER_USER_IDS`. Markets provides the evidence, fingerprint, audit
history, decision dialog, and version-conflict/error workflow.

## Authoritative financial model

The contract version is `edgeos.cashflow.v2`. API monetary values are decimal
strings with explicit currency. Python `Decimal` handles price, quantity, stake,
fees, payouts, and P&L.

### Required two-way reference

For compatible exhaustive outcomes and no costs:

```text
inverse_sum = sum(1 / decimal_odds_i)
stake_i     = capital * (1 / decimal_odds_i) / inverse_sum
payout      = capital / inverse_sum
gross P&L   = payout - capital
```

| Leg | Odds | Simulated stake | Covered-outcome payout |
| --- | ---: | ---: | ---: |
| A | 2.20 | $100.00 | $220.00 |
| B | 2.00 | $110.00 | $220.00 |

Total allocation is `$210`; gross profit is `$10` in either covered outcome;
gross ROI is `10 / 210 = 4.7619...%`. This is valid only with exhaustive,
compatible outcomes and available prices/sizes under the stated zero-cost model.

### General cash-flow procedure

Each offer carries price kind, currency, outcome payout vector, quote time,
minimum/increment/maximum, depth, fee inputs, settlement state, and source
evidence. The engine validates currency/outcomes/evidence/freshness, applies total
and per-provider balance caps, size/depth and discrete constraints, models stated
slippage and proportional/per-contract/fixed-order fees, computes cash flows for
every outcome, and selects a feasible quantity vector to maximize the minimum
modeled result.

It returns requested/deployed/unallocated capital, every leg and outcome, total
fees, best/worst result, gross/net ROI, limiting size, quote age/skew, lockup,
break-even movement, unmatched exposure, and hard gates. Small discrete spaces
are exhausted. Large spaces use a deterministic payout target plus bounded
neighborhood; that is reproducible but not a proof of global optimality.

Fee schedules support `ceiling`, `floor`, `half_up`, and `half_even` rounding of
the stated increment. The mapper preserves that mode and rejects malformed or
non-finite fee parameters, invalid rounding increments, and unsupported modes
instead of substituting zero fees or a default increment.
The pipeline validates normal-outcome payouts against the
quoted economics and refund/loss payouts against the explicit treatment.
Declaring a scenario set complete does not permit omitting a refund/loss event:
push, void, cancellation, postponement, retirement, and abandonment are checked.
A zero-return refund scenario prevents a strictly positive worst-case claim.

`EDGEOS_PIPELINE_SLIPPAGE_BPS` defaults to `10` in the live worker. This is a
recorded adverse-price assumption, not a measured execution estimate. Offers
use the larger of their existing slippage and that configured floor without
mutating source quotes. The pure calculation pipeline keeps a zero default for
callers that supply their own assumptions.

Net ROI uses minimum modeled net P&L divided by deployed capital. It is not
annualized. Capital remains locked until the modeled exit/settlement.

### Hard gates

Strict classification/ranking fails if settlement or coverage is unknown, data
is demo in live context, quote time is missing/future/stale, timestamp skew is
missing/excessive where required, material fees are unknown/out of date, required
depth is missing/insufficient, leg currencies differ,
discrete quantities cannot cover outcomes, worst case is not positive, or stress
exposure exceeds the stated constraint.

### Model boundaries

- Generic fees support proportional cash cost, per-contract cost, and fixed-order
  cost; an operator must supply effective-dated provider evidence.
- The engine consumes normalized price/depth; it is not a universal book router.
- Aggregated sportsbook odds normally cannot prove accepted stake.
- Transfer delay, withdrawal friction, FX, taxes, and jurisdiction costs remain
  outside automatic discovery. If they are material and not represented in the
  supplied cost evidence, the operator must reject the result.
- A positive bundle does not prove simultaneous fills.

## Ranking and scanner selection

Ranking occurs only after hard gates and uses conservative net dollars, minimum
ROI, known size, freshness, evidence, and execution-risk factors. The component
breakdown is transparent. The score is not a probability. Separate sorts cover
ROI, dollar profit, execution/freshness, match confidence, liquidity, quote time,
and research score.

The scanner supports search plus sport, league, provider, market type, minimum
ROI, minimum dollars, minimum known liquidity, maximum quote age, verified-only,
prematch/live, direction, page, and limit. Live mode executes these in the
Postgres repository, not against a hard-coded browser array. Unknown age/depth
renders unknown rather than fresh or `$0`. The strict persisted feed currently
models sportsbook `back` and binary `buy_yes`/`buy_no` leg shapes; records that
require lay or short-sale collateral are excluded until their margin and
liability accounting is implemented. The connected writer marks aggregator
decimal sportsbook odds indicative, so no current direct sportsbook adapter can
produce an executable-supported strict row. Prediction-market purchases still
need actual best-ask size plus every other evidence gate.

## Page-by-page review

### Dashboard

Shows paper portfolio value, realized/unrealized P&L, ROI, active positions,
detected/verified opportunities, capture rate, cumulative performance, and
operating summaries with mode labels and empty states. Ordinary development demo
uses the in-memory ledger; packaged Windows Local Mode uses the SQLite-backed
ledger. Configured live mode reads the durable paper and research repositories;
repository failures remain visible.

### Arbitrage Scanner

Provides the compact table, responsive filters, actual event time/phase, source
time/age/origin, match status, prices, gross/net ROI, dollars, depth, score, and
detailed drawer with source, legs, outcomes, assumptions, fees, audit, failure
reasons, and history. Scanner star/save actions use the owner-scoped API. Demo
saves persist in SQLite in the installed Windows app and reset with the API only
in the ordinary development demo; configured live saves persist in Postgres. A
sync failure is shown and no browser-only bookmark is substituted.

### Edge Lab

Validated/debounced inputs call the Python engine and expose two-/three-way
allocation, payout matrix, fees, slippage, caps, break-even, lockup, unmatched
exposure, version, and gates. Inputs accept decimal, American, fractional, and
binary contract asks; the Auto Balance action reruns the same server maximin
solver. Manual quotes explicitly report freshness/skew and official fee evidence
as unknown. It neither invents win probabilities nor promotes a manual set to
verified.

### Markets

Browses labeled synthetic provider events/markets in demo mode, including product
separation, IDs, sources, state/times, outcomes, prices, and evidence. Live direct
browser polling fails closed to protect provider quotas and provenance; use the
authenticated bounded connection test and worker telemetry until a
centrally-ingested persisted-snapshot read surface is added. No universal direct
sportsbook API or order placement is present.

### Paper Portfolio

Creates a bundle with exact allocation and notes, records each quantity/price/fee,
shows partial-fill exposure, settles/cancels, prevents reserve overcommitment,
uses append-oriented ledger activity, and exports CSV. The default `$10,000` demo
cash is a fixture, not advice. Unrealized P&L is zero when no authoritative mark
source exists. Packaged Windows records survive restart in device-local SQLite;
ordinary development demo records reset with the API. Configured live records
are transactional Postgres data and synchronize through the authenticated
account.

### Analytics

Today/7D/30D/90D/YTD/All/custom UTC ranges calculate realized P&L, fees, turnover,
reserved cash, equity/drawdown, capture denominator, and provider/sport/strategy
breakdowns from recorded activity. Planned profit is never realized. Configured
live analytics use Postgres paper activity, and the live dashboard reads that
paper summary plus persisted opportunity counts.

### Watchlist and Alerts

Watchlist CRUD covers team, sport, league, event, market, provider, and strategy.
Configured live creation resolves a canonical target and rejects missing or
ambiguous targets; custom labels/details and retry keys persist. Alert-rule CRUD
persists thresholds, enabled state, cooldown, and retry keys and lists stored
events. Packaged Windows local rules/watch items persist in SQLite; ordinary
development demo state is process memory; configured live state is owner-scoped
Postgres. The worker evaluates `new_verified_opportunity` and the UI's
`net_roi_above` rule (stored as `roi_threshold`) against active strict rows.
Minimum net profit, ROI, known liquidity, and maximum quote-age gates exposed by
the rule editor are conjunctive; event
keys and cooldown suppress duplicates, and opportunity expiry or withdrawal
marks related events withdrawn. The evaluator recognizes provider, market-type,
sport, and league condition keys, but the current rule DTO/editor does not expose
them. Liquidity-increase, spread-widening, quote-stale, market-close,
market-settlement, and provider-offline rules are CRUD-only in this release and
do not emit events. Delivery is an in-app database record only. No email, SMS,
push, native Windows notification, or background worker inside the standard
desktop package exists.

### Settings/account

Contains profile/timezone and research preferences, API Connections, password
flows, RLS-scoped export, and recent-auth account deletion. Desktop
authentication has the additional limits below.

## Authentication, RLS, and secrets

Supabase supplies email/password, verification, recovery, optional hosted Google
OAuth, and session management; EDGEOS does not store plaintext passwords. Server
authorization derives the user from the verified token, never a body user ID.

All exposed tables have RLS. Shared research tables have explicit read policies;
private rows use `auth.uid() = user_id` and matching `WITH CHECK` rules. Browser
grants are narrow. Service-only ingestion, financial append records, worker
leases, and private credential ciphertext are not browser-writable.

Provider credentials use AES-256-GCM with a fresh 96-bit nonce. Authenticated
additional data binds ciphertext to user, provider, and key version. The base64
32-byte master key is server-only and not stored in Postgres. A private schema
stores ciphertext, nonce, algorithm, version, and field names; public metadata
returns masks/status only. Logs/errors redact known values and common secret
patterns; exports omit ciphertext/original values. Changing a key without a
re-encryption rotation makes prior data unreadable.

The same-origin BFF proxies only an explicit path/method allowlist to a configured
internal HTTP(S) origin. It checks browser origin, bounds bodies/responses, refuses
redirects, carries the authenticated token, validates upstream schemas, and maps
unsafe error bodies to safe codes. It is not an unrestricted proxy.

## Paper accounting semantics

The in-memory demo ledger, Windows SQLite ledger, and live repository use
retry/idempotency keys and reconstruct committed capital from recorded fill
quantity, price, and fee. SQLite mutations are committed atomically and restore
their prior in-memory state if a durable write fails. The live implementation
also locks the owner's portfolio during mutations so concurrent requests cannot
reserve the same paper cash. Partial bundles remain exposed. Settlement posts
realized ledger entries only after an outcome; cancellation never books planned
edge as income. The live repository currently supports sportsbook `back` and
prediction-market `buy_yes`/`buy_no` paper fills; unsupported lay/sell collateral
semantics are rejected rather than invented. This is simulated accounting only.

## Historical simulation

The backtest endpoint sorts snapshots by observation time and supports zero/fixed
delay, seeded randomized delay, expiry, slippage, partial-fill/liquidity fractions,
a known-depth gate, captured/failed counts, gross/net result, worst exposure, and
drawdown. The supplied `conservative_profit` must already include any relevant
fees; replay has no independent fee parameter. It does not provide a persistent
user run library, historical provider L2, trained forecast model, or proof that
assumptions matched real fills. Missing historical depth cannot be reconstructed
without assumptions.

## Windows installer

The repository contains Electron, a PyInstaller API bundle, Next standalone
bundle, electron-builder NSIS config, integrity manifest, Electron fuse checks,
packaged smoke tests, signing scripts, and CI.

- x64, per-user install;
- loopback-only child servers on OS-assigned candidate ports;
- per-launch nonce linking the desktop instance to its health responses;
- separate per-launch API credential kept out of the renderer, command line, and
  logs, with constant-time FastAPI verification;
- sandbox/context isolation, no renderer Node integration, navigation restrictions,
  single-instance lock, and clean child shutdown;
- remembered/clamped window geometry and durable device-local SQLite workflow
  state beneath `%APPDATA%\EDGEOS`;
- build-time/staged resource hash validation;
- a local-demo default using bundled services and device-local simulated records;
- a native Connection menu for a verified hosted-live HTTPS origin, reconnect,
  and explicit return to local demo; hosted content receives no local settings
  bridge or API token and starts no bundled services;
- no installed worker/background Windows service;
- no auto-updater.

The deployed hosted web application supplies its own authentication configuration;
selecting it does not require bundling provider credentials or a database secret.
The local bundled build retains its existing desktop authentication limitations:
signup, confirmation/recovery links, and Google OAuth lack secure desktop
deep-link callbacks. Hosted flows that leave the selected origin also need
separate desktop acceptance; opening a page is not proof of a completed OAuth
return. Never embed a secret/service-role key.

Connection selection is stored in `%APPDATA%\EDGEOS\connection.json`, containing
the mode and optional HTTPS origin, not credentials. Per-origin browser sessions
retain hosted sign-ins separately from local demo data. Invalid saved settings or
failed live verification require an explicit recovery choice and never select
demo silently.

The installer must be built on Windows. The release scripts target
`artifacts/windows/EDGEOS Setup <version>.exe`, a manifest, and SHA-256 files.
The packaged-runtime smoke path writes a local setting, gracefully restarts the
bundled API against the same SQLite database, and verifies the value survives.
Tagged releases require a signature matching the approved certificate
thumbprint. Unsigned PR/manual artifacts are development-only. This Linux
source/browser audit does not establish a signed, installed, or launched `.exe`;
the Windows workflow and acceptance result must be checked separately. See
`WINDOWS_INSTALLER.md`.

The local database is not encrypted at rest and must not contain provider/Auth/
signing secrets. Startup validates its schema and integrity and preserves a
corrupt or unsupported file while failing closed. Settings can download and
transactionally restore a versioned, integrity-checked `.edgeos-backup` containing
only simulated local workspace namespaces. The file is not encrypted or
authenticated, and automatic scheduling, rotation, and remote retention are not
implemented; reliable recovery still depends on operator-held tested backups.

## Setup and deployment

### Local demo

```bash
cp .env.example .env
npm install
cd apps/api && uv sync --all-groups && cd ../..
```

Run `make api` and `make web` in separate terminals, then open
<http://localhost:3000>. OpenAPI is at <http://localhost:8000/docs>.

### Hosted/live prerequisites

1. Create Supabase and apply every migration in lexical order.
2. Configure Site URL, callbacks, confirmation template, SMTP, and optional Google.
3. Set web and API to `live` together.
4. Use a psycopg URI such as `postgresql://...`, not
   `postgresql+asyncpg://...`.
5. Generate/store the base64 32-byte credential key in a server secret manager.
6. Configure internal API origin and exact web/CORS origins.
7. Add a per-user The Odds API key through API Connections for masked storage and
   bounded testing. Separately set the deployment `THE_ODDS_API_KEY` for the
   current worker, which does not yet consume saved user connections. Never put a
   key in chat, client-visible environment, URLs, or browser storage.
8. Deploy web, API, and worker separately and supervise them.
9. Verify mode, migrations, worker lease/heartbeat, quotas, persisted records,
   provider times, and empty-or-evidenced scanner behavior.
10. Configure backups, retention, monitoring, TLS, and secret rotation.

Set and version the worker controls: `INGESTION_POLL_SECONDS`, shared/per-provider
hourly request budgets, `QUOTE_STALE_AFTER_SECONDS`,
`EDGEOS_PIPELINE_TOTAL_ALLOCATION`, `EDGEOS_MAX_QUOTE_SKEW_SECONDS`, and
`EDGEOS_PIPELINE_MINIMUM_LIQUIDITY`, plus
`EDGEOS_PIPELINE_SLIPPAGE_BPS` (default `10`). The slippage floor is an explicit
modeled assumption. Partial-fill probability, quote update rate, and stability
are optional measured inputs; lockup is an optional modeled duration. Missing
inputs remain unknown. `DEPLOYMENT.md` lists deployment controls.

See `.env.example`, `DEPLOYMENT.md`, and `OPERATOR_GUIDE.md`.

## Test and evidence guide

Run the repository verification:

```bash
./scripts/verify.sh
```

Or separately:

```bash
make test-api
make test-web
npm run desktop:test
npm run typecheck --workspace=@edgeos/desktop
npx playwright install chromium
npm run test:e2e
```

The inventory covers two-/multi-outcome math, the `$210` reference, fees,
negative-after-cost cases, rounding/discrete quantity/depth/caps, stale/future/skew
times, partial fills and scenario matrices, rule mismatches/reversed participants,
ranking gates, paper reserve/fills/settlement/idempotency, chronological replay,
provider normalization/retry/rate-limit/cache fixtures, API auth/mode/demo isolation,
RLS/schema ownership, AES-GCM wrong-owner/provider failure, masking/redaction,
browser workflows/date ranges/responsiveness, and desktop runtime/build controls.

Tests do not prove provider uptime, plan access, accepted stakes, simultaneous
fills, profitability, target deployment hardening, Windows reputation, or legal
eligibility. Report optional credentialed probes separately from fixture tests.

### Verification evidence from this upgrade session

Final backend verification completed **290 passed**, including the opt-in
actual PostgreSQL acceptance test, immediate stale-feed exclusion, and restored
active-history checks. Ruff passed and mypy passed for 37 source files.

The v3 matching/financial regression command completed **76 tests** on
2026-10-20 across `test_matching.py`, `test_arbitrage.py`, and
`test_opportunity_pipeline.py`. Ruff and mypy passed on the five changed source
modules. Coverage includes alias collisions, missing/mismatched propositions,
native-identity fingerprint invalidation, no-line moneylines, contradictory
outcome structure, missing/inflated refund payouts, fee rounding, and conservative
slippage without source mutation.

The recorded integration suite also completed **6 tests** after fee mapping
preserved the explicit rounding mode. The costly-fee fixture uses ceiling
rounding; a separate test preserves legitimate zero-rounded half-up fees for
the synthetic contract's tiny fractional quantities.

The opt-in `test_postgres_live_pipeline.py` also passed against actual
PostgreSQL 16 with all four migrations; local schema/RLS assertions passed.
The test uses a dedicated loopback `edgeos_test` database and a rollback-only
transaction, with no production data cleanup. Its rule/fee/quote evidence is
explicitly synthetic. It tests budget and health persistence, canonical gating,
raw/event/market/rule/quote writes, matching and Decimal calculation, opportunity
and alert persistence, the actual scanner API DTO, withdrawal, and restoration.
The default verified scanner excludes stale rows immediately while detail
history stays available; restoration adds an active lifecycle transition even
when quote IDs are unchanged. CI includes the database test.

The real local Next.js/FastAPI Playwright run completed **5 passed, 1 expected
skip** on 2026-10-20 using system Chromium. The new `paper-workflow.spec.ts`
creates a synthetic `$210` bundle at odds `2.20`/`2.00`, records partial then
complete fills, recognizes `$10` only when settled, and verifies a browser reload
reads the saved server state. The legitimate loopback mutation-origin regression
was fixed before this pass. These are demo inputs against running services,
not a provider execution or Windows installer test.

The web pass also completed **131 unit/component tests**, lint, TypeScript
checking, and a production Next.js build. Final combined backend, desktop, and
database acceptance results remain separate from the focused results above.
Worker regression tests cover persisted budget pauses, failed-poll health
and withdrawal, adapter lifetime, and explicit versus unknown assumptions.
Desktop connection tests exercise configuration, origin validation, health
identity, session separation, and settings-message boundaries; Windows runtime
acceptance remains separate.

The provider cooldown/failure-health pass completed **10 focused tests** and
Ruff, including long retry deadlines, cross-route/cache blocking, exact-expiry
recovery, HTTP attempt counts, and later failures overriding earlier success.

At **2026-10-20 17:46:34–17:46:35 UTC**, the actual Kalshi, Polymarket US, and
Polymarket International adapters each completed `list_events(limit=2)` and
`list_markets(limit=2)` with HTTP 200, returning two events and two markets.
Provider timestamps were known for both markets from each provider. Kalshi
returned two known outcome ask sizes; the two Polymarket list responses supplied
none. These were unauthenticated public reads with no paid quota or orders.
The Odds API was not probed.

A subsequent actual worker cycle at **2026-10-20 17:55:59–17:56:01 UTC**
persisted **100 events and 100 markets from each of Kalshi, Polymarket
International, and Polymarket US** in PostgreSQL. Each provider used two HTTP
requests; all three recorded zero rejected records. The Odds API was skipped
without configured sport/key. The downstream result was correctly
`no_evaluable_markets`, with **0 loaded evaluable markets, 0 opportunities, and
0 alerts**. This proves public provider-to-database ingestion at that time, not
a real provider-backed strict opportunity.

Those results establish retrieval and normalization at the recorded time.
Neither the probes nor deterministic adapter fixtures establish continuous
uptime, sufficient fee/rule/depth evidence for a strict opportunity, or entitlement.
The target Postgres migration/transaction/RLS path, supervised live-provider
soak, and Windows artifact build/sign/install/launch evidence must each be
reported independently. A successful browser or source test does not establish
those results.

These are source/fixture results, not proof of an executable price at a provider.
See `LIVE_PIPELINE_VERIFICATION.md` for the boundary between verified behavior
and deployment acceptance still required.

## Conservative paper-capital guidance

This is educational paper-trading structure, not financial advice. EDGEOS does
not know the user's obligations, jurisdiction, taxes, risk tolerance, provider
limits, or withdrawal conditions.

### Real money: $0

The advised real-money amount for this release is **$0**. Use simulated capital
until live collection has been independently operated/reviewed, exact rule/fee/
depth evidence exists, and the user independently establishes legality and
suitability. EDGEOS still would not promise a return or execute an order.

### Illustrative simulated training policy

For a `$1,000` fictional ledger, a conservative learning policy could be:

- retain at least 50% unreserved simulated cash;
- deploy at most 2% (`$20`) in one complete verified paper bundle;
- allow at most 1% (`$10`) one-event stress/one-leg loss;
- cap aggregate unmatched worst-case exposure at 3% (`$30`);
- simulate no more than 10% of evidenced executable depth;
- reduce size for long lockup or provider concentration;
- allocate `$0` when depth, fee, quote time, rules, outcome set, or currency cost
  is unknown.

These figures are intentionally illustrative, not optimized or real-money stake
recommendations. Change paper capital to study rounding, liquidity, concentration,
and lockup—not to convert missing evidence into confidence.

| Candidate | Paper treatment | Real treatment |
| --- | --- | --- |
| Demo fixture | Any amount useful to test UI/math, clearly recorded as demo | `$0` |
| Review required/rejected | Stress/research only; exclude from strict allocation | `$0` |
| Unknown fee/depth/time/rule/currency/outcome | `$0` strict allocation; optionally run explicit adverse scenarios | `$0` |
| Stale/future/skewed quote | `$0`; repair timing/ingestion | `$0` |
| Positive EV without a complete hedge | Model the full unhedged loss distribution; do not call arbitrage | `$0` here |
| Verified/fresh/complete/positive after costs | Paper only, bounded by balances/depth/stress exposure | `$0` here |

### Per-bundle review

1. Confirm origin and that no live leg silently uses demo/cache/history.
2. Open official sources and compare provider product/market IDs.
3. Confirm event, orientation, time, type, period, line/unit, overtime, draw, push,
   void/cancellation, and resolution source.
4. Confirm every mutually exclusive outcome, including draw/push cases.
5. Use executable ask/offer economics, not last trade or midpoint.
6. Confirm timestamp, age, cross-leg skew, and phase.
7. Confirm depth, minimum, increment, and maximum; unknown is a stop.
8. Confirm effective fee evidence plus slippage/transfer/FX assumptions.
9. Enter provider balances without reusing reserved paper cash.
10. Inspect every outcome, minimum result, break-even, partial/rejected-second-leg,
    and one-sided-void scenario.
11. Record each fill. If leg two fails, preserve the exposure.
12. Settle from evidence, then review realized P&L, fees, drawdown, denominator,
    and failure reason.

## Exact limitations and priorities

1. Extend the recorded bounded public probes into deployment and soak tests, then expand
   official effective-dated rule, fee, provider-time, and executable-depth
   coverage without weakening unknown-evidence gates.
2. Connect a centrally ingested persisted-snapshot browser for the live Markets
   page; direct browser/provider polling remains deliberately disabled.
3. Decide and implement a safe scheduler/ownership model if the ingestion worker
   should consume per-user saved connections; it currently uses public adapters
   and the deployment-level The Odds API key.
4. Add email/SMS/push/native Windows delivery only as separately configured
   channels with retry, consent, and delivery audit; current alerts are in-app.
5. Add a user-facing auditable correction-entry workflow and complete remaining
   research-note, saved-filter, and model/backtest-run persistence paths.
6. Continue hardening the existing Markets reviewer console with production
   operator training, evidence policy, and deployment acceptance testing.
7. Expand persisted historical replay/L2 availability reporting.
8. Add a production credential-key rotation procedure.
9. Soak-test leases, retries, lifecycle withdrawal, retention, quotas, and
   monitoring before a 24/7 claim.
10. Add scheduled/rotated backup destinations and a pre-start recovery path for
    cases where a corrupt primary database prevents the Settings UI from opening.
11. Build/sign/install/smoke-test the Windows artifact on Windows before release.

## Reviewer checklist

- [ ] Demo/live labels and mode mismatch fail closed.
- [ ] Live failures never render demo opportunities or P&L.
- [ ] Unknown age/liquidity/fees/rules stay unknown.
- [ ] The `$210` fixture returns `$100/$110`, `$220`, `$10`, about `4.7619%`.
- [ ] Costs/staleness/partial fill can turn an apparent edge negative.
- [ ] Review/rejected markets never enter strict counts.
- [ ] Material rule changes invalidate prior decisions.
- [ ] User A cannot read User B private rows/connections.
- [ ] Secrets never appear in safe DTOs, logs, exports, URLs, or bundles.
- [ ] Retry keys do not duplicate paper financial activity.
- [ ] Projected profit is not realized before settlement.
- [ ] Date filters affect underlying analytics records.
- [ ] In-app alert events are not advertised as email/SMS/push/native delivery.
- [ ] Fixture tests and live probes are reported separately.
- [ ] A Windows artifact has hash/signature evidence before distribution.
- [ ] No screen/document suggests automated betting or guaranteed profit.

## Documentation map

- `README.md` — installation and top-level status
- `.env.example` — environment contract
- `docs/API.md` — routes and response behavior
- `docs/ARCHITECTURE.md` — components/trust boundaries
- `docs/DATA_PROVENANCE.md` — origins, timestamps, evidence, unknowns
- `docs/MODEL_ASSUMPTIONS.md` — math definitions and limits
- `docs/PROVIDER_MATRIX.md` — provider/product support and official sources
- `docs/SECURITY.md` — auth, RLS, encryption, proxy, deployment controls
- `docs/OPERATOR_GUIDE.md` — daily operation and incidents
- `docs/DEPLOYMENT.md` — hosted rollout and worker requirements
- `docs/LIVE_PIPELINE_VERIFICATION.md` — connected stages, gates, fixtures, and
  deployment acceptance boundary
- `docs/WINDOWS_INSTALLER.md` — build/sign/install/verify/remove
- `docs/RELEASE_NOTES.md` — current desktop release outcome and limitations
- `supabase/README.md` — migrations and database assertions

The safest characterization is: EDGEOS is a substantial research and durable
paper-accounting foundation with rigorous core math, provenance, authenticated
private storage, and a connected fail-closed ingestion → matching → calculation
→ opportunity lifecycle → in-app alert pipeline. It still needs target-environment
Supabase/provider/worker verification and sustained operations before it can be
described as continuously operational live market intelligence.
