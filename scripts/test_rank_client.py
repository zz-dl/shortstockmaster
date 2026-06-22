import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rank_client import fetch_rank


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"status": "done", "total": 60, "results": [{"code": "000001"}]}


class FlakySession:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if len(self.calls) == 1:
            raise requests.Timeout("first call timed out")
        return FakeResponse()


def test_rank_client_retries_with_configured_timeout():
    session = FlakySession()
    payload = fetch_rank(
        session,
        "https://example.test/api/rank",
        {"markets": ["A"]},
        timeout_seconds=180,
        attempts=2,
    )

    assert payload["status"] == "done"
    assert len(session.calls) == 2
    assert all(call["timeout"] == 180 for call in session.calls)


if __name__ == "__main__":
    test_rank_client_retries_with_configured_timeout()
    print("ALL TESTS PASSED")
