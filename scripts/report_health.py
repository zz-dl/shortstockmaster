def normalize_rank_error(rank_available: bool, rank_error="", rank_status="") -> str:
    """Return a durable reason when the rank endpoint yields no effective results."""
    if rank_error:
        return str(rank_error)
    if rank_status == "market_closed":
        return ""
    if not rank_available:
        return "rank returned no effective results during daily report run"
    return ""


def summarize_rank_health(rank_available: bool, rank_status="", rank_total=None, rank_error="") -> dict:
    """Return daily-report text for rank data availability."""
    if rank_available:
        return {"warning": "", "suggestion": ""}
    if rank_status == "market_closed":
        return {
            "warning": "- ℹ️ A股休市，今日不生成排行榜信号，也不执行模拟买卖",
            "suggestion": "",
        }

    details = []
    if rank_status:
        details.append(f"status={rank_status}")
    if rank_total is not None:
        details.append(f"total={rank_total}")
    if rank_error:
        details.append(f"error={rank_error}")

    detail_text = f"（{'，'.join(details)}）" if details else ""
    return {
        "warning": (
            f"- ⚠️ 排行榜未返回有效候选{detail_text}，今日已暂停排行榜驱动买入/跌出Top10卖出，"
            "仅保留止损/止盈保护"
        ),
        "suggestion": (
            f"排行榜未返回有效候选{detail_text}，需检查 API 可用性或候选过滤阈值；"
            "日报已避免将空结果误判为正常无买入"
        ),
    }
