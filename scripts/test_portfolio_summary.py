import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_summary import build_portfolio_summary


def test_daily_summary_includes_realized_pnl_from_closed_positions():
    summary = build_portfolio_summary(
        positions=[],
        sold_positions=[
            {
                "code": "000593",
                "amount": 10000,
                "pnl": -932.46,
            }
        ],
    )

    assert summary["total_inv"] == 10000
    assert summary["total_pnl"] == -932.46
    assert round(summary["total_pct"], 2) == -9.32
    assert summary["realized_pnl"] == -932.46
    assert summary["open_pnl"] == 0


if __name__ == "__main__":
    test_daily_summary_includes_realized_pnl_from_closed_positions()
    print("ALL TESTS PASSED")
