import copy


def build_trade_history_records(today: str, positions: list, previous_positions: list,
                                top10: list, is_day1: bool,
                                sold_positions: list | None = None) -> list:
    """Normalize ShortStockMaster daily state into analysis-ready records."""
    records = []
    seq = 1
    previous_codes = {p.get("code") for p in previous_positions or []}
    top_rank = {s.get("code"): i + 1 for i, s in enumerate(top10 or [])}

    for p in sold_positions or []:
        code = p.get("code", "")
        entry_price = p.get("entry_price")
        sell_price = p.get("sell_price", p.get("cur_price", entry_price))
        records.append({
            "source_app": "short_stockmaster",
            "strategy": "daily_report_top10",
            "trade_date": today,
            "sequence": seq,
            "action": "sell",
            "code": code,
            "name": p.get("name", code),
            "market": p.get("market", ""),
            "industry": p.get("industry", ""),
            "buy_date": p.get("entry_date", ""),
            "buy_time": p.get("buy_time", "09:00:00"),
            "buy_price": entry_price,
            "sell_date": today,
            "sell_time": p.get("sell_time", "09:00:00"),
            "sell_price": sell_price,
            "current_price": sell_price,
            "return_pct": p.get("pnl_pct"),
            "shares": p.get("shares"),
            "cost": p.get("amount", p.get("cost")),
            "rank_at_buy": p.get("rank_at_buy", top_rank.get(code)),
            "score_at_buy": p.get("score_at_buy", p.get("score")),
            "rec_at_buy": p.get("rec_at_buy", p.get("rec", "")),
            "sell_reason": p.get("sell_reason", ""),
            "holding_days": None,
            "pnl": p.get("pnl"),
            "chg_today": p.get("chg_today"),
            "raw": copy.deepcopy(p),
        })
        seq += 1

    for p in positions or []:
        code = p.get("code", "")
        is_new = p.get("bought_today") or is_day1 or code not in previous_codes
        action = "buy" if is_new else "snapshot"
        entry_price = p.get("entry_price")
        current_price = p.get("cur_price", entry_price)
        records.append({
            "source_app": "short_stockmaster",
            "strategy": "daily_report_top10",
            "trade_date": today,
            "sequence": seq,
            "action": action,
            "code": code,
            "name": p.get("name", code),
            "market": p.get("market", ""),
            "industry": p.get("industry", ""),
            "buy_date": p.get("entry_date", today if is_new else ""),
            "buy_time": p.get("buy_time", "09:00:00") if is_new else "",
            "buy_price": entry_price,
            "sell_date": "",
            "sell_time": "",
            "sell_price": None,
            "current_price": current_price,
            "return_pct": p.get("pnl_pct"),
            "shares": p.get("shares"),
            "cost": p.get("amount", p.get("cost")),
            "rank_at_buy": p.get("rank_at_buy", top_rank.get(code)),
            "score_at_buy": p.get("score_at_buy", p.get("score")),
            "rec_at_buy": p.get("rec_at_buy", p.get("rec", "")),
            "sell_reason": "",
            "holding_days": None,
            "pnl": p.get("pnl"),
            "chg_today": p.get("chg_today"),
            "raw": copy.deepcopy(p),
        })
        seq += 1

    return records
