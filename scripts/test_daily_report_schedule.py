import re
from datetime import time
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily_report.yml"


def _minutes(value: str) -> int:
    hour, minute, *_ = [int(part) for part in value.split(":")]
    return hour * 60 + minute


def test_daily_report_runs_in_tail_confirmation_window():
    text = WORKFLOW.read_text(encoding="utf-8")

    report_match = re.search(r'REPORT_TIME:\s*"(\d{2}:\d{2}:\d{2})"', text)
    cron_match = re.search(r"cron:\s*'(\d+)\s+(\d+)\s+\*\s+\*\s+1-5'", text)

    assert report_match, "REPORT_TIME must be set in daily_report workflow"
    assert cron_match, "weekday cron must be set in daily_report workflow"

    report_time = report_match.group(1)
    report_minutes = _minutes(report_time)
    assert _minutes("14:30:00") <= report_minutes <= _minutes("14:55:00")

    cron_minute = int(cron_match.group(1))
    cron_hour = int(cron_match.group(2))
    utc_minutes = (report_minutes - 8 * 60) % (24 * 60)

    assert cron_hour == utc_minutes // 60
    assert cron_minute == utc_minutes % 60


if __name__ == "__main__":
    test_daily_report_runs_in_tail_confirmation_window()
    print("ALL TESTS PASSED")
