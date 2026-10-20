# Quantitative model and assumptions

## Complete outcome cash flow

EDGEOS evaluates a proposed bundle over an explicit, mutually exclusive scenario
set `S`. For leg `l` and scenario `s`:

- `q_l` is the filled quantity after increments and available depth;
- `c_l` is acquisition cash cost at the normalized input price plus the supplied
  slippage assumption; a caller may first derive that price from captured depth;
- `p_l(s)` is settlement cash returned per filled unit in scenario `s`;
- `f_l` is the documented fee under the effective-dated fee schedule;
- `x_l` is any modeled transfer or currency-conversion cost.

The bundle result is:

```text
net(s) = Σ_l q_l × p_l(s) − Σ_l (c_l + f_l + x_l)
```

The conservative result is `min(net(s) for s in S)`. A bundle is not admitted to
the strict feed unless every valid settlement scenario is represented and this
minimum remains positive after all configured costs.

Refund, push, and void outcomes are scenarios, not annotations. When a rule cannot
be mapped to a cash return, settlement equivalence is unknown and the pair is sent
to review.

## Reciprocal-odds sizing

For a complete set of decimal odds `d_i` with compatible settlement:

```text
inverse_sum = Σ_i 1 / d_i
stake_i     = capital × (1 / d_i) / inverse_sum
gross_return = capital / inverse_sum
```

This continuous solution is only a reference case. Platform currency precision,
minimum stake, contract increments, depth, fees, and per-provider balances can make
the equal-payout answer infeasible or negative. Financial contract
`edgeos.cashflow.v2` maximizes the minimum modeled outcome subject to the total cash
cap, per-provider capital caps, minimum quantities, quantity increments, observed
maximum quantities, slippage, and fee rounding. It exhaustively evaluates small
discrete state spaces (up to the engine's bounded state limit). For larger spaces it
uses a deterministic payout-target search and a bounded increment-neighborhood
search. The response identifies which method was used; the large-space method is
not a mathematical proof of the global optimum.

Unused cash stays `unallocated_capital`; the optimizer does not spend capital only
to make the displayed allocation larger. `deployed_capital` and `capital_lockup`
mean acquisition/stake cash plus modeled fees. They are not account balances and do
not include a guessed value for unknown transfer, conversion, or withdrawal costs.

Validation fixture:

```text
d = [2.20, 2.00]
capital = 210.00
inverse_sum = 1/2.20 + 1/2.00 = 0.95454545…
stakes = [100.00, 110.00]
gross return = 220.00
gross profit = 10.00
gross ROI = 4.761904…%
```

## Prediction-market contracts

A hypothetical purchase uses asks and a hypothetical sale uses bids. Last trade
and midpoint may be displayed as market context but are not executable inputs.
Provider adapters expose sorted order-book levels, but the current reusable
simulator accepts a single acquisition price, an explicit maximum quantity, and a
slippage assumption per offer. It does not yet walk a raw multi-level book. A
caller must first derive the depth-adjusted price from observed levels and retain
that evidence; otherwise strict execution eligibility must fail closed. Contract
quantities are quantized to the supplied venue increment before cash flows are
evaluated.

Each simulation input carries a versioned fee schedule and the engine checks its
effective date and rounding increment. The implemented generic schedule supports a
proportional charge on cash cost, a per-contract amount, and a fixed per-order
amount. It does not yet encode every provider's winnings-based or market-specific
curve. Automatic provider/product fee-schedule selection is not wired in this
release. If the relevant schedule is missing, unknown, out of its effective range,
or cannot be represented, strict eligibility fails. An explicit zero-fee research
assumption is reported as an assumption; it is not transformed into verified cost
evidence.

## Staleness and availability

Quote age uses the provider quote timestamp and the server's calculation time.
Retrieval time is retained separately and never refreshes an old provider quote.
`unknown` is preserved when an upstream timestamp is absent. Cross-provider quote
timestamp skew is also reported and can fail the configured gate. A quote can be
informationally current without being immediately executable; polling feeds are
labelled as snapshots.

Depth `unknown` is different from unlimited depth. Strict sizing is capped only by
observed depth or a clearly named research assumption. An upstream book with no
size field cannot support a verified liquidity claim.

## Break-even and stress interpretation

For each fixed allocation the engine searches for the adverse single-leg price at
which the minimum all-outcome result reaches break-even. This is a local,
one-leg-at-a-time sensitivity with other quantities and prices held fixed; it is not
a forecast of fill probability or market movement.

`POST /api/v1/simulate/scenarios` evaluates caller-supplied payout matrices for
partial fills, rejected legs, pushes, refunds, voids, and other asymmetric cases.
Those scenarios are useful only when the caller has explicitly represented every
material outcome. The public simulator never upgrades user text to settlement
verification, so manual Edge Lab results remain research-only even when their
modeled minimum is positive.

## Matching

Fuzzy text and time similarity generate candidates only. Verification compares:

- sport, league, competitors, scheduled start, market type, and period;
- outcome exhaustiveness and thresholds;
- overtime, tie, push, cancellation, postponement, and void behavior;
- settlement authority and any provider-specific resolution clause.

Unknown material rules yield `review_required`; incompatible rules yield
`rejected`. Neither is allowed into the strict arbitrage feed.

## Ranking

Ranking occurs only after hard eligibility gates. The implemented transparent
components are conservative net profit, net ROI, executable size, quote age,
modeled slippage, partial-fill risk, optional stability evidence, and settlement
confidence. Update-frequency and opportunity-duration features require durable
snapshots and are not scored in this release.

The UI also exposes direct sort orders for theoretical ROI, dollar profit, lowest
execution risk, match confidence, and liquidity. A composite score never suppresses
the underlying negative scenario.

## Historical tests

The baseline replay orders snapshots by observation time, refuses an
`available_at` earlier than observation time, applies fixed/seeded randomized delay,
and rejects a signal whose supplied `valid_until` has elapsed. It applies configured
slippage and partial-fill ratios to that same immutable snapshot; it does not yet
look up an independently captured execution book at `t + delay`. Missing historical
depth is reported as an assumption and cannot be backfilled from a later book.

No trained predictive model ships in this release. Any future performance or
calibration model must use chronological train, validation, and test windows and
record its data selection, fee schedule, latency distribution, and slippage
settings so results can be reproduced.

