# Market-data provenance and freshness policy

Provider documentation/schema audit: **2026-10-20 (UTC)**. No credentialed or
live-provider connectivity probe was completed, so this date is not an uptime or
compatibility claim. This document combines mandatory provenance policy with
explicit notes on what the current implementation does and does not expose.

## Core rule

EDGEOS never upgrades evidence. A polled aggregator observation is not an executable quote; a midpoint is not an ask; a last trade is not current liquidity; metadata similarity is not settlement equivalence; and missing fees or depth are not zero.

## Required provenance record

Policy target and current boundary differ for raw evidence: the live worker stores
a deterministic `capture_stage=normalized_adapter_contract` envelope and hash.
It does **not** retain the byte-for-byte upstream HTTP response. The table below
describes the long-term reproducibility record; operators must not call the
current envelope a verbatim raw capture.

The normalized event/market records preserve adapter-produced settlement/rule
evidence in metadata rather than discarding it. The local demo adapter maps to the
explicit database provider slug `edgeos_demo`; demo identity must never collide
with a live provider or be admitted to live opportunity generation.

Every normalized quote or book snapshot must retain enough information to reproduce its meaning:

| Field | Requirement |
| --- | --- |
| `provider` / `provider_surface` | Keep distinct values for Kalshi, Polymarket US, Polymarket international Gamma, international CLOB, international Data API, and The Odds API. |
| `source_url` / `source_route` | Official host and route, with secrets and API keys redacted. |
| `provider_market_id` | Original market/condition/token/bookmaker market identifier. Never substitute a canonical ID. |
| `provider_event_id` | Original event ID/ticker when available. |
| `raw_payload_hash` | Hash of the retained capture. Today this is the normalized-adapter envelope; a future verbatim capture must label its stage distinctly. |
| `retrieved_at` | UTC time EDGEOS received the response/message. |
| `provider_timestamp` | Provider-supplied update time when present; nullable, never synthesized. |
| `effective_quote_time` | Provider time if semantically valid. If only retrieval time exists, retain it separately and mark quote delay unknown; do not silently promote it to provider time. |
| `feed_class` | One of the normalized wire values `live_poll`, `cache`, `historical`, or `demo`; a future stream adapter must add an explicit streaming class rather than reusing polling. |
| `price_kind` | At minimum `BID`, `ASK`, `LAST_TRADE`, `MIDPOINT`, `SPORTSBOOK_ODDS`, or `DERIVED_COMPLEMENT`. |
| `price`, `size`, `currency` | Decimal-safe strings/numerics. `size` is nullable when the source provides none. |
| `book_depth` | Exact captured levels if supplied; null when unavailable. Never create nominal depth. |
| `is_in_play`, `market_status` | Provider values plus mapping version; unknown stays unknown. |
| `rules_source` / `rules_hash` | Versioned settlement text/source used for matching. |
| `fee_source` / `fee_effective_at` | Versioned official fee evidence used in the calculation; nullable means fee unknown, not zero. |
| `adapter_version` / `schema_version` | Parser and normalized-schema versions. |
| `request_id` / sequence metadata | Response request ID, cursor, stream sequence/hash, or null when not supplied. |

## Feed-class semantics

| Class | Meaning | Scanner treatment |
| --- | --- | --- |
| `live_poll` | Current REST response obtained on a schedule. | Show observation age; never label instant/streaming solely because polling is frequent. |
| `cache` | Served from EDGEOS/provider cache rather than freshly fetched for the request. | Show cache age and upstream timestamp separately. |
| `historical` | Provider or locally archived data for a past timestamp. | Backtesting/research only; prohibit admission to the live opportunity set. |
| `demo` | Synthetic fixture shipped for product demonstration. | Visibly label `DEMO DATA`; isolate from live analytics and evidence. |

No provider WebSocket/stream sink is implemented in this release. The schema and
policy describe how a future stream must behave; they are not a claim of current
streaming data.

## Provider-specific interpretation

### Kalshi

- Preserve fixed-point dollar price/size fields and the original legacy fields if both occur.
- Kalshi order-book sides are bid liquidity. If an opposite-side bid is transformed into an ask through the documented binary complement, store `price_kind=DERIVED_COMPLEMENT`, the source level, and derivation formula.
- A future WebSocket adapter must initialize books from a snapshot and apply
  deltas only while stream integrity is known. No such adapter is active today.
- `last_price` is trade history, not an executable side.
- Store `rules_primary`, `rules_secondary`, expiration/close conditions, settlement source/value, and early-close/void information. Unknown rule fields prohibit `VERIFIED MATCH`.

### Polymarket US

- Use a dedicated schema and adapter. No US record may carry a Gamma/CLOB/Data API route as its source.
- The documented public REST source is `https://gateway.polymarket.us/v1`; the
  official quickstart states that its public market-data endpoints need no
  authentication. Preserve the exact `/events`, `/markets`, BBO, book, settlement,
  or price-history route that produced each record.
- The public REST adapter is `WORKING` only while its runtime schema and health
  probes pass. Institutional gRPC remains `NEEDS CONFIGURATION` and must use its
  documented sequence/recovery contract before data is admitted.
- A `positionIds` request field is not approved for use until verified in the exact official US endpoint schema.

### Polymarket international

- Gamma is metadata/discovery; CLOB is the price/book source; Data API is research/account-address data. Record which service produced each field rather than merging provenance.
- Gamma market lists and CLOB cursor-based market lists have different response envelopes. Parse them with service-specific validators.
- For hypothetical purchases, executable sizing should walk CLOB asks for the
  exact outcome token. The current calculation engine accepts normalized
  depth-adjusted offer inputs and does not itself route through arbitrary raw L2
  levels. A last trade, midpoint, or Gamma display price cannot replace an ask.
- Store token ID, condition/market ID, outcome, event ID, tick size, minimum size, negative-risk flag, and book timestamp/hash. Never infer outcome order from array position alone.
- Public price history/trades do not contain historical L2 unless explicitly returned. Backtests must state when they substitute assumptions for unavailable depth.

### The Odds API

- Classify all odds as `live_poll`. Store the API's market-level `last_update`
  when present and the separate EDGEOS retrieval time.
- The API does not provide quote size/depth, user stake limits, acceptance state, or guaranteed execution. Leave those fields null and apply a liquidity/verification gate.
- The current guide deprecates bookmaker-level `last_update`; do not use it when market-level time is present.
- A market can cease updating before it disappears from responses. Detect stale data by timestamp, not presence alone.
- Historical snapshots are `historical` even when the requested timestamp is
  recent. Store requested and provider-returned snapshot times separately.
- Provider bookmaker coverage must be based on the actual response. Do not convert a configured target-book list into claimed coverage.

## Freshness and availability state machine

Thresholds are configuration by provider, sport/market, and feed class; they are not universal constants.

1. `CURRENT`: observed age is within the configured evidence-backed threshold and provider health is good.
2. `AGING`: age exceeds the preferred threshold but not the hard scanner limit; visible warning required.
3. `STALE`: age exceeds the hard limit, timestamp is absent when one is required, or stream integrity is lost. Exclude from strict arbitrage.
4. `UNAVAILABLE`: request failed, authentication/configuration is missing, schema validation failed, or provider reports no data.
5. `UNKNOWN_DELAY`: provider publishes no dependable latency and only retrieval time is known. Display that label rather than `real time`.

Compute and retain:

```text
retrieval_age = evaluation_time - retrieved_at
provider_age  = evaluation_time - provider_timestamp  # only when supplied
transport_lag = retrieved_at - provider_timestamp     # only when semantics permit
```

Clock skew that makes an age negative is a data-quality failure, not zero age.

## Opportunity admission gates

An item may enter the strict theoretical-net-arbitrage feed only when:

- every outcome in the modeled partition is covered;
- settlement equivalence is `VERIFIED MATCH` using versioned rule evidence;
- each acquisition price is an executable ask/offer or is explicitly modeled as non-executable research evidence;
- required size/depth is known and sufficient, including discrete quantity/rounding;
- all price inputs are `CURRENT` and not historical/demo;
- fee schedules, fee rounding, currency/transfer costs, and effective dates are known;
- every scenario after fees and partial-fill assumptions meets the configured profit constraint; and
- provider/runtime health is acceptable.

If any gate fails, retain the candidate for research with a reason code, but do not call it guaranteed profit and do not rank it as strict arbitrage.

Suggested reason codes include `RULES_UNKNOWN`, `SETTLEMENT_MISMATCH`, `QUOTE_STALE`, `DELAY_UNKNOWN`, `DEPTH_UNKNOWN`, `INSUFFICIENT_DEPTH`, `FEE_UNKNOWN`, `NON_EXECUTABLE_PRICE`, `STREAM_GAP`, `SCHEMA_CHANGED`, and `PROVIDER_UNAVAILABLE`.

## Historical testing and no-look-ahead guarantees

- Evaluate a signal using only records whose observation time and availability
  time are no later than the decision time. The current baseline backtest exposes
  `observed_at` and `available_at`; future persisted replay must retain the same
  no-look-ahead distinction.
- Never backfill a missing historical fee, rule, depth, or settlement fact using a version learned later without marking it as an explicit retrospective assumption.
- Preserve ingestion order and revisions. Corrections create new records; they do not overwrite the evidence seen by an earlier simulation.
- Provider historical snapshots must retain the provider-returned snapshot time, not merely the requested time.
- If full depth was not captured, report `historical_depth_available=false` and isolate results that use assumed liquidity/slippage.
- Chronological train/validation/test splits must be based on availability time, not final event time alone.

## Demo-data isolation

- Demo rows use impossible-to-confuse provider IDs and `feed_class=demo`.
- The UI must show a persistent `Demo data` badge whenever demo records are in scope.
- Demo records may demonstrate calculations but may not be described as observed opportunities, live quotes, capture rate, or user profitability.
- Demo and real data must not be combined in default KPI, alert, or portfolio aggregates.

## Operational diagnostics

For each provider/surface expose:

- configured status and runtime status separately;
- last attempt, last success, last validated message, and last provider timestamp;
- HTTP/gRPC/WebSocket status category, redacted error code, reconnect count, and backoff deadline;
- rolling request/credit usage where supplied;
- stream sequence/resnapshot state;
- parser/schema-rejection counts; and
- active-instrument count by feed class.

Errors must not be converted into empty successful responses. A schema change is `UNAVAILABLE` plus an alert until reviewed.

## Source register

All sources below are provider-owned and were reviewed for schema/documentation
contract on **2026-10-20**; none is a live-connectivity result:

- Kalshi: [documentation index](https://docs.kalshi.com/llms.txt), [API reference](https://docs.kalshi.com/api-reference), [WebSockets](https://docs.kalshi.com/websockets), [authentication](https://docs.kalshi.com/getting_started/api_keys), [rate limits](https://docs.kalshi.com/getting_started/rate_limits), [order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook), [fees](https://kalshi.com/docs/kalshi-fee-schedule.pdf).
- Polymarket US: [documentation](https://docs.polymarket.us), [quickstart](https://docs.polymarket.us/getting-started/quickstart), [events](https://docs.polymarket.us/api-reference/events/get-events), [markets](https://docs.polymarket.us/api-reference/markets/get-markets), [market book](https://docs.polymarket.us/api-reference/markets/get-market-book), [data guide](https://docs.polymarket.us/data-guide/overview), [documentation index](https://docs.polymarket.us/llms.txt).
- Polymarket international: [documentation](https://docs.polymarket.com),
  [API introduction](https://docs.polymarket.com/getting-started/api),
  [market discovery](https://docs.polymarket.com/market-data/discover-markets),
  and [prices/order books](https://docs.polymarket.com/market-data/prices-order-books).
- The Odds API: [v4 guide](https://the-odds-api.com/liveapi/guides/v4/),
  [rate-limit guide](https://the-odds-api.com/guide/rate-limit.html),
  [error behavior](https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html),
  and [historical data](https://the-odds-api.com/historical-odds-data/).

Provider documentation and terms change. Re-verify the source register before enabling a production connector or changing any fee, rate-limit, authentication, or settlement assumption.
