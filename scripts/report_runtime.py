from datetime import datetime, timedelta


def beijing_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def _parse_target(now: datetime, target_time: str) -> datetime:
    parsed = datetime.strptime(target_time, "%H:%M:%S")
    return datetime.combine(now.date(), parsed.time())


def _in_tail_window(value: datetime) -> bool:
    minutes = value.hour * 60 + value.minute
    return 14 * 60 + 30 <= minutes <= 14 * 60 + 55


def plan_report_runtime(
    now: datetime,
    target_time: str = "14:45:00",
    max_wait_minutes: int = 30,
) -> dict:
    target = _parse_target(now, target_time)
    wait_seconds = int((target - now).total_seconds())

    if wait_seconds > max_wait_minutes * 60:
        return {
            "action": "skip",
            "wait_seconds": 0,
            "trade_execution_enabled": False,
        }
    if wait_seconds > 0:
        return {
            "action": "wait",
            "wait_seconds": wait_seconds,
            "trade_execution_enabled": _in_tail_window(target),
        }
    return {
        "action": "run",
        "wait_seconds": 0,
        "trade_execution_enabled": _in_tail_window(now),
    }
