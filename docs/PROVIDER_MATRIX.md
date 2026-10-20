# Provider capability matrix

Source-contract audit updated **2026-10-20 (UTC)** from the provider-owned links
embedded in the adapters. The bounded public-read evidence below verifies
discovery and market retrieval, not strict scanner execution, trading access,
or permission for a particular user or jurisdiction. Adapter tests also use
deterministic official-shaped fixtures. `UNKNOWN` means the cited
documentation did not establish the fact; it must not be filled with a guess.

## Runtime evidence — 2026-10-20 UTC

The actual adapters completed the following unauthenticated reads in the managed
runtime at **17:46:34–17:46:35 UTC**, using the documented public hosts. No paid
API quota or trading endpoint was used.

| Adapter | Successful HTTP calls | Events returned | Markets returned | Markets with provider timestamps | Outcomes with known ask size |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kalshi | 2 | 2 | 2 | 2 | 2 |
| Polymarket US | 2 | 2 | 2 | 2 | 0 |
| Polymarket International | 2 | 2 | 2 | 2 | 0 |

All six calls returned HTTP 200. Counts are deliberately bounded samples, not
coverage claims. The timestamps in this table are market-record timestamps;
discovery timestamps do not establish the freshness of an executable book.
An earlier attempt without the command's network permission failed before
receiving provider data; the successful reads supersede that connectivity check.
The runtime reports no configured secret bindings, and The Odds API was not
queried. These samples do **not** demonstrate a fully normalized sports matchup,
reviewed equivalent settlement rules, effective fees, complete executable depth,
or a strict arbitrage opportunity.

Provider HTTP retries now retain an adapter-wide `Retry-After` deadline. A
server-directed wait longer than the local retry sleep budget ends the current
request; subsequent calls make no HTTP request before that deadline and health
is `DELAYED`. Reused worker adapters preserve this state across cycles, not
process restarts. A failed request after a previous success reports
`DISCONNECTED` once any cooldown ends. Contract tests cover these cases.

## Status meanings

| Status | Meaning in EDGEOS |
| --- | --- |
| `WORKING` | An implemented read-only path can be exercised without a private account or paid subscription. Runtime health still determines whether it is presently available. |
| `NEEDS CONFIGURATION` | An EDGEOS adapter exists, but a key, approved account, subscription, or deployment setting is required. |
| `UNAVAILABLE` | EDGEOS has no implemented adapter for the capability, or no verified permitted source is configured. No fabricated substitute is allowed. |

## Summary

| Provider surface | EDGEOS status | Transport | Authentication for read-only market data | Book/order depth | Streaming | Provider history |
| --- | --- | --- | --- | --- | --- | --- |
| Kalshi public REST | `WORKING` for documented public endpoints; runtime-probe before presenting data | REST/JSON | Public market-data routes do not require private-account signing; private routes do | Yes, price-level book | No (REST poll) | Trades and candlesticks are documented; this is not guaranteed full historical L2 |
| Kalshi WebSocket | `UNAVAILABLE` in this release; upstream access also requires credentials | WebSocket | API-key handshake with signed Kalshi headers | Snapshot plus deltas | Yes | Adapter and durable stream sink are not implemented |
| Polymarket US public REST | `WORKING` adapter for the documented Gateway contract; runtime-probe before presentation | REST/JSON | No authentication for documented public endpoints | Yes, BBO and L2 | No (REST adapter); gRPC is documented separately | Price-history endpoint documented; locally persist L2 for full-depth replay |
| Polymarket US institutional stream | `UNAVAILABLE` in this release; upstream access also requires onboarding | gRPC streaming | Institutional onboarding and credentials | BBO and L2 | Yes | Adapter and durable stream sink are not implemented |
| Polymarket international Gamma API | `WORKING` for public metadata, subject to runtime health and terms | REST/JSON | None documented for public metadata | No | No | Metadata lifecycle, not execution-quality history |
| Polymarket international CLOB REST | `WORKING` for public order-book reads, subject to runtime health | REST/JSON | Public market-data endpoints are documented without trading credentials; trading routes require credentials | Yes | No (REST adapter) | The EDGEOS adapter does not fetch price history |
| Polymarket international WebSocket | `UNAVAILABLE` in this release | WebSocket | Public market channel is documented; user channel is authenticated | Yes | Yes upstream | Adapter, sequence recovery, and durable stream sink are not implemented |
| Polymarket international Data API | `UNAVAILABLE` in this release | REST/JSON | Public research queries are documented separately from trading auth | No canonical L2 book | No | No EDGEOS Data API methods are implemented |
| The Odds API v4 | `NEEDS CONFIGURATION` | REST/JSON polling | API key in `apiKey` query parameter | No | No | Paid-plan historical snapshots |
| Fliff | `UNAVAILABLE` in this release | None configured | No official, permitted read-only market-data contract was verified for this release | Unknown | Unknown | No adapter, scraping, account automation, or geographic-control bypass is implemented |

## 1. Kalshi

### Endpoint contract

- Official examples currently use REST base URL `https://external-api.kalshi.com/trade-api/v2`. Keep this base URL configurable: do not silently fall back to a legacy or guessed host.
- Relevant public research routes include:
  - `GET /events` and `GET /events/{event_ticker}` for discovery and event metadata.
  - `GET /markets` and `GET /markets/{ticker}` for market identity, state, rules, top-of-book fields, times, and settlement metadata.
  - `GET /markets/{ticker}/orderbook` for depth.
  - `GET /markets/trades` for public trades.
  - documented candlestick routes for time-bucketed market history.
  - exchange status/announcement routes for operational context.
- The exact current WebSocket URL was not re-established by the retrieved official page set in this checkpoint. Treat it as configuration sourced from the current [Kalshi WebSocket documentation](https://docs.kalshi.com/websockets) and fail closed if absent; do not derive it by replacing `https` with `wss`.

### Authentication and rate limiting

- Signed private REST calls use `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`; the signature is an RSA-PSS signature over Kalshi's documented request payload. EDGEOS v1 does not need order-entry routes.
- The official WebSocket reference requires an API-key-authenticated connection. Market-data subscriptions must not reuse sportsbook credentials or expose a private key to the browser.
- Kalshi documents tiered and endpoint-weighted limits. One current endpoint page, for example, states a 2-token cost while the default is 10 tokens. A single universal requests/second value was **not verified** for this checkpoint. Read the current tier page, honor `429`, add jittered exponential backoff, and record actual response diagnostics.

### Quote and book semantics

- Current market representations document explicit fixed-point/dollar fields such as `yes_bid_dollars`, `yes_ask_dollars`, `yes_bid_size_fp`, `yes_ask_size_fp`, `no_bid_dollars`, `no_ask_dollars`, `last_price_dollars`, `volume_fp`, and `open_interest_fp`. Legacy integer-cent fields may coexist; prefer the documented fixed-point fields and preserve the raw response.
- The REST order book is bid-side liquidity for YES and NO. A missing displayed ask must only be derived through the documented binary complement (for example, a YES ask from a NO bid), never from `last_price` or midpoint. Store whether a value was provider-supplied or derived.
- WebSocket `orderbook_delta` messages are deltas, not standalone books. Initialize from the documented snapshot, apply updates in sequence, and resnapshot on disconnect, sequence uncertainty, or integrity failure.
- Market objects carry rule and lifecycle information, including primary/secondary rules and close/expiration/settlement fields. Preserve these verbatim; a shared title does not prove settlement equivalence.

### Fees and limitations

- Kalshi fees are not safely modeled as zero. The exchange publishes a fee schedule and supports series/market-specific fee type, multiplier, and waiver metadata. Version the effective schedule and retain the exact source used by every simulation.
- **UNKNOWN:** a single evergreen fee formula applicable to every market at the
  2026-10-20 documentation checkpoint was not established. If fee metadata or
  the effective official schedule cannot be resolved, the opportunity must be
  `REVIEW REQUIRED`, not strict arbitrage.
- Public trades/candles do not prove historical executable depth. EDGEOS must persist received snapshots/deltas if historical depth-aware backtests are required.

Official sources (documentation/schema audit 2026-10-20; bounded public reads above): [Kalshi documentation index](https://docs.kalshi.com/llms.txt), [API reference](https://docs.kalshi.com/api-reference), [WebSockets](https://docs.kalshi.com/websockets), [authentication](https://docs.kalshi.com/getting_started/api_keys), [rate limits and tiers](https://docs.kalshi.com/getting_started/rate_limits), [market order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook), [markets](https://docs.kalshi.com/api-reference/market/get-markets), [trades](https://docs.kalshi.com/api-reference/market/get-trades), [fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).

## 2. Polymarket US — a separate regulated product

Polymarket US must have its own adapter, identifiers, credentials, schemas, eligibility flags, and provenance label. It must **never** be pointed at the international Gamma, Data, or CLOB hosts.

### Documented public REST contract

- Official REST base URL: `https://gateway.polymarket.us/v1`.
- The official quickstart explicitly states that public market-data endpoints do
  not require authentication.
- Implemented, documented read-only routes are `GET /events`, `GET /markets`, and
  `GET /markets/{slug}/book`. The official API reference also documents market
  lookup, BBO, settlement, and price-history routes.
- The documented book envelope contains `marketData`, `bids`, `offers`, price
  objects (`value` plus `currency`), quantities, state, market statistics, and a
  `transactTime` provider timestamp when supplied. EDGEOS keeps it separate from
  HTTP retrieval time.
- Contract tests cover official-shaped event, market, and book fixtures. Bounded
  public event and market reads succeeded as recorded above; this alone does not
  validate full book ingestion or strict opportunities.

### Still configuration-gated or unknown

- Institutional gRPC delivery requires separate onboarding/configuration and is
  not claimed by the REST adapter.
- The gRPC sequence/recovery contract, stable retention guarantees, current rate
  limits, and effective fee schedule remain deployment-time verification items.
- Support for an international-style `positionIds` request field is not documented
  for this product. Code must not send or depend on it.

Official sources (documentation/schema audit 2026-10-20; bounded public reads above): [Polymarket US documentation](https://docs.polymarket.us), [quickstart](https://docs.polymarket.us/getting-started/quickstart), [Get Events](https://docs.polymarket.us/api-reference/events/get-events), [Get Markets](https://docs.polymarket.us/api-reference/markets/get-markets), [Get Market Book](https://docs.polymarket.us/api-reference/markets/get-market-book), [Get Market BBO](https://docs.polymarket.us/api-reference/markets/get-market-bbo), [US data guide overview](https://docs.polymarket.us/data-guide/overview), [US documentation index](https://docs.polymarket.us/llms.txt).

## 3. Polymarket international public market data

This is informational research access to the international product. It does not imply user eligibility, trading permission, or access to Polymarket US.

### Separate official services

| Service | Base URL | Purpose and relevant routes |
| --- | --- | --- |
| Gamma API | `https://gamma-api.polymarket.com` | Discovery and metadata. Common documented routes include `GET /markets`, `GET /events`, and related series/tag/sports discovery. |
| CLOB REST | `https://clob.polymarket.com` | Execution-quality public market data. Documented routes include `GET /book?token_id=...`, batch books, price/BBO/spread/last-trade endpoints, market discovery, price history, and fee-rate lookup. |
| CLOB WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Public market-channel snapshots and updates for subscribed asset/token IDs. The separate user channel is authenticated and out of scope. |
| Data API | `https://data-api.polymarket.com` | Research data such as public trades, activity, and wallet-position views where documented. It is not the order-book source. |

### Schema boundaries

- The implemented Gamma routes are `GET /markets/keyset` and
  `GET /events/keyset`, confirmed by current official documentation. They return
  `markets` or `events` arrays in an envelope with `next_cursor`; pass the exact
  cursor back as `after_cursor`. Do not parse these routes as a top-level array.
- CLOB market-list endpoints use a cursor/keyset envelope containing market data and a next cursor (documented examples use fields such as `data` and `next_cursor`). Do not parse it as the Gamma array.
- CLOB books contain bid and ask price levels with sizes plus book metadata such as token/asset ID and timestamp/hash where supplied. Hypothetical buys must use asks and be walked through depth; midpoint and last trade are not executable asks.
- Binary/multi-outcome relationships come from event/market metadata and outcome
  asset IDs. Current official discovery documentation distinguishes protocol
  `v1` `clobTokenIds` from `v2` `positionIds`. The CLOB `/book` request still names
  its asset lookup parameter `token_id`. The adapter preserves the protocol
  version and uses the corresponding asset ID; neither is a Polymarket US ID.
- The adapter distinguishes international v1 `USDC` and v2 `pUSD` from US
  contracts denominated in `USD`. This currency label is not proof of a 1:1
  conversion or a verified execution currency in the bounded discovery sample.

### Authentication, history, rate limits, and fees

- Public Gamma, public CLOB market data, and the public market WebSocket channel do not require L2 trading credentials in the official data documentation. Trading/user endpoints use separate wallet-derived L1/L2 authentication and are outside EDGEOS v1.
- The current prices/order-books guide documents
  `https://data-api.polymarket.com/v2/prices-history`; EDGEOS does not implement
  that history route. Price/trade history does not establish historical
  full-depth order books. Record received books locally for depth-aware replay.
- Official limits are endpoint-specific and can change. The exact 2026 limits were **not captured as a stable contract** here. Apply conservative client-side budgets, honor throttling/`429`, and use the current official rate-limit table when configuring production.
- Fees vary by market/program and can change. Resolve the documented fee-rate value for the exact token/market and effective time. If unavailable, mark fees `UNKNOWN` and exclude the candidate from strict net-arbitrage ranking. Never infer zero from a missing field.

Official sources: [Polymarket developer documentation](https://docs.polymarket.com),
[API introduction](https://docs.polymarket.com/getting-started/api),
[market discovery](https://docs.polymarket.com/market-data/discover-markets), and
[prices/order books](https://docs.polymarket.com/market-data/prices-order-books).
The adapter descriptor treats those provider-owned pages as its contract source;
bounded public discovery and market retrieval are recorded above.

## 4. The Odds API v4

### Endpoint contract

- Base URL: `https://api.the-odds-api.com` (IPv6-specific host is also documented).
- Authentication: subscription key in the `apiKey` query parameter. Keep it server-side and redact URLs before logging.
- Relevant REST endpoints:
  - `GET /v4/sports` — in-season sport keys; no usage-credit charge according to the guide.
  - `GET /v4/sports/{sport}/events` — in-play/prematch event identity without odds; no usage-credit charge according to the guide.
  - `GET /v4/sports/{sport}/odds` — current/recent bookmaker odds for requested regions/markets.
  - `GET /v4/sports/{sport}/events/{eventId}/odds` — one event, including additional markets where available.
  - `GET /v4/sports/{sport}/events/{eventId}/markets` — recently seen bookmaker market keys, explicitly not a complete supported-market catalog.
  - `GET /v4/sports/{sport}/scores` — live/recent scores for covered sports; the guide states live scores update approximately every 30 seconds.
  - paid-plan history under `/v4/historical/sports/...` for sport snapshots, historical events, and event odds.

### Freshness, quota, and history

- This is polling, not streaming. The odds endpoint is not proof that a price remains wagerable or that any requested stake is accepted.
- Use the market-level `last_update` supplied by the API; the guide deprecates bookmaker-level `last_update`. A suspended/closed market can stop updating and is removed after roughly 15 minutes, so absence and staleness are distinct.
- Every relevant response exposes `x-requests-remaining`, `x-requests-used`, and `x-requests-last`. Quota cost depends on returned/requested markets and regions; historical calls are substantially more expensive. Persist these headers in provider-health telemetry.
- The official error guide currently states a 30 API-calls/second burst limit and recommends spacing retries after `429`. Plan credits are a separate limit.
- Historical odds are paid-plan only. The guide documents snapshots from 2020-06-06 at 10-minute intervals and, from September 2022, at 5-minute intervals. The historical route returns the closest snapshot at or before the requested timestamp. Additional event markets are documented from 2023-05-03. This cadence is not order-level history.

### Explicit limitations

- No general sportsbook order-book depth, fill probability, or streaming feed is
  supplied. The adapter retains optional outcome `bet_limit` only when returned;
  an absent limit or currency remains `UNKNOWN`. An aggregator's reported limit
  does not establish account-specific accepted stake or an executable order book.
- Odds are aggregator observations, not guaranteed executable quotes. Use `THEORETICAL`/`POLLING` provenance and a configured maximum quote age.
- The API does not provide a universal settlement-rule contract sufficient to prove equivalence across sportsbooks. EDGEOS must maintain separately sourced, reviewed rule metadata; otherwise classify cross-provider matches `REVIEW REQUIRED`.
- Sportsbook coverage is dynamic by region, sport, and market. Do not claim DraftKings, FanDuel, BetMGM, Caesars, bet365, Fanatics, ESPN BET/a successor, or any other book for a specific request until its bookmaker key is actually returned and timestamped.
- Sportsbook commissions, payout rounding, limits, and user-specific availability are not supplied. They are `UNKNOWN`/not applicable to this feed, not zero.

Official sources: [The Odds API v4 guide](https://the-odds-api.com/liveapi/guides/v4/),
[rate-limit guide](https://the-odds-api.com/guide/rate-limit.html),
[API error codes](https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html),
[historical odds](https://the-odds-api.com/historical-odds-data/), and
[bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html).

## Production acceptance gate

A connector changes from `NEEDS CONFIGURATION` or `UNAVAILABLE` to runtime `WORKING` only after all of the following are recorded:

1. Current official base URL and endpoint schema match the adapter.
2. Authentication succeeds without exposing credentials to the client.
3. A real response passes strict validation and is stored with retrieval and provider timestamps.
4. Rate-limit telemetry and retry behavior are verified.
5. Quote provenance and feed class are visible in the UI.
6. Rules and effective fee source are available, or the opportunity is excluded from verified net arbitrage.
7. The connector's permitted use has been reviewed for the deployment and jurisdiction.

