import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import _analyze_main_fund_flow


def test_strong_capital_price_confirmation():
    analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": 0.42, "main_pct": 2.1, "large_net": 0.18, "super_net": 0.14, "small_net": -0.22},
            {"date": "2026-06-16", "main_net": 0.78, "main_pct": 4.3, "large_net": 0.35, "super_net": 0.31, "small_net": -0.40},
            {"date": "2026-06-17", "main_net": 1.58, "main_pct": 8.2, "large_net": 0.71, "super_net": 0.66, "small_net": -0.88},
        ],
        {"chg_pct": 4.2, "vol_ratio": 2.1, "turnover": 7.5},
    )

    assert analysis["label"] == "强资金共振"
    assert analysis["rating"] == "bullish"
    assert analysis["score_delta"] >= 24
    assert analysis["metrics"]["positive_days_3"] == 3
    assert analysis["metrics"]["net3"] > 2.5
    assert any("价格上涨" in d for d in analysis["drivers"])


def test_accumulation_watch_when_capital_inflows_but_price_is_flat():
    analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": -0.12, "main_pct": -0.7, "large_net": -0.03, "super_net": -0.02, "small_net": 0.05},
            {"date": "2026-06-16", "main_net": 0.36, "main_pct": 2.2, "large_net": 0.20, "super_net": 0.08, "small_net": -0.18},
            {"date": "2026-06-17", "main_net": 0.82, "main_pct": 5.1, "large_net": 0.37, "super_net": 0.25, "small_net": -0.33},
        ],
        {"chg_pct": -0.4, "vol_ratio": 1.3, "turnover": 5.2},
    )

    assert analysis["label"] == "吸筹观察"
    assert analysis["rating"] == "watch"
    assert 8 <= analysis["score_delta"] <= 18
    assert any("价格未明显上涨" in d for d in analysis["drivers"])


def test_distribution_risk_when_inflow_is_overheated():
    analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": 0.50, "main_pct": 2.6, "large_net": 0.20, "super_net": 0.10, "small_net": -0.22},
            {"date": "2026-06-16", "main_net": 1.20, "main_pct": 5.0, "large_net": 0.40, "super_net": 0.42, "small_net": -0.55},
            {"date": "2026-06-17", "main_net": 2.70, "main_pct": 9.1, "large_net": 0.90, "super_net": 1.05, "small_net": -1.10},
        ],
        {"chg_pct": 8.4, "vol_ratio": 6.3, "turnover": 23.0},
    )

    assert analysis["label"] == "诱多风险"
    assert analysis["rating"] == "bearish"
    assert analysis["score_delta"] < 0
    assert any("过热" in r or "换手" in r for r in analysis["risks"])


def test_main_outflow_is_bearish():
    analysis = _analyze_main_fund_flow(
        [
            {"date": "2026-06-15", "main_net": -0.30, "main_pct": -1.8, "large_net": -0.10, "super_net": -0.06, "small_net": 0.18},
            {"date": "2026-06-16", "main_net": -0.76, "main_pct": -4.1, "large_net": -0.35, "super_net": -0.21, "small_net": 0.42},
            {"date": "2026-06-17", "main_net": -1.18, "main_pct": -6.4, "large_net": -0.62, "super_net": -0.40, "small_net": 0.77},
        ],
        {"chg_pct": -2.8, "vol_ratio": 1.8, "turnover": 8.0},
    )

    assert analysis["label"] == "主力流出"
    assert analysis["rating"] == "bearish"
    assert analysis["score_delta"] <= -20
    assert analysis["metrics"]["positive_days_3"] == 0


def test_no_capital_flow_data_is_neutral():
    analysis = _analyze_main_fund_flow([], {"chg_pct": 3.0, "vol_ratio": 2.0, "turnover": 5.0})

    assert analysis["label"] == "暂无主力资金数据"
    assert analysis["rating"] == "neutral"
    assert analysis["score_delta"] == 0
    assert analysis["metrics"]["rows"] == 0


if __name__ == "__main__":
    test_strong_capital_price_confirmation()
    test_accumulation_watch_when_capital_inflows_but_price_is_flat()
    test_distribution_risk_when_inflow_is_overheated()
    test_main_outflow_is_bearish()
    test_no_capital_flow_data_is_neutral()
    print("ALL TESTS PASSED")
