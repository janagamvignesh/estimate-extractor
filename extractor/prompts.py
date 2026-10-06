"""Prompts. The retry prompt gets stricter each attempt and quotes the validation errors."""
from __future__ import annotations

SYSTEM = (
    "You transcribe vehicle-repair estimates from photos/scans into JSON. "
    "You are a careful reader, not an estimator: you copy what is written and you never guess, "
    "calculate, or repair numbers. If something is not legible or not present, use null."
)

RULES = """\
Return ONE JSON object and nothing else (no prose, no markdown fences) with exactly these keys:
{
  "date":      "YYYY-MM-DD" | null,
  "items":     [ {"description": str, "qty": number|null, "amount": number|null, "kind": "part"|"labour"|null} ],
  "total":     number | null,
  "tax_added": number | null
}

Rules:
- date: the date of the estimate / job card itself. Dates are day-first (DD/MM/YYYY) in these documents.
  Ignore browser print stamps, scanner stamps, vehicle sale dates. If day/month/year is not fully legible or
  two different document dates conflict, use null.
- items: one entry per line item, in page order. description: copy the wording as written (keep misspellings,
  keep part numbers). Do not merge, split, translate, or "correct" lines.
- qty: only if the page shows a quantity for that line. A running serial number (1,2,3... / S.NO / Srl) is NOT a
  quantity. A column titled "Qty" that just counts 1..N is a serial number. Otherwise null.
- amount: the number in that line's final AMOUNT column, in rupees, as a bare number (no symbols, no commas).
  If the amount column is tax-inclusive, use it as printed. If any digit is ambiguous, or the amount cannot be
  matched to the line with confidence, use null. Do NOT back-calculate from other numbers.
- kind: "part" for physical parts; "labour" for labour, denting, painting, aligning, fitting, repair or service
  charges. null if unclear.
- total: the grand total / net payable written on the page, exactly as written (not your own sum). null if absent
  or unreadable.
- tax_added: total of tax rows (e.g. CGST + SGST) that the page adds ON TOP of the line amounts to reach the
  total. null if there are none, or if the line amounts already include tax.
- Black boxes are redactions. Ignore them; never infer what is under them.
- If the page does not add up, still report what is written. Do not fix it.
"""


def first_prompt() -> str:
    return "Transcribe the attached estimate image.\n\n" + RULES


def retry_prompt(attempt: int, previous_errors: list[str], previous_raw: str | None) -> str:
    """attempt is 1 for the first retry, 2 for the second."""
    problems = "\n".join(f"- {e}" for e in previous_errors[:12]) or "- (response was not usable)"
    head = (
        "Your previous answer was REJECTED by an automatic validator.\n"
        f"Problems found:\n{problems}\n\n"
        "Read the image again and answer again. Output ONLY the JSON object. Every amount must be a bare "
        "number or null (not a string like \"4,030\"). Use null instead of guessing.\n\n"
    )
    if attempt >= 2:
        head += (
            "FINAL ATTEMPT. Shape to follow exactly (replace the placeholders; keep key names):\n"
            '{"date": null, "items": [{"description": "<text>", "qty": null, "amount": null, "kind": null}], '
            '"total": null, "tax_added": null}\n'
            "No extra keys. No commentary. If you cannot read the page at all, return "
            '{"date": null, "items": [], "total": null, "tax_added": null}.\n\n'
        )
    return head + RULES
