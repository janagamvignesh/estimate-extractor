"""Exercises the REAL anthropic SDK against a mock HTTP transport (no network, no key needed).
This proves request shape + response/usage parsing, not model accuracy."""
import base64
import io
import json

import anthropic
import pytest

try:  # newer SDKs ship on httpx2, older ones on httpx; use whichever the installed SDK expects
    import httpx2 as httpx
except ImportError:
    import httpx
from PIL import Image

from extractor.pipeline import extract_estimate
from extractor.providers import AnthropicProvider, ProviderError
from tests.conftest import SAMPLE

IMG = SAMPLE / "CLAIM_B_estimate_original_printed.jpg"
PAYLOAD = {"date": "2025-05-08", "items": [{"description": "DENTING CHARGES", "qty": 1, "amount": 3363, "kind": "labour"}], "total": 3363}


def make_client(responder, seen):
    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=responder(len(seen)))

    return anthropic.Anthropic(api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def message(content, i_tok=1234, o_tok=321):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": content,
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": i_tok, "output_tokens": o_tok}}


def test_text_mode_request_shape_and_usage():
    seen = []
    client = make_client(lambda n: message([{"type": "text", "text": "```json\n" + json.dumps(PAYLOAD) + "\n```"}]), seen)
    rec = extract_estimate(IMG, AnthropicProvider(model="test-model", client=client))
    assert rec["status"] == "ok" and rec["data"]["total"] == 3363.0
    assert (rec["input_tokens"], rec["output_tokens"], rec["model"]) == (1234, 321, "test-model")
    body = seen[0]
    assert body["model"] == "test-model" and "never guess" in body["system"] and "tools" not in body
    blocks = body["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/jpeg"
    img = Image.open(io.BytesIO(base64.b64decode(blocks[0]["source"]["data"])))
    assert img.format == "JPEG" and max(img.size) <= 1800
    assert blocks[1]["type"] == "text" and "day-first" in blocks[1]["text"]


def test_structured_mode_uses_forced_tool_and_parses_tool_input():
    seen = []
    client = make_client(lambda n: message([{"type": "tool_use", "id": "t1", "name": "submit_estimate", "input": PAYLOAD}]), seen)
    rec = extract_estimate(IMG, AnthropicProvider(model="m", structured=True, client=client))
    assert rec["status"] == "ok"
    assert seen[0]["tool_choice"] == {"type": "tool", "name": "submit_estimate"}
    assert seen[0]["tools"][0]["input_schema"]["required"] == ["date", "items", "total"]


def test_bad_then_good_response_retries_through_real_sdk():
    seen = []
    bad = message([{"type": "text", "text": '{"date": null, "items": [{"description": "x", "qty": 1, "amount": "4,030", "kind": "part"}], "total": null}'}])
    good = message([{"type": "text", "text": json.dumps(PAYLOAD)}])
    client = make_client(lambda n: bad if n == 1 else good, seen)
    rec = extract_estimate(IMG, AnthropicProvider(model="m", client=client))
    assert (rec["status"], rec["retries"], rec["input_tokens"]) == ("ok", 1, 2468)
    retry_text = seen[1]["messages"][0]["content"][1]["text"]
    assert "REJECTED" in retry_text and "amount" in retry_text


def test_http_error_becomes_failed_record_not_exception():
    def handler(request):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    client = anthropic.Anthropic(api_key="bad", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    rec = extract_estimate(IMG, AnthropicProvider(model="m", client=client), sleep=lambda s: None)
    assert rec["status"] == "failed" and rec["errors"][0]["kind"] == "provider_error"
    assert "AuthenticationError" in rec["errors"][0]["messages"][0]


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()
