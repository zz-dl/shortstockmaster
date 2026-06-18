import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_insights import build_big_move_suggestion, format_buy_direction


def test_format_buy_direction_prefers_trade_decision_and_keeps_rank_context():
    position = {
        "decision_at_buy": "买入",
        "rec_at_buy": "偏多观望",
    }

    summary = format_buy_direction(position)

    assert summary == "交易决策：买入；榜单方向：偏多观望"


def test_big_move_suggestion_excludes_same_day_buys_from_blind_spot():
    positions = [
        {
            "name": "御银股份",
            "chg_today": 6.36,
            "entry_date": "2026-06-17",
            "bought_today": True,
        },
        {
            "name": "旧持仓",
            "chg_today": 5.20,
            "entry_date": "2026-06-16",
            "bought_today": False,
        },
    ]

    suggestion = build_big_move_suggestion(positions, today="2026-06-17")

    assert suggestion == "**旧持仓** 今日波动超 5%，系统未提前识别，可考虑加强实时新闻触发检测"


def test_big_move_suggestion_recognizes_same_day_captured_candidates():
    positions = [
        {
            "name": "御银股份",
            "chg_today": 6.36,
            "entry_date": "2026-06-17",
            "bought_today": True,
        },
        {
            "name": "瑞玛精密",
            "chg_today": 5.83,
            "entry_date": "2026-06-17",
            "bought_today": True,
        },
    ]

    suggestion = build_big_move_suggestion(positions, today="2026-06-17")

    assert suggestion == "御银股份、瑞玛精密 今日大波动均已被当日买入信号捕捉，暂无新增漏报"


if __name__ == "__main__":
    test_format_buy_direction_prefers_trade_decision_and_keeps_rank_context()
    test_big_move_suggestion_excludes_same_day_buys_from_blind_spot()
    test_big_move_suggestion_recognizes_same_day_captured_candidates()
    print("ALL TESTS PASSED")
