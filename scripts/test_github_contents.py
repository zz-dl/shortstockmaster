import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_contents import decode_github_content


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_decode_github_json_content():
    payload = {"date": "2026-07-08", "ok": True}

    assert decode_github_content(_encoded(json.dumps(payload))) == payload


def test_decode_github_markdown_content_without_json_parse():
    markdown = "# StockMaster 每日日报 2026-07-08\n\n正文"

    assert decode_github_content(_encoded(markdown), parse_json=False) == markdown


if __name__ == "__main__":
    test_decode_github_json_content()
    test_decode_github_markdown_content_without_json_parse()
    print("ALL TESTS PASSED")
