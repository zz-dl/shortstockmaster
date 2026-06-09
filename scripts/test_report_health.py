import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_health import summarize_rank_health


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


healthy = summarize_rank_health(True, "done", 60, "")
check("healthy rank has no warning", healthy["warning"] == "")
check("healthy rank has no suggestion", healthy["suggestion"] == "")

empty = summarize_rank_health(False, "done", 60, "")
check("empty rank warning is explicit", "排行榜未返回有效候选" in empty["warning"])
check("empty rank warning protects trade logic", "暂停排行榜驱动买入/跌出Top10卖出" in empty["warning"])
check("empty rank suggestion is actionable", "检查 API 可用性或候选过滤阈值" in empty["suggestion"])

failed = summarize_rank_health(False, "", None, "timeout")
check("rank error includes cause", "timeout" in failed["warning"])

print("ALL TESTS PASSED")
