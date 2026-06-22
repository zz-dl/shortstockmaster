import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module


def test_rank_short_circuits_when_a_share_quote_date_is_stale():
    original_now = app_module._beijing_now
    original_quote = app_module._tencent_quote
    original_fetch = app_module._fetch_eastmoney_top
    try:
        app_module._beijing_now = lambda: datetime(2026, 6, 19, 14, 45)
        app_module._tencent_quote = lambda codes: {
            "sh000001": {
                "price": 4080.0,
                "quote_time": "20260618150000",
            }
        }

        def unexpected_fetch(*args, **kwargs):
            raise AssertionError("market-closed rank request must not scan candidates")

        app_module._fetch_eastmoney_top = unexpected_fetch
        response = app_module.app.test_client().post(
            "/api/rank",
            json={"markets": ["A"], "as_of_time": "14:45:00"},
        )
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["status"] == "market_closed"
        assert payload["market_date"] == "2026-06-18"
        assert payload["market_time"] == "2026-06-18 15:00:00"
        assert payload["results"] == []
        assert payload["total"] == 0
    finally:
        app_module._beijing_now = original_now
        app_module._tencent_quote = original_quote
        app_module._fetch_eastmoney_top = original_fetch


if __name__ == "__main__":
    test_rank_short_circuits_when_a_share_quote_date_is_stale()
    print("ALL TESTS PASSED")
