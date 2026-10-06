import copy

from extractor.reconcile import check_internal, explain_amount, reconcile


def by(rec, check, status=None):
    return [f for f in rec["findings"] if f["check"] == check and (status is None or f["status"] == status)]


# ---------------- unit checks
def test_explain_amount_rules():
    assert explain_amount(2200, 1100, 0, 50)[0] == "explained"
    assert explain_amount(3925, 4631.5, 18, 0)[0] == "explained"
    assert explain_amount(100, 100, 18, 50)[0] == "ok"
    st, note = explain_amount(8800, 4140, 0, 50)
    assert st == "mismatch" and "8280.00" in note and "transposition" not in note
    st, note = explain_amount(5945, 6484.1, 18, 0)
    assert st == "mismatch" and "transposition" in note and "5495.00" in note


def test_internal_sum_checks():
    base = {"date": None, "tax_added": None, "total": 30.0,
            "items": [{"description": "a", "qty": None, "amount": 10.0, "kind": None},
                      {"description": "b", "qty": None, "amount": 20.0, "kind": None}]}
    assert check_internal(base)[0]["status"] == "ok"
    bad = {**base, "total": 35.0}
    f = check_internal(bad)[0]
    assert f["status"] == "mismatch" and (f["a"]["value"], f["b"]["value"]) == (30.0, 35.0)
    taxed = {**base, "total": 35.4, "tax_added": 5.4}
    assert check_internal(taxed)[0]["status"] == "explained"
    nulled = copy.deepcopy(base); nulled["items"][1]["amount"] = None
    f = check_internal(nulled)[0]
    assert f["status"] == "not_checkable" and "null" in f["note"]
    assert check_internal({**base, "total": None})[0]["status"] == "not_checkable"


# ---------------- real sample claims
def test_claim_b(ref_b, sys_b):
    rec = reconcile(ref_b, sys_b, "CLAIM_B_data.json")
    assert by(rec, "items_sum_vs_page_total")[0]["status"] == "ok"
    assert by(rec, "total_vs_system")[0]["status"] == "ok"
    assert by(rec, "part_split_vs_system")[0]["status"] == "ok"
    assert by(rec, "labour_split_vs_system")[0]["status"] == "ok"
    # system's own lines (31601.62) do not add to its own total (29764)
    f = by(rec, "system_lines_vs_system_total")[0]
    assert f["status"] == "mismatch" and (f["a"]["value"], f["b"]["value"]) == (31601.62, 29764)
    # three description differences: FR vs RR on the fascia, BRAKIT vs BRACKET, dropped digit in a part number
    d = by(rec, "line_description")
    assert len(d) == 3
    fascia_d = next(f for f in d if "FASCIA" in f["a"]["value"])
    assert "RR FASCIA" in fascia_d["a"]["value"] and "FR FASCIA" in fascia_d["b"]["value"]
    assert any("1703AU300221N" in f["a"]["value"] and "1703AU30221N" in f["b"]["value"] for f in d)
    amounts = {f["a"]["label"].split("(")[1]: f for f in by(rec, "line_amount")}
    fascia = next(f for k, f in amounts.items() if "FASCIA" in k)
    assert fascia["status"] == "mismatch" and (fascia["a"]["value"], fascia["b"]["value"]) == (4030, 5169.92)
    assert "4039.00" in fascia["note"]
    bracket = next(f for k, f in amounts.items() if "BRAKIT" in k)
    assert bracket["status"] == "mismatch" and (bracket["a"]["value"], bracket["b"]["value"]) == (156, 147.2)
    light = next(f for k, f in amounts.items() if "BACK LIGHT" in k)
    assert light["status"] == "explained"
    assert not by(rec, "line_missing_in_system") and not by(rec, "line_missing_in_extraction")


def test_claim_c(ref_c, sys_c):
    rec = reconcile(ref_c, sys_c, "CLAIM_C_data.json")
    assert by(rec, "items_sum_vs_page_total")[0]["status"] == "explained"   # 40650 + 3923.10 = 44573.10
    t = by(rec, "total_vs_system")[0]
    assert t["status"] == "explained" and (t["a"]["value"], t["b"]["value"]) == (44573.10, 40650)
    assert by(rec, "part_split_vs_system")[0]["status"] == "ok"
    assert by(rec, "labour_split_vs_system")[0]["status"] == "ok"
    q = [f for f in by(rec, "line_amount") if "PANEL QUARTER RH" in f["a"]["label"]][0]
    assert q["status"] == "mismatch" and (q["a"]["value"], q["b"]["value"]) == (5945, 6484.1)
    assert "transposition" in q["note"]
    assert len(by(rec, "line_amount", "explained")) == 7                       # other 7 parts: system added 18% GST
    assert len(by(rec, "line_missing_in_system")) == 11                        # all labour lines absent from system parse
    f = by(rec, "system_lines_vs_system_total")[0]
    assert f["status"] == "mismatch" and f["a"]["value"] == 21717.9
    assert by(rec, "page_total_vs_claims_amount")[0]["status"] == "info"


def test_claim_a_system_only_when_extraction_failed(sys_a):
    rec = reconcile(None, sys_a, "CLAIM_A_data.json")
    f = by(rec, "system_lines_vs_system_total")[0]
    assert f["status"] == "mismatch" and (f["a"]["value"], f["b"]["value"]) == (24855, 43100)
    assert any("extraction failed" in n for n in rec["notes"])
    assert not by(rec, "items_sum_vs_page_total")


def test_null_amount_is_reported_not_hidden(ref_c, sys_c):
    d = copy.deepcopy(ref_c)
    d["items"][4]["amount"] = None           # pretend the model returned null for PANEL QUARTER RH
    rec = reconcile(d, sys_c, "CLAIM_C_data.json")
    assert by(rec, "items_sum_vs_page_total")[0]["status"] == "not_checkable"
    q = [f for f in by(rec, "line_amount") if "PANEL QUARTER RH" in f["a"]["label"]][0]
    assert q["status"] == "not_checkable"


def test_date_before_loss_is_flagged(ref_b, sys_b):
    d = {**ref_b, "date": "2025-04-01"}
    assert by(reconcile(d, sys_b), "estimate_date_vs_loss_date")[0]["status"] == "mismatch"
    assert by(reconcile(ref_b, sys_b), "estimate_date_vs_loss_date")[0]["status"] == "info"
