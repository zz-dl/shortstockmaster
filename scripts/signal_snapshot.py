def build_signal_snapshot_records(today: str, top_results: list, strategy: str = "daily_report_top10") -> list:
    records = []
    for i, s in enumerate(top_results or [], 1):
        records.append({
            "source_app": "short_stockmaster",
            "strategy": strategy,
            "snapshot_date": today,
            "snapshot_time": "09:00:00",
            "sequence": i,
            "code": s.get("code", ""),
            "name": s.get("name", s.get("code", "")),
            "market": s.get("market", ""),
            "industry": s.get("industry", ""),
            "rank": s.get("rank") or i,
            "score": s.get("score"),
            "recommendation": s.get("rec", ""),
            "signal_action": "daily_rank",
            "price": s.get("price") or s.get("current_price"),
            "open": s.get("open"),
            "close": s.get("close"),
            "chg_pct": s.get("chg_pct") or s.get("chg"),
            "vol_ratio": s.get("vol_ratio"),
            "turnover": s.get("turnover") or s.get("turnover_rate"),
            "capital_net": s.get("capital_net") or s.get("main_net"),
            "market_state": s.get("market_state", ""),
            "weights": s.get("weights", {}),
            "factors": s.get("factors", {}),
            "news": s.get("news", []),
            "raw": s,
            "forward_returns": {
                "next_open_pct": None,
                "next_close_pct": None,
                "return_5d_pct": None,
                "return_20d_pct": None,
            },
        })
    return records
