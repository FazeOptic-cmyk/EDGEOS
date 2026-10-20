# EDGEOS architecture

EDGEOS is a read-only market-research and simulated paper-trading system. There
is no real-money order-entry service. The architecture separates shared market
evidence from authenticated user records and keeps every upstream adapter behind
the server.

## Component map

```text
                              ┌────────────────────────────┐
Browser / Electron renderer ─►│ Next.js App Router         │
                              │ UI + SSR + same-origin BFF │
                              └───────┬─────────┬──────────┘
                                      │         │
                       Supabase SSR/RLS│         │bearer + mode contract
                                      ▼         ▼
                             Supabase Auth    FastAPI
                             + Postgres       ├─ provider adapters
                                              ├─ normalizer/matcher
                                              ├─ Decimal cash-flow/ranking
                                              ├─ public research repositories
                                              ├─ user/private repositories
                                              └─ demo ledger/store
                                                   ├─ memory (development)
                                                   └─ SQLite (Windows Local Mode)
                                                     ▲
                                                     │
Provider APIs ─► worker ─► normalized/canonical evidence
                         └─► matching ─► Decimal engine ─► opportunity/alert DB─┘
```

The diagram shows module boundaries. Provider-connection CRUD/test routes use the
cipher/repository through the same-origin web BFF. After bounded provider polls,
the worker loads persisted evidence, records match audits, writes only strict
opportunities, applies lifecycle expiry/withdrawal, and evaluates qualifying
in-app alert rules. It does not yet consume saved per-user connections.

### Repository layout

- `apps/web` — Next.js 16/React UI, Supabase SSR, query/cache clients, validated
  BFF routes, accessibility and browser tests.
- `apps/api` — FastAPI, provider contracts, normalization, matching, cash-flow,
  ranking, paper accounting, persistence, replay, and worker.
- `apps/desktop` — Electron host and Windows package configuration.
- `supabase/migrations` — Postgres types/tables/indexes/triggers/grants/RLS.
- `scripts` — validation, local readiness, Windows build/sign/verify helpers.
- `docs` — product, model, provider, security, operations, and deployment truth.

## Data planes

### Shared research plane

Providers, sports, leagues, participants/aliases, events, provider events,
markets, market rules, outcomes, fee schedules, quotes, order books, market
matches/reviews, opportunities, opportunity legs/outcomes/history, raw ingestion,
ingestion runs, and provider health are shared evidence. Browser read access is
explicit and limited; ingestion/mutation is service-controlled.

### Private user plane

Profiles, preferences, provider-connection metadata, encrypted credentials,
saved filters/opportunities, notes, watchlists, alert rules/events, paper
portfolios/positions/legs/fills/settlements/ledger entries, performance snapshots,
model runs, and backtests carry owner IDs and RLS. Service queries additionally
predicate every operation by the authenticated token subject.

Private credential ciphertext resides in the `private` schema with no `anon` or
ordinary `authenticated` access.

## Ingestion pipeline

The durable target flow is designed as separate stages:

1. a provider adapter makes a bounded official read-only request;
2. the worker records attempt/run identity and an immutable, hashed
   `normalized_adapter_contract` capture (not the upstream HTTP bytes);
3. validation rejects incompatible schemas instead of returning an empty success;
4. normalization maps provider records while retaining original IDs and times;
5. idempotent upserts create canonical/provider events, markets, outcomes, rules,
   and quotes; order-book storage exists separately but is not collected by this
   discovery-only worker path;
6. candidate matching compares event structure and complete settlement metadata;
7. rule fingerprints bind versioned machine/human decisions;
8. the Decimal engine evaluates all outcomes and constraints;
9. hard gates and transparent rank components determine whether the result is
   theoretical or strict;
10. strict opportunity legs/outcomes/history and match audits are persisted;
11. stale/absent lifecycle changes and qualifying in-app alerts are applied;
12. the live scanner reads the research repository with server filters/paging.

The worker implements stages 1–12 with a database lease/heartbeat, versioned runs,
request budgets, provider health, and idempotent writes. Its capture record is a
stable `normalized_adapter_contract` envelope, not the byte-for-byte upstream
HTTP payload. Canonical event materialization uses exact normalized identity
evidence; fuzzy similarity never merges database events. Missing provider quote
time is persisted as immediately stale.

The post-ingestion cycle loads a bounded evidence set, converts it without
substitutions, applies only fingerprint- and engine-version-valid review
overrides, runs candidate matching and all-scenario Decimal calculation, persists
cross-market audits and strict opportunities, withdraws no-longer-present rows,
expires stale/closed rows, and evaluates alerts for the active IDs written in
that cycle. Persistence failure propagates before absence withdrawal. If nothing
is evaluable, the cycle may expire already-stale rows but does not broadly
withdraw prior opportunities.

The strict gate requires the selected legs to share one non-empty canonical event
and explicit evidence for rules, complete scenario coverage, provider time,
freshness/skew, source reference, executable price/depth, effective fee schedule,
and currency compatibility. The modeled minimum result across normal, push,
void, refund, and other declared scenarios must remain positive. It does not fill
missing facts with assumptions. Non-current, indicative, or incompletely
evidenced calculations may remain theoretical diagnostics but are not persisted
as active strict opportunities.

## Provider adapter contract

Each provider exposes a descriptor (code, product, feed type, auth requirement,
capabilities, official hosts/docs), event/market discovery, optional order book,
and health state through normalized models. The common HTTP layer bounds response
size/time, coalesces identical requests, caches only briefly, applies jittered
retry/backoff for eligible failures and `429`, and retains quota/error telemetry.

Provider/product identity is never flattened: Polymarket US Gateway and
International Gamma/CLOB have separate adapter codes and ID namespaces. A new
adapter does not enter strict matching until settlement mapping and contract
fixtures exist.

Adapter capability is not the same as evidence present in a worker snapshot.
Some adapters implement a public order-book method, but the worker's current
event/market collection path does not guarantee historical L2 or executable
depth for every outcome. Missing depth blocks the strict feed. Aggregated
sportsbook quotes from The Odds API are marked indicative because an upstream
price does not prove that a named book will accept the modeled stake.

Live provider-health API/UI reads use the worker's persisted telemetry. A page
view never turns into an upstream provider request, which prevents browser
traffic from bypassing request budgets or making an unavailable provider appear
healthy.

## Matching boundary

Text normalization and fuzzy similarity generate candidates only. Verification
requires sport/league, participants and orientation, start time, market/period,
line/unit, complete outcome set, overtime/shootout, tie/draw, push/refund,
void/cancel/postpone/retirement, and resolution-source compatibility. Unknown
material fields produce `review_required`; differences produce `rejected`.

A human decision is versioned, has rationale/checks/source evidence, and is tied
to left/right rule fingerprints. A material rule update invalidates the decision.
Only explicit reviewers configured server-side may mutate global decisions.

## Financial boundary

`edgeos.cashflow.v2` is the authoritative Python Decimal contract. It evaluates
cash required and payout for every exhaustive outcome with price kind, fees,
slippage, quantity increments/minimums, depth, total/per-provider capital caps,
freshness, cross-leg skew, settlement status, and evidence. It reports decimal
strings and never treats unknown material cost or liquidity as zero/unlimited.

The web Edge Lab, scanner drawer, and paper-position creation consume this
contract. Edge Lab normalizes two-/three-way decimal, American, fractional, and
binary-contract inputs before submitting them to the API. Browser calculations
are input/format validation, not an independent source of financial truth.
Manual calculator sets remain review-only.

## Opportunity repository and ranking

The public Postgres repository reconstructs an opportunity from persisted event,
market, quote, leg, outcome, match, assumption, and history records. Search,
filters, sort, direction, page, and limit are allowlisted and executed server-side.
Unknown quote time or depth cannot satisfy a maximum-age/minimum-liquidity filter.

Rank components apply after hard gates. A research score is not a fill probability
or return forecast and cannot override an unverified match, unknown fee/depth,
stale quote, incomplete outcome set, or non-positive worst case.

Active strict rows have explicit expiry/lifecycle state. A later cycle withdraws
an opportunity when its evaluated market scope no longer produces the same key;
stale or closed rows expire, and related in-app alert events are marked withdrawn
with an audit reason. The web refreshes persisted live data every 15 seconds only
while the page is visible and refetches on focus. That UI cadence is presentation
behavior, not a provider poll or a substitute for an always-on worker.

The alert evaluator currently supports `new_verified_opportunity` and the UI's
`net_roi_above` rule (stored as `roi_threshold`). The rule API/editor exposes
conjunctive minimum net profit, ROI, known liquidity, and maximum quote-age gates.
The evaluator can also honor
provider, market-type, sport, and league keys if they already exist in a stored
condition object, but no supported editor/DTO currently creates those scope keys.
A unique alert/opportunity key plus cooldown avoids duplicate active events.
Other displayed rule types remain stored configuration only until a matching
evaluator exists. Delivery is an in-app database record only; email, SMS, push,
and native Windows delivery are not part of this release.

## Authentication and API boundary

Supabase Auth owns passwords/tokens. The browser receives only project URL and a
publishable key. Next.js refreshes cookie sessions; FastAPI validates the bearer
token. Every private query uses the verified subject.

The BFF only forwards allowlisted paths/methods to a server-configured internal
HTTP(S) origin. It checks same-origin mutations, expected desktop host/nonce,
body/response limits, redirects, timeouts, and response schemas. In the desktop
runtime it also attaches a cryptographically random, server-only per-launch
credential; FastAPI rejects business routes without it using constant-time
comparison. The token is not exposed to the renderer. These controls do not
claim isolation from malware already executing as the same Windows user. The BFF
does not accept an arbitrary destination URL.

## Runtime modes

- `demo` — deterministic `is_demo=true` research. Ordinary development uses
  process-local private workflow state that resets with the API; packaged Windows
  Local Mode supplies an application-owned SQLite path so private simulated state
  survives restart. Neither makes demo market data live.
- `live` — external adapters and configured Postgres repositories. Missing
  dependencies return an empty result or actionable error, never demo data.
- tests — deterministic fixtures and mocked upstream HTTP.

The client sends its expected mode and the API publishes mode/version headers on
every response. Mismatch returns HTTP 409. This prevents a live-looking UI from
rendering demo profitability.

## Paper ledger

Paper records are distinct from market opportunities. A position preserves the
calculation version and original snapshot; fills record quantity/price/fee/time;
settlement posts realized ledger entries; cancellation does not book theoretical
profit. Retry keys make financial commands idempotent. Open partial bundles retain
their unmatched outcome exposure. Corrections must be append-only adjustments.

The exact current durable/demo support is maintained in `HANDOFF.md`; schema
presence alone is not a claim that every private FastAPI repository is connected.

## Historical simulation

The baseline replay sorts by observed time, checks availability time, and models
fixed/seeded-random delay, expiry, slippage, partial fills, liquidity gates,
failure, worst exposure, and drawdown. The input `conservative_profit` must
already include relevant fees; the replay has no fee field. It is stateless unless
a caller persists the result. Historical depth cannot be inferred if no book was
captured.

## Windows architecture

The Windows package combines Electron, the standalone Next server, and a
PyInstaller API executable. Both child services bind to loopback candidate ports
and must return a per-launch nonce. Electron passes the API/BFF a distinct
per-launch request credential and supplies an absolute SQLite path confined under
its `userData` directory. The renderer is sandboxed; routes use the same BFF/mode
checks; staged hashes and Electron fuses are verified. Window bounds/maximized
state are persisted and clamped to an available display. Standard packages are
forced to demo market-data mode and do not install a worker, hosted database, or
live secret, but private paper/workspace records are durable on that device.

SQLite uses a versioned strict schema, WAL/full synchronization, bounded
checksummed JSON state, atomic writes, and startup integrity checks. It contains
no provider/Auth/signing credentials and is not encrypted at rest. A consistent
backup primitive exists internally. The UI also exposes a versioned logical
backup with strict size/schema/namespace/hash/domain validation, transactional
restore, and stale-facade generation fencing. Scheduling, rotation, encryption,
remote retention, and pre-start recovery are not shipped.

## Deliberate extension boundary

Any future execution product must be a separate compliance/eligibility-gated
service with independent authorization and audit. Do not add a provider write
route, sportsbook credential, geolocation workaround, or order button to this
research API.
