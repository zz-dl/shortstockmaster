# Main Fund Flow Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured main-fund-flow analysis layer to ShortStockMaster and surface it in ranking, detail, and trade decisions.

**Architecture:** Keep the implementation inside `app.py` to match the current single-file Flask app pattern. Add one pure helper, `_analyze_main_fund_flow()`, then call it from `_short_signal_score()` and from `/api/rank` after real capital data is fetched. Update `static/index.html` to render the structured result.

**Tech Stack:** Python 3.11, Flask, plain JavaScript, existing EastMoney and Tencent quote data.

## Global Constraints

- Do not use fake fund-flow proxies based on price change times turnover.
- Do not claim historical fund-flow backtest validity because local DuckDB `fund_flow` is empty.
- Keep Render free-plan compatible; no new paid data source or background cron.
- Keep UI mobile-first and concise.

---

### Task 1: Add Pure Main-Fund-Flow Analyzer

**Files:**
- Modify: `F:\ShortStockMaster\app.py`
- Test: `F:\ShortStockMaster\scripts\test_main_fund_flow_analysis.py`

**Interfaces:**
- Produces: `_analyze_main_fund_flow(capital_flow: list, quote: dict | None = None) -> dict`
- Consumes: existing `_to_float()` and `_clip()`

- [x] **Step 1: Write failing tests**

Create tests for strong confirmation, accumulation watch, distribution risk, outflow, and no-data behavior.

- [x] **Step 2: Verify tests fail**

Run `python scripts/test_main_fund_flow_analysis.py`; expected failure is import or missing function.

- [x] **Step 3: Implement analyzer**

Add `_analyze_main_fund_flow()` near `_capital_flow()`.

- [x] **Step 4: Verify tests pass**

Run `python scripts/test_main_fund_flow_analysis.py`.

### Task 2: Feed Analyzer Into Scores And API Payloads

**Files:**
- Modify: `F:\ShortStockMaster\app.py`
- Test: `F:\ShortStockMaster\scripts\test_rank_strategy.py`

**Interfaces:**
- Consumes: `_analyze_main_fund_flow()`
- Produces: `fund_flow_analysis` on rank and detail rows.

- [x] **Step 1: Add regression tests**

Assert that strong fund-flow analysis improves candidate score and that distribution/outflow blocks or downgrades candidates.

- [x] **Step 2: Verify tests fail**

Run `python scripts/test_rank_strategy.py`.

- [x] **Step 3: Wire analysis into `_short_signal_score()` and `/api/rank`**

Use `fund_flow_analysis["score_delta"]` rather than repeating raw `capital_net` thresholds.

- [x] **Step 4: Verify tests pass**

Run `python scripts/test_rank_strategy.py`.

### Task 3: Render Analysis In Mobile UI

**Files:**
- Modify: `F:\ShortStockMaster\static\index.html`

**Interfaces:**
- Consumes: `fund_flow_analysis` returned by `/api/stock` and `/api/rank`.

- [x] **Step 1: Update ranking row text**

Show label, today main net, and summary on each ranking item.

- [x] **Step 2: Update stock detail page**

Add a `主力资金分析` card above the existing net-flow bars.

- [x] **Step 3: Verify by local API/browser smoke**

Run local Flask app and check the page contains the new labels without JavaScript console errors.

### Task 4: Final Verification And Deployment

**Files:**
- No new code files.

**Interfaces:**
- Uses Render API service `shortstockmaster`.

- [x] **Step 1: Run focused test suite**

Run all existing script tests plus the new test.

- [ ] **Step 2: Commit and push**

Commit code and docs to GitHub.

- [ ] **Step 3: Trigger Render deploy**

Use the existing saved Render API key environment variable for ShortStockMaster.

- [ ] **Step 4: Verify production**

Hit production `/api/stock` or `/api/rank` and confirm `fund_flow_analysis` is present.
