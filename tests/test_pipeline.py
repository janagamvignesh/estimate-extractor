import json
from pathlib import Path

from extractor.logging_utils import CallLogger
from extractor.pipeline import extract_estimate
from extractor.providers import Provider, ProviderResult

IMG = Path("whatever.jpg")  # scripted providers never read the file
GOOD = json.dumps({"date": "2025-05-08", "items": [{"description": "X", "qty": 1, "amount": 10, "kind": "part"}], "total": 10})
BAD_TYPE = json.dumps({"date": None, "items": [{"description": "X", "qty": 1, "amount": "4,030", "kind": "part"}], "total": None})


class Scripted(Provider):
    name = "fake"

    def __init__(self, script, retry=True):
        self.script, self.prompts, self.retry_on_invalid = list(script), [], retry

    def extract(self, image_path, system, prompt):
        self.prompts.append(prompt)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ProviderResult(text=item, input_tokens=100, output_tokens=50, model="fake-1")


def test_first_try_ok():
    r = extract_estimate(IMG, Scripted([GOOD]), sleep=lambda s: None)
    assert (r["status"], r["attempts"], r["retries"]) == ("ok", 1, 0)
    assert r["data"]["total"] == 10.0 and r["data"]["tax_added"] is None
    assert (r["input_tokens"], r["output_tokens"]) == (100, 50)


def test_retry_uses_clearer_prompt_that_quotes_the_error():
    p = Scripted([BAD_TYPE, GOOD])
    r = extract_estimate(IMG, p, sleep=lambda s: None)
    assert (r["status"], r["attempts"], r["retries"]) == ("ok", 2, 1)
    assert "REJECTED" in p.prompts[1] and "$.items[0].amount" in p.prompts[1]
    assert "REJECTED" not in p.prompts[0]
    assert r["input_tokens"] == 200  # summed over both calls


def test_fails_after_two_retries_without_crashing():
    p = Scripted([BAD_TYPE, "not json at all", BAD_TYPE])
    r = extract_estimate(IMG, p, sleep=lambda s: None)
    assert r["status"] == "failed" and r["data"] is None
    assert (r["attempts"], r["retries"]) == (3, 2)
    assert [e["kind"] for e in r["errors"]] == ["schema_invalid", "invalid_json", "schema_invalid"]
    assert "FINAL ATTEMPT" in p.prompts[2] and "FINAL ATTEMPT" not in p.prompts[1]
    assert r["last_raw"]


def test_provider_exception_is_recorded_and_retried():
    sleeps = []
    p = Scripted([RuntimeError("503 overloaded"), GOOD])
    r = extract_estimate(IMG, p, sleep=sleeps.append)
    assert r["status"] == "ok" and r["retries"] == 1
    assert r["errors"][0]["kind"] == "provider_error" and "503" in r["errors"][0]["messages"][0]
    assert sleeps == [1]


def test_all_provider_errors_mark_failed():
    r = extract_estimate(IMG, Scripted([ValueError("a")] * 3), sleep=lambda s: None)
    assert r["status"] == "failed" and r["attempts"] == 3 and r["input_tokens"] is None


def test_deterministic_provider_is_not_retried():
    r = extract_estimate(IMG, Scripted([BAD_TYPE], retry=False), sleep=lambda s: None)
    assert r["status"] == "failed" and r["attempts"] == 1


def test_per_call_log_lines(tmp_path):
    log = tmp_path / "calls.jsonl"
    extract_estimate(IMG, Scripted([BAD_TYPE, GOOD]), call_logger=CallLogger(log), sleep=lambda s: None)
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert [l["outcome"] for l in lines] == ["schema_invalid", "valid"]
    assert [l["is_retry"] for l in lines] == [False, True]
    assert all({"elapsed_s", "input_tokens", "output_tokens", "attempt", "model"} <= l.keys() for l in lines)
    assert lines[0]["input_tokens"] == 100 and lines[0]["errors"]
