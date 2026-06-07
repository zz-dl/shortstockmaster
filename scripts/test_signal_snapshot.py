import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signal_snapshot import build_signal_snapshot_records


def test_short_signal_snapshot_keeps_rank_and_quote_context():
    today = "2026-06-07"
    top10 = [{
        "code": "600900",
        "name": "S0",
        "market": "A",
        "score": 82.5,
        "rec": "strong_buy",
        "price": 27.2,
        "chg_pct": 1.4,
        "vol_ratio": 1.8,
        "turnover": 2.3,
    }]

    records = build_signal_snapshot_records(today, top10)

    assert len(records) == 1
    rec = records[0]
    assert rec["source_app"] == "short_stockmaster"
    assert rec["strategy"] == "daily_report_top10"
    assert rec["snapshot_date"] == today
    assert rec["snapshot_time"] == "09:00:00"
    assert rec["code"] == "600900"
    assert rec["rank"] == 1
    assert rec["score"] == 82.5
    assert rec["recommendation"] == "strong_buy"
    assert rec["price"] == 27.2
    assert rec["forward_returns"]["return_5d_pct"] is None


if __name__ == "__main__":
    test_short_signal_snapshot_keeps_rank_and_quote_context()
    print("[PASS] test_short_signal_snapshot_keeps_rank_and_quote_context")
