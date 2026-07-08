def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _same_day_buy(position: dict, today: str) -> bool:
    return bool(position.get("bought_today")) or str(position.get("entry_date") or "")[:10] == today


def format_buy_direction(position: dict) -> str:
    decision = str(
        position.get("decision_at_buy")
        or (position.get("trade_plan_at_buy") or {}).get("decision")
        or ""
    )
    rec = str(position.get("rec_at_buy") or position.get("rec") or "")
    if decision and rec:
        return f"交易决策：{decision}；榜单方向：{rec}"
    if decision:
        return f"交易决策：{decision}"
    if rec:
        return f"榜单方向：{rec}"
    return "交易方向：未知"


def build_big_move_suggestion(positions: list[dict], today: str, threshold: float = 5.0) -> str | None:
    if not positions:
        return None

    big_moves = [p for p in positions if abs(_num(p.get("chg_today"))) > threshold]
    if not big_moves:
        return None

    uncaptured = [p for p in big_moves if not _same_day_buy(p, today)]
    if uncaptured:
        names = "、".join(str(p.get("name") or p.get("code") or "") for p in uncaptured)
        return f"**{names}** 今日波动超 5%，系统未提前识别，可考虑加强实时新闻触发检测"

    names = "、".join(str(p.get("name") or p.get("code") or "") for p in big_moves)
    return f"{names} 今日大波动均已被当日买入信号捕捉，暂无新增漏报"


def build_trade_timing_suggestion(
    actual_time_label: str,
    report_time_label: str,
    buy_execution_enabled: bool,
    late_exit_enabled: bool,
    rank_status: str = "",
) -> str | None:
    if buy_execution_enabled or rank_status == "market_closed":
        return None
    prefix = f"GitHub Actions 实际在 {actual_time_label} 运行，偏离目标 {report_time_label}；"
    if late_exit_enabled:
        return prefix + "本次仅执行风险退出，不执行新增买入"
    return prefix + "本次已阻止使用非尾盘行情执行模拟交易"
