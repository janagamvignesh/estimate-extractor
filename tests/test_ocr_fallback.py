from extractor.ocr_fallback import parse_ocr_text
from extractor.schema import validate_estimate

# Synthetic OCR-style text modelled on a printed estimate (not real OCR output).
TEXT = """
ESTIMATION / QUOTATION
Jobcard Date: 08/05/2025 16:10:10
DRIVE TRAIN - OTHERS
1 ASSY RR FASCIA UPPER-0119BU300810N 8708 10 90 1 4030.00 3148.44 14.00 440.78 14.00 440.78 4030.00
LIGHTS
1 BACK LIGHT L-1703AU300221N 70072190 1 3925.00 3326.27 9.00 299.36 9.00 299.36 3925.00
SUB-TOTAL BEFORE TAX 6596.58
BODY SHOP
1 DENTING CHARGES 998714 1 2850.00 2850.00 9.00 256.50 9.00 256.50 3363.00
PAINT SHOP
1 PAINTING CHARGE 998714 1 15500.00 15500.00 9.00 1395.00 9.00 1395.00 18290.00
TOTAL AMOUNT 29764.00
"""


def test_rule_parser_basics():
    out = parse_ocr_text(TEXT)
    assert validate_estimate(out) == []
    assert out["date"] == "2025-05-08" and out["total"] == 29764.0
    assert [i["amount"] for i in out["items"]] == [4030.0, 3925.0, 3363.0, 18290.0]
    assert [i["kind"] for i in out["items"]] == ["part", "part", "labour", "labour"]
    assert all(i["qty"] is None for i in out["items"])        # never guesses quantities


def test_rule_parser_returns_nulls_not_guesses():
    out = parse_ocr_text("some noise\n43/00\n")
    assert out == {"date": None, "items": [], "total": None, "tax_added": None}
    assert parse_ocr_text("Date 31/02/2025")["date"] is None   # impossible date


def test_noise_and_zero_amount_lines_are_dropped():
    txt = "Parts 0.00\ncost @ 9.00% 57.28\nEstimated Days: Time: 8.2 21795.00\n1 PANEL QUARTER RH 5945.00\n"
    out = parse_ocr_text(txt)
    assert [i["description"] for i in out["items"]] == ["PANEL QUARTER RH"]


def test_date_ignores_sale_date_and_refuses_ambiguity():
    assert parse_ocr_text("Chassis No  Sale Date 28/09/2022")["date"] is None          # only a sale date -> null
    assert parse_ocr_text("Date: 09/05/2025\nSale Date 28/09/2022")["date"] == "2025-05-09"
    assert parse_ocr_text("Date: 09/05/2025\nPrinted 10/05/2025")["date"] is None      # two candidates -> null


def test_tax_rows_misread_by_ocr_are_dropped_but_zero_lookalikes_are_kept():
    txt = "cost @ 9.00% 299.36\n1 BACK LIGHT L-1703AU3@0221N 70072190 1 3925.00 3326.27 9.00 299.36 3925.38\n"
    out = parse_ocr_text(txt)
    assert [i["amount"] for i in out["items"]] == [3925.38]     # value is the (wrong) OCR read, kept as-is; reconcile flags it
