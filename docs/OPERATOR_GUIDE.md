# EDGEOS operator guide

This runbook is for the research/paper-trading release. It does not authorize or
describe real-money execution. The advised real-money allocation is **$0**.
Detailed product status and capital examples are in `HANDOFF.md`.

## Status vocabulary

| Label | Meaning |
| --- | --- |
| `WORKING` | Implemented and exercisable, subject to upstream/runtime health. |
| `NEEDS CONFIGURATION` | Code exists but needs auth, database, key, migration, or deployment. |
| `DEMO ONLY` | Deterministic market evidence, never current/live data. Private local records may still be durable. |
| `UNAVAILABLE` | Absent or deliberately disabled; no substitute is fabricated. |

## First local run

```bash
cp .env.example .env
npm install
cd apps/api && uv sync --all-groups && cd ../..
```

Run `make api` and `make web` in separate terminals. Open
<http://localhost:3000>; OpenAPI is <http://localhost:8000/docs>.

Start in demo mode. It exercises scanner → detail → paper position → fills →
settlement → analytics without keys. All opportunity values are labelled
fixtures. Demo private records live only in API process memory and reset when the
API stops.

That reset statement applies to this source-development workflow. The installed
Windows application supplies its own SQLite database at
`%APPDATA%\EDGEOS\data\edgeos-local.sqlite3`; local paper records, watchlists,
alert rules, settings, and saved opportunities survive an ordinary close/reopen.
They remain demo/simulated, stay on that device, and do not become live market
evidence or cloud-synced account data.

Run safe readiness/verification checks with:

```bash
./scripts/doctor.sh
./scripts/verify.sh
```

## Mode preflight

Before reviewing any number:

1. Confirm the web banner says the intended mode.
2. Check `GET /api/v1/health` reports the same mode.
3. Confirm responses contain matching `X-EDGEOS-Mode` and contract version.
4. Treat HTTP 409 `mode_mismatch` as a release/configuration incident.
5. Never work around the guard by removing a header or relabelling demo data.

Live mode never falls back to demo quotes, positions, or performance.

## Daily live-operation checklist

The configured worker performs ingestion, canonical linking, strict matching,
all-scenario calculation, opportunity lifecycle, and in-app alert evaluation in
one supervised cycle. Non-zero results are valid only when their persisted
evidence passes every gate; zero strict rows can be a healthy and expected
outcome.

1. Confirm API, web, database, and worker are on the same release.
2. Confirm the current database migrations are applied.
3. Check worker lease owner, heartbeat, and last completed run.
4. Review each configured provider: enabled/configured state, last attempt, last
   success, last market update, quota/budget, backoff, and schema errors.
5. Compare provider observation times to receipt/ingestion times. A recent fetch
   of an old quote is still stale.
6. Check normalized-contract capture, normalized market/quote, match, and
   opportunity counts. Missing exact canonical identity, rules, fees, source,
   provider time, depth, executable basis, currency, or scenario coverage must
   block strict persistence.
7. Check duplicate/conflict/rejection counts rather than only inserted rows.
8. Check opportunity-stage survival: candidates → verified → cost/depth/freshness
   gates → positive worst-case.
9. Sample at least one scanner row back to its exact provider IDs, rules, quotes,
   leg/outcome records, and calculation version.
10. Review opportunity withdrawal/expiry plus alert created/deduplicated/filtered/
    withdrawn counts. Confirm no stale alert remains presented as active.
11. Check database size/retention, error queue, and backup state.

Do not claim 24/7 monitoring unless the worker is actually supervised and these
signals are monitored while the UI is closed.

## Connecting providers safely

Settings → API Connections provides authenticated live-only, owner-scoped
add/replace/test/enable/disable/delete through the registered backend. It requires
Postgres/migrations and the credential cipher key. The current worker does not
consume these saved connections, so configure worker collection separately
through the server secret manager. Never paste a secret into chat, a URL, a
browser console, or source code.

The live integrations/health display reads persisted worker telemetry. Opening or
refreshing the page does not call a provider and cannot repair a failed poll.

### The Odds API

1. Store the key in the worker's server-only secret environment.
2. Choose only needed official sport keys, regions, and markets.
3. Set a conservative polling interval and hourly request budget.
4. Start with a bounded poll and inspect quota remaining and last market-update time.
5. An `invalid_credentials`, `insufficient_plan`, `rate_limited`, or
   `provider_unavailable` status has distinct remediation; do not retry rapidly.

Bookmaker presence is determined by the response. This feed normally supplies
neither accepted-stake evidence nor order-book depth, so the opportunity engine
keeps it indicative/theoretical rather than executable-quote-supported.

### Kalshi

The implemented public REST routes are credential-free. Do not enter a trading
private key. The adapter implements event, market, and public order-book reads,
but the current worker's event/market polling path does not guarantee a book/depth
snapshot for every outcome. Missing executable depth blocks the strict feed.
Authenticated WebSocket and account/order routes are not implemented.

### Polymarket

Configure US and International as separate providers. Never reuse IDs or point a
US adapter at Gamma/CLOB, or vice versa. Both implemented surfaces are public
read-only research. Access does not establish eligibility to transact.

### Credential-store prerequisites

The encrypted service requires authenticated live mode, `DATABASE_URL`, the
secure migration, a valid server-only 32-byte base64 master key, an internal API
origin, and the expected mode handshake. Responses expose only masks and safe
status. Rotating a populated store requires re-encrypting existing envelopes.

### Worker controls

Set `INGESTION_POLL_SECONDS`, the shared or per-provider hourly request budget,
`QUOTE_STALE_AFTER_SECONDS`, `EDGEOS_PIPELINE_TOTAL_ALLOCATION`,
`EDGEOS_MAX_QUOTE_SKEW_SECONDS`, and
`EDGEOS_PIPELINE_MINIMUM_LIQUIDITY` deliberately for the deployment. The optional
`EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY`,
`EDGEOS_MEASURED_QUOTE_UPDATE_HZ`, `EDGEOS_MEASURED_QUOTE_STABILITY`, and
`EDGEOS_EXPECTED_LOCKUP_SECONDS` values must come from defensible measurements;
leave them unset when unknown. Restarting a worker changes active configuration,
so record the release and environment version used for each operating period.

## Scanner review procedure

1. Set sport/league/provider/market/phase and verified-only scope.
2. Add conservative minimum net dollars/ROI and maximum quote age.
3. Use a minimum-liquidity filter only when provider depth is actually known.
4. Sort separately by minimum net ROI, dollars, freshness/execution risk,
   matching confidence, and known liquidity. Research score is not probability.
5. Open the drawer and verify:
   - exact provider/product and market IDs;
   - source references;
   - provider time, retrieval/ingestion time, age, and cross-leg skew;
   - event/participants/orientation/start/phase;
   - market/period/line/unit and all outcomes;
   - overtime/draw/push/void/cancel/postpone/retirement rules;
   - effective fee evidence, increments, rounding, depth, and balance caps;
   - every outcome P&L plus break-even and partial/rejected-leg stress;
   - matcher/calculation version and any gate/failure reason.
6. If any material field is unknown, move it to research-only and allocate `$0`
   in the strict paper book.

Never call a row guaranteed profit. The correct description is “modeled minimum
net result under stated assumptions,” subject to price and fill risk.

## Match-review procedure

Global review writes are restricted to IDs configured in
`EDGEOS_REVIEWER_USER_IDS`.

1. Read provider rule evidence at the exact source/version.
2. Compare every field listed above, not just title/teams.
3. Require complete mutually exclusive outcome coverage.
4. Record specific rationale, structured checks, and source evidence.
5. Use the current `decision_version`; a conflict means another reviewer changed
   the record and it must be reloaded.
6. Do not mark verified when a material rule is unknown.
7. Expect a material market-rule update to invalidate the decision.

The Markets page exposes the review queue, fingerprints, source/check JSON,
history, versioned decision dialog, and actionable permission/conflict errors.
Only an allowlisted reviewer can save a global decision.

## Paper workflow

1. Create a bundle from an authoritative opportunity calculation and enter the
   exact simulated allocation/notes.
2. Confirm sufficient unreserved balance; do not allocate the same paper cash to
   multiple open positions.
3. Record each fill separately with provider leg, actual simulated quantity,
   price, fee, time, and idempotency key.
4. If one leg is missing/partial, retain unmatched exposure. Do not enter the
   planned complete-bundle profit.
5. Cancel only when appropriate; cancellation does not book profit.
6. Settle from explicit outcome evidence. Realized P&L starts at settlement.
7. Correct with an auditable adjustment, never an edited historical fill/ledger
   row.
8. Export CSV and confirm demo/provenance/calculation-version fields before using
   it in research.

Ordinary source-development demo API state resets on restart. Installed Windows
Local Mode instead commits this workflow to SQLite and is covered by a packaged
API stop/restart persistence smoke test. If the portfolio API or local store is
unavailable, the web shows an error and does not invent positions or CSV rows.

## Analytics interpretation

- date controls filter the actual requested UTC interval;
- realized P&L includes settled paper ledger entries only;
- reserved capital is not a loss but is unavailable for another position;
- unrealized P&L is zero when there is no authoritative mark source;
- capture rate needs an explicit signal denominator;
- drawdown depends on the recorded equity series;
- provider/sport/strategy splits are only as complete as record metadata;
- demo metrics do not establish a profitable strategy.

Use Today, 7D, 30D, 90D, YTD, All, and Custom to check the denominator, not to
select the best-looking period after the fact.

## Alerts

Alert rules can be created, edited, enabled/disabled, and deleted. After each
successful post-ingestion cycle, the worker evaluates active strict opportunities
for `new_verified_opportunity` and the UI's `net_roi_above` rule (stored as
`roi_threshold`). Minimum net profit, minimum net ROI, minimum known liquidity,
and maximum quote age are applied
conjunctively. The evaluator recognizes provider, market-type, sport, and league
keys in stored conditions, but the current supported rule API/editor does not
create those scope keys. `liquidity_increase`, `spread_widens`, `quote_stale`,
`market_closed`, `market_settled`, and `provider_offline` can be stored/edited but
are not evaluated in this release. The evaluator deduplicates by
rule/opportunity, respects cooldown, and marks related events withdrawn when the
opportunity is withdrawn or expires. Withdrawn records remain in the database
for audit but are excluded from the active notification list.

Delivery is an owner-scoped in-app database event only. The live page polls
persisted rules/events every 15 seconds while visible and refetches on focus;
closing the page does not stop a separately deployed worker. Conversely, an open
page is not monitoring if the worker is down. No email, SMS, push, or native
Windows notification is sent. The standard installed Windows package has no
worker and does not scan after it closes.

## Baseline replay

Use only time-ordered snapshots and an availability time no later than each
decision. Configure delay, expiry, slippage, fill fraction, and depth requirement
before looking at results. Supply a `conservative_profit` that already includes
relevant costs; replay has no separate fee input. Missing historical L2 remains
an assumption.
Separate tuning periods from chronological evaluation; past paper performance is
not a future-return guarantee.

## Simulated capital controls

This is an illustrative learning policy, not financial advice:

- real-money allocation: `$0`;
- example paper bankroll: `$1,000`;
- complete verified bundle: at most 2% (`$20`);
- one-event unmatched/stress loss: at most 1% (`$10`);
- aggregate unmatched exposure: at most 3% (`$30`);
- unreserved paper cash: at least 50% (`$500`);
- modeled consumption: at most 10% of evidenced displayed depth;
- any unknown material fee/depth/time/rule/currency/outcome: `$0` strict size.

The `$210`/2.20/2.00 reference is a math fixture: `$100 + $110` produces `$220`
in either covered outcome, `$10` gross profit, and approximately `4.7619%` gross
ROI before costs. It is not suggested capital.

## Incident responses

### Provider unavailable/schema changed

Disable or allow backoff on that provider, preserve the error/raw evidence,
classify it unavailable, and inspect current official documentation. Do not map
unknown fields by guess. Re-enable only after fixture and bounded runtime checks.

### Rate limited/quota low

Stop unnecessary polls, honor `Retry-After`/backoff, reduce universe/frequency,
and wait for the documented reset. Do not buy/increase a plan automatically.

### Stale or future timestamps

Exclude affected quotes, inspect provider time semantics and host clock, and
confirm fetch time did not overwrite observation time. Never clamp a negative age
to fresh.

### Worker lease/heartbeat missing

Do not restart multiple replicas blindly. Inspect lease owner/expiry, database
connectivity, and the last ingestion run. Restore one owner, then verify
idempotent normalized writes, quote age, post-ingestion diagnostics, lifecycle
changes, and alert evaluation. A healthy process alone does not prove the scanner
is current; provider observation time and surviving strict evidence are decisive.

### Credential suspected leaked

Disable the connection, rotate/revoke at the provider, replace through the form,
inspect redacted logs/audit activity, and test within budget. Do not email/log the
old key.

### Mode mismatch

Stop the rollout and align web/API configuration. Do not disable the guard.

### Calculation/rule anomaly

Remove the candidate from strict use, retain evidence/version, reproduce every
outcome, add a regression fixture, and require a new review after the repair.

## Shutdown and retention

Shutting web/API does not automatically make a separately deployed worker stop;
manage each resource explicitly. Preserve raw evidence and audit records according
to provider licensing, privacy, and organizational retention policy. Back up and
test restores before pruning. Ordinary development demo process-memory state is
intentionally not a backup target.

For installed Windows Local Mode, uninstall intentionally retains
`%APPDATA%\EDGEOS`. Use **Settings → Data & security** to download a portable
`.edgeos-backup`. Restore validates size, schema, namespaces, domain payloads,
and SHA-256 integrity before replacing simulated local state transactionally.
Keep multiple tested copies because scheduling, rotation, encryption, and remote
retention are not built in. Preserve the original database when investigating
corruption. SQLite and portable backups are not encrypted at rest and must not
be used for provider/Auth/signing secrets.
