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
    cron_matches = re.findall(r"cron:\s*'(\d+)\s+(\d+)\s+\*\s+\*\s+1-5'", text)
    timeout_match = re.search(r"timeout-minutes:\s*(\d+)", text)

    assert report_match, "REPORT_TIME must be set in daily_report workflow"
    assert cron_matches, "weekday cron must be set in daily_report workflow"
    assert timeout_match, "workflow timeout must be explicit"

    report_time = report_match.group(1)
    report_minutes = _minutes(report_time)
    assert _minutes("14:30:00") <= report_minutes <= _minutes("14:55:00")

    cron_times = {(int(hour), int(minute)) for minute, hour in cron_matches}
    assert (2, 0) in cron_times, "keep an early trigger to absorb observed GitHub schedule delay"
    assert (6, 45) in cron_times, "keep the exact 14:45 Beijing fallback trigger"
    assert int(timeout_match.group(1)) >= 45, "allow 30-minute alignment wait plus rank retries"


if __name__ == "__main__":
    test_daily_report_runs_in_tail_confirmation_window()
    print("ALL TESTS PASSED")
