import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trade_rules import sell_reason


def test_trade_plan_take_profit_triggers_sell():
    position = {
        "entry_date": "2026-06-18",
        "pnl_pct": 7.23,
        "trade_plan_at_buy": {
            "take_profit_pct": 4.8,
            "stop_loss_pct": 2.6,
            "max_holding_days": 3,
        },
    }

    assert sell_reason(position, signal=None, rank_available=True, today="2026-06-22") == "planned_take_profit"


def test_trade_plan_stop_loss_triggers_sell():
    position = {
        "entry_date": "2026-06-18",
        "pnl_pct": -3.4,
        "trade_plan_at_buy": {
            "take_profit_pct": 6.5,
            "stop_loss_pct": 3.2,
            "max_holding_days": 3,
        },
    }

    assert sell_reason(position, signal={}, rank_available=True, today="2026-06-22") == "planned_stop_loss"


def test_trade_plan_max_holding_days_triggers_sell():
    position = {
        "entry_date": "2026-06-18",
        "pnl_pct": 1.2,
        "trade_plan_at_buy": {"max_holding_days": 3},
    }

    assert sell_reason(position, signal={}, rank_available=True, today="2026-06-24") == "max_holding_days"


def test_trade_plan_max_holding_uses_market_days_not_calendar_days():
    position = {
        "entry_date": "2026-06-18",
        "pnl_pct": 1.2,
        "trade_plan_at_buy": {"max_holding_days": 3},
    }

    assert sell_reason(position, signal={}, rank_available=True, today="2026-06-22") == ""


if __name__ == "__main__":
    test_trade_plan_take_profit_triggers_sell()
    test_trade_plan_stop_loss_triggers_sell()
    test_trade_plan_max_holding_days_triggers_sell()
    test_trade_plan_max_holding_uses_market_days_not_calendar_days()
    print("ALL TESTS PASSED")
