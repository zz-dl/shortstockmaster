import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import _apply_plan_to_rank_item, _build_trade_plan, _is_rank_candidate, _rank_score_quick


def _quote(chg_pct, vol_ratio, turnover=6):
    return {
        "sh600000": {
            "name": "TEST",
            "code": "600000",
            "price": 10,
            "prev_close": 9.5,
            "open": 9.7,
            "volume": 100000,
            "amount": 1000000,
            "chg_pct": chg_pct,
            "turnover": turnover,
            "vol_ratio": vol_ratio,
        }
    }


def test_rank_candidate_requires_backtested_gain_and_volume_ranges():
    assert _is_rank_candidate({"market": "A股", "chg_pct": 5.5, "vol_ratio": 2.5})
    assert not _is_rank_candidate({"market": "A股", "chg_pct": 8.0, "vol_ratio": 2.5})
    assert not _is_rank_candidate({"market": "A股", "chg_pct": 5.5, "vol_ratio": 6.2})


def test_extreme_volume_and_overheat_gain_are_downgraded():
    sweet = _rank_score_quick("600000", "A股SH", _quote(5.5, 2.5))
    overheated = _rank_score_quick("600000", "A股SH", _quote(8.0, 2.5))
    extreme_volume = _rank_score_quick("600000", "A股SH", _quote(5.5, 6.2))

    assert sweet["score"] > overheated["score"]
    assert sweet["score"] > extreme_volume["score"]
    assert not _is_rank_candidate(overheated)
    assert not _is_rank_candidate(extreme_volume)


def test_trade_plan_prefers_tail_confirmation_for_quality_candidate():
    stock = {
        "code": "600000",
        "name": "TEST",
        "market": "A股",
        "price": 10.0,
        "score": 48,
        "chg_pct": 4.2,
        "vol_ratio": 1.9,
        "turnover": 6.5,
        "capital_net": 1.2,
    }

    plan = _build_trade_plan(stock, market_sentiment={"score": 56, "label": "贪婪"})

    assert plan["decision"] == "尾盘确认"
    assert plan["confidence"] in ("中", "高")
    assert plan["position_pct"] >= 10
    assert "14:30" in plan["buy_plan"]
    assert plan["stop_loss_price"] < stock["price"]
    assert plan["take_profit_price"] > stock["price"]
    assert plan["max_holding_days"] == 3


def test_trade_plan_rejects_overheated_or_capital_outflow_candidate():
    overheated = {
        "code": "600000",
        "name": "TEST",
        "market": "A股",
        "price": 10.0,
        "score": 55,
        "chg_pct": 8.2,
        "vol_ratio": 2.0,
        "turnover": 9.0,
        "capital_net": 1.5,
    }
    outflow = dict(overheated, chg_pct=4.0, capital_net=-1.5, score=45)

    assert _build_trade_plan(overheated)["decision"] == "回避"
    assert _build_trade_plan(outflow)["decision"] in ("观察", "回避")


def test_apply_plan_to_rank_item_reprices_recommendation_and_candidate_gate():
    stock = {
        "code": "600000",
        "name": "TEST",
        "market": "A股",
        "price": 10.0,
        "score": 42,
        "rec": "短线做多",
        "chg_pct": 4.0,
        "vol_ratio": 2.0,
        "turnover": 7.0,
        "capital_net": 0.8,
    }

    enriched = _apply_plan_to_rank_item(stock, {"score": 52, "label": "中性"})

    assert enriched["decision"] == "尾盘确认"
    assert enriched["trade_plan"]["position_pct"] >= 10
    assert enriched["rec"] in ("尾盘确认", "短线做多")
    assert _is_rank_candidate(enriched)
