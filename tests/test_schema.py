import copy

import pytest

from extractor.schema import ParseError, normalize, parse_json_text, validate_estimate

GOOD = {
    "date": "2025-05-08",
    "items": [{"description": "DENTING CHARGES", "qty": 1, "amount": 3363, "kind": "labour"}],
    "total": 3363,
}


def test_valid_minimal_and_null_everything():
    assert validate_estimate(GOOD) == []
    nulls = {"date": None, "items": [{"description": "x", "qty": None, "amount": None, "kind": None}], "total": None}
    assert validate_estimate(nulls) == []
    assert validate_estimate({"date": None, "items": [], "total": None}) == []


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["items"][0].__setitem__("amount", "3,363"), "amount"),
        (lambda d: d["items"][0].__setitem__("kind", "material"), "kind"),
        (lambda d: d["items"][0].__setitem__("amount", -5), "amount"),
        (lambda d: d["items"][0].__setitem__("qty", 0), "qty"),
        (lambda d: d["items"][0].__setitem__("description", ""), "description"),
        (lambda d: d.__setitem__("total", "29764"), "total"),
        (lambda d: d.__setitem__("date", "08/05/2025"), "date"),
        (lambda d: d.__setitem__("extra", 1), "extra"),
        (lambda d: d.pop("total"), "total"),
        (lambda d: d["items"][0].pop("kind"), "kind"),
        (lambda d: d["items"][0].__setitem__("amount", True), "amount"),
    ],
)
def test_invalid_variants_are_rejected(mutate, needle):
    d = copy.deepcopy(GOOD)
    mutate(d)
    errs = validate_estimate(d)
    assert errs and any(needle in e for e in errs), errs


def test_impossible_calendar_date_rejected():
    d = copy.deepcopy(GOOD)
    d["date"] = "2025-02-30"
    assert any("calendar" in e for e in validate_estimate(d))


def test_parse_handles_fences_and_prose():
    raw = '{"date": null, "items": [], "total": null}'
    assert parse_json_text(f"```json\n{raw}\n```")["items"] == []
    assert parse_json_text(f"Here you go:\n{raw}\nHope that helps")["total"] is None


@pytest.mark.parametrize("bad", ["", "   ", "no json here", "[1,2]", '{"a": ', None])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ParseError):
        parse_json_text(bad)


def test_normalize_adds_tax_added_and_rounds():
    n = normalize({**GOOD, "total": 10.126, "items": [{"description": "  A   B ", "qty": 1, "amount": 2.005, "kind": None}]})
    assert n["tax_added"] is None and n["total"] == 10.13 and n["items"][0]["description"] == "A B"
