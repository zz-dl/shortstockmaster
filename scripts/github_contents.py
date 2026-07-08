import base64
import json


def decode_github_content(encoded_content: str, parse_json: bool = True):
    text = base64.b64decode(encoded_content).decode("utf-8")
    return json.loads(text) if parse_json else text
