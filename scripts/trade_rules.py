from datetime import date, timedelta


def _num(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


def _holding_days(today: str | None, entry_date) -> int | None:
    if not today or not entry_date:
        return None
    try:
        start = date.fromisoformat(str(entry_date)[:10])
        end = date.fromisoformat(today)
    except Exception:
        return None
    if end <= start:
        return 0

    days = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def sell_reason(position, signal, rank_available=True, today: str | None = None):
    rec = str((signal or {}).get("rec") or position.get("rec", ""))
    score = _num((signal or {}).get("score", position.get("score", 0)))
    pnl = _num(position.get("pnl_pct"))
    chg = _num(position.get("chg_today"))
    plan = position.get("trade_plan_at_buy") or {}

    if signal is not None and ("\u77ed\u7ebf\u505a\u7a7a" in rec or score <= -30):
        return "bearish_signal"
    if signal is not None and "\u504f\u7a7a" in rec and pnl > 0:
        return "weakening_take_profit"

    planned_stop = _num(plan.get("stop_loss_pct"), None)
    if planned_stop is not None and planned_stop > 0 and pnl <= -planned_stop:
        return "planned_stop_loss"

    planned_take = _num(plan.get("take_profit_pct"), None)
    if planned_take is not None and planned_take > 0 and pnl >= planned_take:
        return "planned_take_profit"

    max_holding = _num(plan.get("max_holding_days"), None)
    days_held = position.get("holding_days")
    if days_held is None:
        days_held = _holding_days(today, position.get("entry_date"))
    if max_holding is not None and days_held is not None and days_held > max_holding:
        return "max_holding_days"

    if pnl <= -6:
        return "stop_loss"
    if pnl >= 8 and chg <= 0:
        return "profit_protection"
    if signal is None and rank_available:
        return "dropped_from_top10"
    return ""
