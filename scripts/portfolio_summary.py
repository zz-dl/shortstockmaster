def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _position_amount(position: dict) -> float:
    amount = position.get("amount", position.get("cost"))
    if amount is None:
        amount = position.get("cost", 10000)
    return _num(amount, 0.0)


def build_portfolio_summary(positions: list, sold_positions: list | None = None) -> dict:
    open_positions = positions or []
    closed_positions = sold_positions or []

    open_pnl = sum(_num(p.get("pnl")) for p in open_positions)
    realized_pnl = sum(_num(p.get("pnl")) for p in closed_positions)
    total_inv = (
        sum(_position_amount(p) for p in open_positions)
        + sum(_position_amount(p) for p in closed_positions)
    )
    total_pnl = round(open_pnl + realized_pnl, 2)
    total_pct = total_pnl / total_inv * 100 if total_inv else 0

    return {
        "total_inv": total_inv,
        "total_pnl": total_pnl,
        "total_pct": total_pct,
        "open_pnl": round(open_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
    }
