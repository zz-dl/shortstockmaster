from datetime import datetime, timedelta


def beijing_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def _parse_target(now: datetime, target_time: str) -> datetime:
    parsed = datetime.strptime(target_time, "%H:%M:%S")
    return datetime.combine(now.date(), parsed.time())


def _in_tail_window(value: datetime) -> bool:
    minutes = value.hour * 60 + value.minute
    return 14 * 60 + 30 <= minutes <= 14 * 60 + 55


def _after_tail_window(value: datetime) -> bool:
    minutes = value.hour * 60 + value.minute
    return minutes > 14 * 60 + 55


def _market_date_matches_today(market_date: str, today: str) -> bool:
    return str(market_date or "")[:10] == today


def plan_trade_execution(
    now: datetime,
    rank_status: str = "",
    rank_available: bool = False,
    market_date: str = "",
) -> dict:
    market_open = rank_status != "market_closed"
    buy_execution_enabled = market_open and _in_tail_window(now)
    late_exit_enabled = (
        market_open
        and not buy_execution_enabled
        and rank_available
        and _after_tail_window(now)
        and _market_date_matches_today(market_date, now.date().isoformat())
    )
    return {
        "buy_execution_enabled": buy_execution_enabled,
        "sell_execution_enabled": buy_execution_enabled or late_exit_enabled,
        "late_exit_enabled": late_exit_enabled,
    }


def report_snapshot_is_final(snapshot: dict | None) -> bool:
    if not snapshot:
        return False
    return (
        snapshot.get("trade_execution_enabled") is True
        or snapshot.get("sell_execution_enabled") is True
        or snapshot.get("rank_status") == "market_closed"
    )


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
