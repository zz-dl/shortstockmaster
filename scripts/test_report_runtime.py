import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_runtime import plan_report_runtime


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


if __name__ == "__main__":
    test_near_target_run_waits_until_tail_time()
    test_far_too_early_run_exits_without_writing_report()
    test_late_run_can_report_but_cannot_trade()
    print("ALL TESTS PASSED")
