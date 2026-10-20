# EDGEOS provider adapters

These integrations were checked against the providers' official documentation on
2026-10-20. They are read-only. None of them places orders or stores sportsbook
credentials.

## Implemented feeds

| Adapter | Active transport | Credentials | Implemented data | Important limitation |
| --- | --- | --- | --- | --- |
| Kalshi | Public REST polling | None for these endpoints | Events, markets, BBO fields, L2 book | The REST book has no guaranteed snapshot timestamp. Asks are derived from the documented binary YES/NO bid relationship. |
| Polymarket International | Public Gamma and CLOB REST polling | None | Events, markets, v1/v2 outcome IDs, CLOB book | This is not Polymarket US. Gamma market IDs and CLOB asset IDs have different roles. |
| Polymarket US | Public Gateway REST polling | None | Events, markets, BBO fields, book | US identifiers and books must never be joined directly to International identifiers. Institutional gRPC access requires separate onboarding and is not implemented. |
| The Odds API | REST polling | `THE_ODDS_API_KEY` | Events and bookmaker offers; per-outcome bet limits when the feed supplies them | Refresh frequency varies. Most sportsbooks expose neither depth nor stake limits through this feed, so missing liquidity remains `None`. |
| EDGEOS Demo | In process | None | One synthetic event, market, and book | Every record is marked `provider=demo`, `data_origin=demo`, and non-executable. |

Only implemented transports appear in each descriptor's `feed_types`. A provider's
documentation may advertise other transports, but EDGEOS does not claim they are
running until an adapter actually implements them.

## Official references

- Kalshi: [market-data quick start](https://docs.kalshi.com/getting_started/quick_start_market_data), [markets](https://docs.kalshi.com/api-reference/market/get-markets), [order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook), and [rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- Polymarket International: [API hosts](https://docs.polymarket.com/getting-started/api), [market discovery](https://docs.polymarket.com/market-data/discover-markets), and [prices/order books](https://docs.polymarket.com/market-data/prices-order-books)
- Polymarket US: [public quick start](https://docs.polymarket.us/getting-started/quickstart), [markets](https://docs.polymarket.us/api-reference/markets/get-markets), [BBO](https://docs.polymarket.us/api-reference/markets/get-market-bbo), and [book](https://docs.polymarket.us/api-reference/markets/get-market-book)
- The Odds API: [v4 guide](https://the-odds-api.com/liveapi/guides/v4/) and [rate-limit guide](https://the-odds-api.com/guide/rate-limit.html)

## Usage

```python
from edgeos.providers import KalshiAdapter, MarketQuery

async with KalshiAdapter() as provider:
    page = await provider.list_markets(
        MarketQuery(limit=100, filters={"status": "open"})
    )
```

The interface is deliberately typed around `EventQuery`, `MarketQuery`, and
`OrderBookQuery`. Polymarket International requires `OrderBookQuery.outcome_id`
because the CLOB book is keyed by a v1 token ID or v2 position ID, not a Gamma
market ID.

## Freshness and missing data

- `provider_timestamp` or `quote_timestamp` is populated only when the provider
  supplies an applicable timestamp.
- `retrieved_at` records when EDGEOS received the response and is never used as a
  substitute for quote time.
- Responses served by the short in-process TTL cache use `data_origin=cache`.
- Unsupported depth, size, resolution rules, and delay remain `None`; adapters do
  not synthesize them.
- The Odds API's `bet_limit`, when present, maps to `maximum_stake`. It is not
  generalized into market liquidity.

The shared HTTP layer applies bounded exponential backoff to transient errors,
honors `Retry-After` where supplied, tracks quota headers, coalesces identical
concurrent reads, and caches short-lived reference responses. Durable ingestion
deduplication remains the responsibility of the database worker using provider IDs
and provider timestamps.
