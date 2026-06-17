import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    _apply_plan_to_rank_item,
    _analyze_main_fund_flow,
    _build_trade_plan,
    _is_rank_candidate,
    _merge_rank_item_with_detail,
    _rank_score_quick,
)


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


def test_trade_plan_uses_structured_fund_flow_analysis():
    strong_analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": 0.55, "main_pct": 2.8, "large_net": 0.20, "super_net": 0.18, "small_net": -0.24},
            {"date": "2026-06-16", "main_net": 0.86, "main_pct": 4.6, "large_net": 0.36, "super_net": 0.32, "small_net": -0.45},
            {"date": "2026-06-17", "main_net": 1.62, "main_pct": 8.6, "large_net": 0.72, "super_net": 0.68, "small_net": -0.90},
        ],
        {"chg_pct": 4.1, "vol_ratio": 2.2, "turnover": 7.5},
    )
    risk_analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": 0.40, "main_pct": 1.9, "large_net": 0.15, "super_net": 0.12, "small_net": -0.18},
            {"date": "2026-06-16", "main_net": 1.10, "main_pct": 4.5, "large_net": 0.30, "super_net": 0.38, "small_net": -0.44},
            {"date": "2026-06-17", "main_net": 2.80, "main_pct": 9.5, "large_net": 0.95, "super_net": 1.10, "small_net": -1.20},
        ],
        {"chg_pct": 8.3, "vol_ratio": 6.4, "turnover": 24.0},
    )

    strong_plan = _build_trade_plan({
        "code": "600000", "name": "TEST", "market": "A股", "price": 10.0,
        "score": 42, "chg_pct": 4.1, "vol_ratio": 2.2, "turnover": 7.5,
        "capital_net": 1.62, "fund_flow_analysis": strong_analysis,
    }, market_sentiment={"score": 56, "label": "贪婪"})
    risk_plan = _build_trade_plan({
        "code": "600000", "name": "TEST", "market": "A股", "price": 10.0,
        "score": 42, "chg_pct": 8.3, "vol_ratio": 6.4, "turnover": 24.0,
        "capital_net": 2.80, "fund_flow_analysis": risk_analysis,
    }, market_sentiment={"score": 56, "label": "贪婪"})

    assert strong_plan["decision"] == "尾盘确认"
    assert "强资金共振" in strong_plan["drivers"]
    assert risk_plan["decision"] == "回避"
    assert any("诱多风险" in x for x in risk_plan["invalidations"])


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


def test_rank_item_uses_detail_score_and_plan_as_canonical_display():
    tail_confirm = chr(23614) + chr(30424) + chr(30830) + chr(35748)
    avoid = chr(22238) + chr(36991)
    high = chr(39640)
    low = chr(20302)
    a_share = chr(65) + chr(32929)
    rank_item = {
        "code": "002617",
        "name": "TEST",
        "market": a_share,
        "price": 8.5,
        "score": 49,
        "rec": "rank-rec",
        "decision": tail_confirm,
        "confidence": high,
        "position_pct": 20,
        "trade_plan": {"decision": tail_confirm},
        "capital_net": 3.35,
    }
    detail = {
        "code": "002617",
        "name": "TEST",
        "market": a_share,
        "price": 8.5,
        "score": 20,
        "rec": "detail-rec",
        "decision": avoid,
        "confidence": low,
        "position_pct": 0,
        "trade_plan": {
            "decision": avoid,
            "confidence": low,
            "position_pct": 0,
        },
        "capital_net": 3.35,
        "fund_flow_analysis": {"label": "强资金共振", "rating": "bullish", "score_delta": 24},
    }

    merged = _merge_rank_item_with_detail(rank_item, detail)

    assert merged["quick_score"] == 49
    assert merged["score"] == 20
    assert merged["rec"] == "detail-rec"
    assert merged["decision"] == avoid
    assert merged["confidence"] == low
    assert merged["position_pct"] == 0
    assert merged["trade_plan"]["decision"] == avoid
    assert merged["capital_net"] == 3.35
    assert merged["fund_flow_analysis"]["label"] == "强资金共振"


if __name__ == "__main__":
    test_rank_candidate_requires_backtested_gain_and_volume_ranges()
    test_extreme_volume_and_overheat_gain_are_downgraded()
    test_trade_plan_prefers_tail_confirmation_for_quality_candidate()
    test_trade_plan_rejects_overheated_or_capital_outflow_candidate()
    test_trade_plan_uses_structured_fund_flow_analysis()
    test_apply_plan_to_rank_item_reprices_recommendation_and_candidate_gate()
    test_rank_item_uses_detail_score_and_plan_as_canonical_display()
    print("ALL TESTS PASSED")
