"""Output schema, tolerant JSON parsing, and validation.

Output contract (one object per estimate image):
  date       ISO "YYYY-MM-DD" or null
  items[]    {description, qty|null, amount|null, kind: "part"|"labour"|null}
  total      grand total written on the page, or null
  tax_added  tax rows the page ADDS ON TOP of the line amounts (e.g. CGST+SGST
             under the table), or null. Extension to the brief: it lets the
             reconciler tell "items + tax = total" from a real mismatch.
"""
from __future__ import annotations

import json
import re
from datetime import date

from jsonschema import Draft202012Validator

ESTIMATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "items", "total"],
    "properties": {
        "date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "qty", "amount", "kind"],
                "properties": {
                    "description": {"type": "string", "minLength": 1},
                    "qty": {"type": ["number", "null"], "exclusiveMinimum": 0},
                    "amount": {"type": ["number", "null"], "minimum": 0},
                    "kind": {"enum": ["part", "labour", None]},
                },
            },
        },
        "total": {"type": ["number", "null"], "minimum": 0},
        "tax_added": {"type": ["number", "null"], "minimum": 0},
    },
}

_VALIDATOR = Draft202012Validator(ESTIMATE_SCHEMA)


class ParseError(ValueError):
    """The model response could not be turned into a JSON object."""


def parse_json_text(text: str | None) -> dict:
    """Parse a model response into a dict. Tolerates ```json fences and stray prose."""
    if text is None or not text.strip():
        raise ParseError("empty response")
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S | re.I)
    if fence:
        s = fence.group(1)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a == -1 or b <= a:
            raise ParseError("no JSON object found in response") from None
        try:
            obj = json.loads(s[a : b + 1])
        except json.JSONDecodeError as e:
            raise ParseError(f"invalid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ParseError("top-level JSON value must be an object")
    return obj


def validate_estimate(obj) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errors: list[str] = []
    for e in sorted(_VALIDATOR.iter_errors(obj), key=lambda e: str(list(e.absolute_path))):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        errors.append(f"{path}: {e.message[:200]}")
    if not errors and obj.get("date") is not None:
        try:
            date.fromisoformat(obj["date"])
        except ValueError:
            errors.append(f"$.date: {obj['date']!r} is not a real calendar date")
    return errors


def normalize(obj: dict) -> dict:
    """Canonical form of an already-valid object (fixed key order, 2dp numbers, tax_added present)."""

    def num(x):
        return None if x is None else round(float(x), 2)

    return {
        "date": obj.get("date"),
        "items": [
            {
                "description": " ".join(i["description"].split()),
                "qty": num(i["qty"]),
                "amount": num(i["amount"]),
                "kind": i["kind"],
            }
            for i in obj["items"]
        ],
        "total": num(obj.get("total")),
        "tax_added": num(obj.get("tax_added")),
    }
