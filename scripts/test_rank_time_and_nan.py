import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module

A_SHARE = "A\u80a1"
BUY = "\u4e70\u5165"
BULLISH = "\u77ed\u7ebf\u505a\u591a"
GREEDY = "\u8d2a\u5a6a"
TEST_NAME = "\u6d4b\u8bd5\u80a1\u4efd"


def test_rank_api_passes_report_time_to_detail_plan():
    original_fetch = app_module._fetch_eastmoney_top
    original_quote = app_module._tencent_quote
    original_capital_flow = app_module._capital_flow
    original_market_sentiment = app_module._market_sentiment
    original_short_signal_score = app_module._short_signal_score
    original_req_get = app_module._req.get

    def fake_fetch(market, n=50):
        return [("600000", TEST_NAME)]

    def fake_quote(codes):
        return {
            "sh600000": {
                "name": TEST_NAME,
                "price": 10.0,
                "chg_pct": 4.2,
                "vol_ratio": 2.0,
                "turnover": 6.0,
            }
        }

    def fake_capital_flow(code, market, days=1):
        return [{"date": "2026-06-17", "main_net": 1.2, "main_pct": 3.0}]

    class FakeResponse:
        def json(self):
            return {"data": {"diff": []}}

    def fake_detail(code, now=None):
        plan_now = now or datetime(2026, 6, 17, 10, 0, 0)
        plan_stock = {
            "code": code,
            "name": TEST_NAME,
            "market": A_SHARE,
            "price": 10.0,
            "score": 48,
            "chg_pct": 4.2,
            "vol_ratio": 2.0,
            "turnover": 6.0,
            "capital_net": 1.2,
        }
        trade_plan = app_module._build_trade_plan(
            plan_stock,
            market_sentiment={"score": 56, "label": GREEDY},
            now=plan_now,
        )
        return {
            **plan_stock,
            "rec": BULLISH,
            "rec_color": "#00cc55",
            "trade_plan": trade_plan,
            "decision": trade_plan["decision"],
            "confidence": trade_plan["confidence"],
            "position_pct": trade_plan["position_pct"],
            "market_sentiment": {"score": 56, "label": GREEDY},
        }

    try:
        app_module._fetch_eastmoney_top = fake_fetch
        app_module._tencent_quote = fake_quote
        app_module._capital_flow = fake_capital_flow
        app_module._market_sentiment = lambda: {"score": 56, "label": GREEDY, "details": []}
        app_module._short_signal_score = fake_detail
        app_module._req.get = lambda *args, **kwargs: FakeResponse()

        with app_module.app.test_client() as client:
            response = client.post(
                "/api/rank",
                json={"markets": ["A"], "as_of_time": "14:45:00"},
            )

        assert response.status_code == 200
        payload = json.loads(response.data.decode("utf-8"))
        assert payload["results"], payload
        assert payload["results"][0]["decision"] == BUY
        assert payload["results"][0]["trade_plan"]["decision"] == BUY
    finally:
        app_module._fetch_eastmoney_top = original_fetch
        app_module._tencent_quote = original_quote
        app_module._capital_flow = original_capital_flow
        app_module._market_sentiment = original_market_sentiment
        app_module._short_signal_score = original_short_signal_score
        app_module._req.get = original_req_get


def test_market_sentiment_does_not_emit_nan_text_for_etf_proxy():
    original_ticker = app_module.yf.Ticker
    original_quote = app_module._tencent_quote

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period="2d", **kwargs):
            if self.symbol == "^VIX":
                return pd.DataFrame({"Close": [16.0]})
            if self.symbol == "513500.SS":
                return pd.DataFrame({"Close": [math.nan, math.nan]})
            return pd.DataFrame()

    try:
        app_module.yf.Ticker = FakeTicker
        app_module._tencent_quote = lambda codes: {}

        payload = app_module._market_sentiment()
        raw = app_module.jdump(payload)

        assert "nan" not in raw.lower()
    finally:
        app_module.yf.Ticker = original_ticker
        app_module._tencent_quote = original_quote


if __name__ == "__main__":
    test_rank_api_passes_report_time_to_detail_plan()
    test_market_sentiment_does_not_emit_nan_text_for_etf_proxy()
    print("ALL TESTS PASSED")
