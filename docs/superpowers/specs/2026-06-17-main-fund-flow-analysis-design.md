# Main Fund Flow Analysis Design

## Goal

Add a clear, actionable main-fund-flow analysis layer to ShortStockMaster using the existing real EastMoney fund-flow data. The feature must explain whether a stock shows strong capital-price confirmation, mild inflow, accumulation watch, distribution risk, main outflow, or insufficient capital support.

## Scope

- Use existing `_capital_flow()` data only. It provides `main_net`, `main_pct`, `large_net`, `super_net`, `mid_net`, and `small_net`.
- Use current quote fields from Tencent: price change, volume ratio, turnover, amount, and price.
- Show the analysis in both ranking rows and stock detail pages.
- Feed the analysis into ranking score and trade-plan decisions.
- Do not claim historical fund-flow backtest validity because local DuckDB `fund_flow` is empty.

## Analysis Rules

The analysis returns a structured object:

- `label`: user-facing classification.
- `rating`: `bullish`, `watch`, `neutral`, or `bearish`.
- `score_delta`: bounded score contribution.
- `summary`: one-sentence explanation.
- `metrics`: today net, today percent, 3-day net, positive-day count, large/super net, small net, volume ratio, turnover, and price change.
- `drivers`: positive reasons.
- `risks`: negative reasons.

Core interpretations:

- Strong capital confirmation: main net is meaningfully positive, main percentage is strong, at least two of the last three days are positive, large/super orders confirm, price is up, and volume ratio is active but not overheated.
- Accumulation watch: main or large/super net is positive while price is flat or slightly down, suggesting possible absorption rather than immediate chase.
- Distribution risk: main net is positive but price is overextended, turnover is very high, or price is not following through.
- Main outflow: main net is clearly negative or main percentage is negative with price weakness.
- Insufficient capital support: no fund-flow rows or very small net flow.

## Data Caveats

EastMoney fund-flow data is model-derived from trade size and active direction. It is useful for same-day screening but is not the same as confirmed institutional holdings. The app must present it as a decision aid, not a guarantee.
