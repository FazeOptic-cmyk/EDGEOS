# FastAPI contract

The API is versioned under `/api/v1`; generated OpenAPI at `/docs` is the
machine-readable source of truth. It is a read-only market-research and simulated
paper-trading API. It contains no real-money order route.

## Wire rules

- money, odds, quantities, fees, percentages, and P&L serialize as decimal
  strings to avoid browser binary-float loss;
- timestamps are UTC ISO-8601 (`Z` on API serialization);
- unknown values are `null`; an observed numeric zero remains `"0"`;
- provider/product IDs and source references are preserved;
- demo records carry `is_demo=true` and/or `data_origin=demo`;
- lists use bounded `limit` plus page/cursor metadata;
- private state is selected from the verified token subject, never a request
  `user_id`.

## Mode contract

The web sends `X-EDGEOS-Client-Mode: demo|live`. Every API response publishes:

```text
X-EDGEOS-Mode: demo|live
X-EDGEOS-Mode-Contract: 1
```

If the requested/client mode differs, the API returns HTTP 409 with
`detail.code=mode_mismatch`. Live mode never responds with a demo opportunity or
demo private record.

## Authentication

In demo mode, workflow routes use the fixed local demo principal. Ordinary
development uses resettable process memory; packaged Windows Local Mode injects
an application-owned SQLite path and persists private simulated state across API
restarts. In live mode, protected routes require
`Authorization: Bearer <Supabase access token>` and validate it before using the
subject. Missing durable dependencies return a stable 503 error rather than a
memory fallback.

The packaged desktop adds a separate transport boundary: Electron generates a
random per-launch API credential, gives it only to the FastAPI and Next.js server
processes, and the BFF supplies `X-EDGEOS-Desktop-Token` upstream. FastAPI desktop
middleware rejects business requests without the exact credential. It is not a
public client header and must never be embedded in renderer code or logs.

Global match-review mutations additionally require the subject to appear in
`EDGEOS_REVIEWER_USER_IDS`. Account deletion requires a token issued within the
last 15 minutes.

## System and dashboard

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Service/version, mode contract, auth state, path-free local-storage backend/durability/schema status, paper-only flag, time. |
| `GET` | `/api/v1/dashboard` | Paper summary and opportunity counts; demo uses the ledger, live requires connected private/public repositories. |

Health success means the API process can answer; it is not proof each provider,
worker, database, or market is healthy.

## Opportunities and matching

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/opportunities` | Demo reference item or durable live research page. |
| `GET` | `/api/v1/opportunities/{id}` | Persisted calculation, legs/outcomes, match audit, assumptions, failures, and history. |
| `GET` | `/api/v1/saved-opportunities` | Owner-scoped saved/favorite records. |
| `PUT` | `/api/v1/saved-opportunities/{id}` | Idempotently save/update favorite state. |
| `DELETE` | `/api/v1/saved-opportunities/{id}` | Remove the owner's saved record. |
| `GET` | `/api/v1/match-reviews` | Persisted review-required/invalidated queue; demo returns an honest empty queue. |
| `PATCH` | `/api/v1/match-reviews/{id}` | Reviewer-only versioned decision with checks, rationale, and source evidence. |

Opportunity query parameters:

- `search`, `sport`, `league`, `provider`, `market_type`;
- `minimum_net_roi`, `minimum_theoretical_profit`, `minimum_liquidity`;
- `maximum_quote_age`, `verified_only`;
- `phase=prematch|live|all`;
- `sort=theoretical_roi|theoretical_profit|execution_risk|match_confidence|liquidity|quote_time|research_score|net_roi`;
- `descending`, `page`, and `limit` (1–200).

Unknown depth cannot satisfy minimum liquidity; unknown quote time cannot satisfy
a maximum-age filter. Live list/detail reads exclude demo rows.

A review request requires:

```json
{
  "decision": "verified | review_required | rejected",
  "rationale": "specific evidence-based explanation",
  "checks": {},
  "source_evidence": {},
  "expected_version": 1
}
```

Verification is rejected unless complete outcome/rule checks and source evidence
are supplied. Concurrent versions return 409.

## Calculation

| Method | Route | Behavior |
| --- | --- | --- |
| `POST` | `/api/v1/simulate` | Evaluate a normalized hypothetical bundle with `edgeos.cashflow.v2`. |
| `POST` | `/api/v1/simulate/scenarios` | Evaluate an explicit arbitrary payout/refund/fill scenario matrix. |

`/simulate` requires an exact `expected_outcome_ids` partition. It returns
requested/deployed/unallocated capital, leg quantities/costs/fees, each outcome,
best/worst result, gross/net ROI, depth, age/skew, break-even, lockup, unmatched
exposure, and rejection gates. Edge Lab manual offers supply no provider quote
timestamp, so age/skew remain unknown and freshness fails by design. The public
route also forces `review_required` because a caller cannot self-attest settlement
equivalence.

`/simulate/scenarios` is research-only and lets the caller enumerate push, void,
partial-fill, rejected-leg, or asymmetric payouts. The caller is responsible for
providing a complete scenario partition; the route never makes it execution-safe.

## Provider research

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/providers/health?probe=false` | Descriptor/snapshot diagnostics only. Shared live health reads never contact providers; `probe=true` is rejected in live mode. |
| `GET` | `/api/v1/providers/{code}/events` | Labeled demo event page in demo mode. Direct live polling is disabled until this reads centrally persisted ingestion snapshots. |
| `GET` | `/api/v1/providers/{code}/markets` | Labeled demo market page in demo mode. Direct live polling is disabled until this reads centrally persisted ingestion snapshots. |

Provider code is a closed enum. Demo events/markets accept a bounded `limit`,
opaque `cursor`, and supported filters. A demo process rejects every non-demo
provider before constructing an adapter. A live process rejects direct provider
browsing and user-triggered shared health probes so neither an anonymous nor an
authenticated browser can burn the server's provider quota. The separate API
Connections test is authenticated, bounded, owner-configured, and no-retry;
continuous reads belong in the supervised ingestion worker.

Normalized records identify `live_poll`, `cache`, `historical`, or `demo` origin,
source URL, provider ID, EDGEOS observation time, and upstream time where actually
supplied. Availability of bid/ask/depth depends on provider capability.

## Provider connections

These routes are registered in FastAPI and forwarded through the same-origin web
BFF. They require live mode, authenticated ownership, Postgres/migrations, and a
valid server-only AES credential key; otherwise they fail closed:

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/provider-connections` | Owner's masked configurations/status. |
| `POST` | `/api/v1/provider-connections` | Add or replace credential/configuration. |
| `PATCH` | `/api/v1/provider-connections/{provider}` | Enable/disable or change polling selections/budget. |
| `DELETE` | `/api/v1/provider-connections/{provider}` | Delete safe row and encrypted envelope. |
| `POST` | `/api/v1/provider-connections/{provider}/test` | Bounded server-side connection test and safe diagnostics. |

Validation accepts only `api_key` for The Odds API and rejects secret fields for
public Kalshi/Polymarket. Responses return safe masks/status, never plaintext.
The test route builds one read-only adapter, disables retry, bounds the call, and
persists categorized safe health metadata. It does not establish continuing
availability, plan entitlement, accepted stake, or eligibility. The current
worker does not consume these per-user rows; it uses public adapters plus the
deployment-level The Odds API key.

## Paper portfolio

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/portfolio` | Summary and positions with per-leg fill state/exposure. |
| `POST` | `/api/v1/portfolio/positions` | Create a simulated bundle from an authoritative opportunity. |
| `POST` | `/api/v1/portfolio/positions/{id}/fills` | Append a simulated leg fill. |
| `POST` | `/api/v1/portfolio/positions/{id}/settle` | Append settlement and realized ledger effects. |
| `POST` | `/api/v1/portfolio/positions/{id}/cancel` | Cancel without booking projected profit. |
| `GET` | `/api/v1/portfolio/export.csv` | Owner-scoped CSV, explicitly simulation/demo labelled. |

Financial mutations require an `X-Idempotency-Key` (or matching body key where
the schema supports it), 8–200 characters. If both are present they must match.
Ordinary development demo uses process memory. Packaged Windows demo/local mode
uses the same route contract with device-local SQLite; positions, fills,
settlements, cancellations, ledger entries, settings, watchlists, alert rules,
and saved opportunities survive a process restart. In configured live mode these
routes use transactional, owner-scoped Postgres accounting. Live creation accepts
only an active, verified, non-demo opportunity with no hard-gate failures and
available paper balance/depth. Supported fill actions are sportsbook `back` and
prediction-market `buy_yes`/`buy_no`; lay/sell collateral models are rejected.

## Analytics, watchlists, alerts, and settings

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/analytics?from=...&to=...` | UTC interval aggregates from recorded paper activity. |
| `GET/POST` | `/api/v1/watchlists` | List/add owner-scoped watched targets. |
| `DELETE` | `/api/v1/watchlists/{id}` | Remove owner's watch item. |
| `GET/POST` | `/api/v1/alerts` | List/create owner-scoped alert rules. |
| `PATCH/DELETE` | `/api/v1/alerts/{id}` | Update/delete owner's rule. |
| `GET/PUT` | `/api/v1/settings` | Owner research defaults/preferences compatibility API. |
| `GET` | `/api/v1/local-workspace/backup` | Installed Local Mode only: versioned portable simulated-workspace backup. |
| `POST` | `/api/v1/local-workspace/restore` | Installed Local Mode only: validated transactional logical restore; requires `X-EDGEOS-Confirm-Restore: REPLACE`. |

Alert CRUD does not mean a rule evaluator is deployed. No route promises email,
SMS, push, or external delivery.

The local backup media type is
`application/vnd.edgeos.workspace-backup+json`. Restore is size-bounded, accepts
only the three known local namespaces, verifies document and per-payload hashes,
re-runs startup/domain and ledger-integrity checks, and fences pre-restore store
handles so a concurrent stale mutation cannot overwrite the restored data. It
never includes the hosted provider-credential repository or cloud session data.

## Backtest and account

| Method | Route | Behavior |
| --- | --- | --- |
| `POST` | `/api/v1/backtests` | Stateless timestamp-ordered baseline replay. |
| `DELETE` | `/api/v1/account` | Recent-auth session revocation and exact Supabase user deletion. |

The replay accepts snapshots plus delay/slippage/expiry/fill/depth assumptions. It
does not claim persisted historical L2 when none was captured.

## Error contract

Pydantic validation uses structured 422 responses. Domain/dependency errors use
stable `detail.code` or safe `error_code` values. Important examples:

| HTTP | Code/example | Meaning |
| ---: | --- | --- |
| 401 | `authentication_required` | Missing/invalid live session. |
| 403 | `reviewer_permission_required` | User cannot mutate global match decisions. |
| 409 | `mode_mismatch` | Web/API modes disagree. |
| 409 | `review_version_conflict` | Stale operator decision version. |
| 422 | `incomplete_review_evidence` | Cannot verify without complete checks/source. |
| 429 | `rate_limited` | Local budget or provider throttle. |
| 503 | `persistent_store_not_configured` | Required live repository is absent. |
| 503 | `research_store_unavailable` | Configured Postgres research read failed. |

Calculation responses also carry non-HTTP hard gates such as
`settlement_not_verified`, `stale_quote`, `insufficient_depth`,
`fee_schedule_unknown`, or unmatched exposure. A 200 calculation response is not
an approval to allocate capital.

Provider errors and database failures must not contain API keys, authorization
headers, plaintext credentials, ciphertext, or DSNs.
