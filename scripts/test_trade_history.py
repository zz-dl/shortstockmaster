import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trade_history import build_trade_history_records


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


today = "2026-06-07"
positions = [{
    "code": "600900",
    "name": "长江电力",
    "market": "A股",
    "score": 32,
    "rec": "短线做多",
    "entry_price": 27.2,
    "entry_date": today,
    "amount": 10000,
    "shares": 367.65,
    "cur_price": 27.8,
    "chg_today": 1.2,
    "pnl_pct": 2.21,
    "pnl": 220.59,
}]

records = build_trade_history_records(today, positions, [], [], True)
check("initial day creates buy record", len(records) == 1, f"={len(records)}")
check("buy fields normalized", records[0]["action"] == "buy" and
      records[0]["source_app"] == "short_stockmaster" and
      records[0]["buy_price"] == 27.2)

records = build_trade_history_records(today, positions, positions, [], False)
check("tracking day creates snapshot", len(records) == 1, f"={len(records)}")
check("snapshot fields normalized", records[0]["action"] == "snapshot" and
      records[0]["current_price"] == 27.8 and records[0]["return_pct"] == 2.21)
check("same-day snapshot includes holding days", records[0]["holding_days"] == 0,
      f"={records[0]['holding_days']}")

stale_flag_position = dict(
    positions[0],
    entry_date="2026-06-18",
    bought_today=True,
)
records = build_trade_history_records(
    "2026-06-23",
    [stale_flag_position],
    [stale_flag_position],
    [],
    False,
)
check("stale bought_today flag does not create repeat buy", records[0]["action"] == "snapshot",
      f"={records[0]['action']}")

older_positions = [dict(positions[0], entry_date="2026-05-19")]
records = build_trade_history_records("2026-06-08", older_positions, older_positions, [], False)
check("multi-day snapshot includes holding days", records[0]["holding_days"] == 20,
      f"={records[0]['holding_days']}")

sold = [dict(older_positions[0], sell_price=27.8, sell_reason="dropped_from_top10")]
records = build_trade_history_records(
    "2026-06-08", [], older_positions, [], False, sold_positions=sold,
)
check("sell records are normalized", len(records) == 1, f"={len(records)}")
check("sell fields normalized", records[0]["action"] == "sell" and
      records[0]["sell_time"] == "09:00:00" and
      records[0]["sell_reason"] == "dropped_from_top10" and
      records[0]["sell_price"] == 27.8)
check("sell records include holding days", records[0]["holding_days"] == 20,
      f"={records[0]['holding_days']}")

print("ALL TESTS PASSED")
