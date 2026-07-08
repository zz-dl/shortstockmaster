import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_runtime import plan_report_runtime
from report_runtime import report_snapshot_is_final
from report_runtime import plan_trade_execution


def test_near_target_run_waits_until_tail_time():
    plan = plan_report_runtime(
        datetime(2026, 6, 22, 14, 26),
        target_time="14:45:00",
        max_wait_minutes=30,
    )
    assert plan["action"] == "wait"
    assert plan["wait_seconds"] == 19 * 60
    assert plan["trade_execution_enabled"] is True


def test_far_too_early_run_exits_without_writing_report():
    plan = plan_report_runtime(
        datetime(2026, 6, 22, 10, 0),
        target_time="14:45:00",
        max_wait_minutes=30,
    )
    assert plan["action"] == "skip"
    assert plan["trade_execution_enabled"] is False


def test_late_run_can_report_but_cannot_trade():
    plan = plan_report_runtime(
        datetime(2026, 6, 22, 19, 0),
        target_time="14:45:00",
        max_wait_minutes=30,
    )
    assert plan["action"] == "run"
    assert plan["trade_execution_enabled"] is False


def test_tail_rank_allows_buy_and_sell_execution():
    plan = plan_trade_execution(
        datetime(2026, 7, 8, 14, 45),
        rank_status="done",
        rank_available=True,
        market_date="2026-07-08",
    )

    assert plan["buy_execution_enabled"] is True
    assert plan["sell_execution_enabled"] is True
    assert plan["late_exit_enabled"] is False


def test_late_same_day_rank_allows_sell_only_execution():
    plan = plan_trade_execution(
        datetime(2026, 7, 8, 17, 12),
        rank_status="done",
        rank_available=True,
        market_date="2026-07-08",
    )

    assert plan["buy_execution_enabled"] is False
    assert plan["sell_execution_enabled"] is True
    assert plan["late_exit_enabled"] is True


def test_late_stale_rank_does_not_allow_sell_execution():
    plan = plan_trade_execution(
        datetime(2026, 7, 8, 17, 12),
        rank_status="done",
        rank_available=True,
        market_date="2026-07-07",
    )

    assert plan["buy_execution_enabled"] is False
    assert plan["sell_execution_enabled"] is False
    assert plan["late_exit_enabled"] is False


def test_late_sell_only_snapshot_counts_as_final_report():
    assert report_snapshot_is_final({
        "rank_status": "done",
        "trade_execution_enabled": False,
        "sell_execution_enabled": True,
    }) is True


def test_late_snapshot_without_any_execution_can_be_retried():
    assert report_snapshot_is_final({
        "rank_status": "done",
        "trade_execution_enabled": False,
        "sell_execution_enabled": False,
    }) is False


if __name__ == "__main__":
    test_near_target_run_waits_until_tail_time()
    test_far_too_early_run_exits_without_writing_report()
    test_late_run_can_report_but_cannot_trade()
    test_tail_rank_allows_buy_and_sell_execution()
    test_late_same_day_rank_allows_sell_only_execution()
    test_late_stale_rank_does_not_allow_sell_execution()
    test_late_sell_only_snapshot_counts_as_final_report()
    test_late_snapshot_without_any_execution_can_be_retried()
    print("ALL TESTS PASSED")
