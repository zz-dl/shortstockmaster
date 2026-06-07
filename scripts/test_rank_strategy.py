import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import _is_rank_candidate, _rank_score_quick


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
