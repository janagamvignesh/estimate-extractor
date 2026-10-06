"""Cross-checks. Every finding carries both numbers and where each came from.

Statuses
  ok             values agree (within rounding)
  explained      values differ, but one is the other with GST and/or depreciation applied
  mismatch       values differ and no simple rule explains it
  not_checkable  a value is null/unreadable, so the check cannot be done (reported, not hidden)
  info           context only (e.g. a claims-system figure that is not a transcription of the page)
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from typing import Optional

TOL_TOTAL = 1.0   # rupees; totals on pages are rounded
TOL_LINE = 0.51   # rupees; line amounts after tax/depreciation maths are rounded to paise or rupee
MATCH_THRESHOLD = 0.6


def _close(a: Optional[float], b: Optional[float], tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _fmt(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _finding(check, status, a, b, note="") -> dict:
    """a / b are (label, value, source) tuples; a is our side, b is the other side."""
    return {
        "check": check,
        "status": status,
        "a": {"label": a[0], "value": a[1], "source": a[2]},
        "b": {"label": b[0], "value": b[1], "source": b[2]},
        "note": note,
    }


# --------------------------------------------------------------------------- internal checks
def check_internal(data: dict) -> list[dict]:
    items, total, tax = data["items"], data["total"], data.get("tax_added")
    amounts = [i["amount"] for i in items]
    nulls = [n for n, a in enumerate(amounts) if a is None]
    s = round(sum(a for a in amounts if a is not None), 2)
    a = ("sum of extracted item amounts", s, "extraction: sum(items[*].amount)")
    b = ("total written on page", total, "extraction: total (read from image)")
    if total is None:
        return [_finding("items_sum_vs_page_total", "not_checkable", a, b, "page total is null (not readable/present)")]
    if nulls:
        return [_finding("items_sum_vs_page_total", "not_checkable", a, b,
                         f"{len(nulls)} item amount(s) are null (unreadable) at items{nulls}; value shown is only a partial sum")]
    if _close(s, total, TOL_TOTAL):
        return [_finding("items_sum_vs_page_total", "ok", a, b)]
    if tax is not None and _close(s + tax, total, TOL_TOTAL):
        return [_finding("items_sum_vs_page_total", "explained", a, b,
                         f"items {_fmt(s)} + tax rows added on the page {_fmt(tax)} = {_fmt(round(s + tax, 2))} = page total")]
    return [_finding("items_sum_vs_page_total", "mismatch", a, b,
                     f"difference {_fmt(round(total - s, 2))}; either a line was misread/missed or the page itself does not add up")]


def _sys_amount(line: dict):
    if line.get("total_final_amt") is not None:
        return line["total_final_amt"], "total_final_amt"
    return line.get("part_total"), "part_total"


def check_system_internal(system: dict, name: str) -> list[dict]:
    pe = system["parsed_estimate"]
    out = []
    amts = [_sys_amount(l)[0] for l in pe.get("lines", [])]
    lines_sum = round(sum(x for x in amts if x is not None), 2)
    stated = pe.get("stated_total_estimate_cost")
    a = ("sum of system line amounts", lines_sum, f"{name}: parsed_estimate.lines[*] (sum)")
    b = ("system's own stated total", stated, f"{name}: parsed_estimate.stated_total_estimate_cost")
    if stated is None:
        out.append(_finding("system_lines_vs_system_total", "not_checkable", a, b, "system has no stated total"))
    elif _close(lines_sum, stated, TOL_TOTAL):
        out.append(_finding("system_lines_vs_system_total", "ok", a, b))
    else:
        out.append(_finding("system_lines_vs_system_total", "mismatch", a, b,
                            "the system record is internally inconsistent: its own lines do not add up to its own total"))
    parts, lab, paint = (pe.get(k) for k in ("total_part_estimation_cost", "labour_estimation_cost", "paint_estimation_cost"))
    if None not in (parts, lab, paint, stated):
        comp = round(parts + lab + paint, 2)
        a2 = ("system parts + labour + paint", comp, f"{name}: parsed_estimate.total_part/labour/paint_estimation_cost")
        st = "ok" if _close(comp, stated, TOL_TOTAL) else "mismatch"
        out.append(_finding("system_components_vs_system_total", st, a2, b))
    return out


# --------------------------------------------------------------------------- comparison with system data
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", s.upper())).strip()


def _digits(x: float) -> str:
    return "".join(sorted(f"{x:.2f}".replace(".", "")))


def explain_amount(ours: float, theirs: float, gst: float, dep: float):
    """Return (status, note) for a line amount comparison."""
    cands = [("same", ours)]
    if gst:
        cands.append((f"page amount + {gst:g}% GST", ours * (1 + gst / 100)))
    if dep:
        cands.append((f"page amount - {dep:g}% depreciation", ours * (1 - dep / 100)))
    if gst and dep:
        cands.append((f"page amount - {dep:g}% depreciation + {gst:g}% GST", ours * (1 - dep / 100) * (1 + gst / 100)))
    for label, v in cands:
        if _close(v, theirs, TOL_LINE):
            return ("ok", "") if label == "same" else ("explained", f"system value = {label}")
    notes, implied = [], []
    if gst:
        implied.append(theirs / (1 + gst / 100))
        notes.append(f"system value implies a pre-GST base of {implied[-1]:.2f} ({gst:g}% GST)")
    if dep:
        implied.append(theirs / (1 - dep / 100))
        notes.append(f"system value implies a pre-depreciation amount of {implied[-1]:.2f} ({dep:g}% dep.)")
    if gst and dep:
        implied.append(theirs / ((1 - dep / 100) * (1 + gst / 100)))
    for base in implied:
        if abs(base - ours) > TOL_LINE and _digits(base) == _digits(ours):
            notes.append(f"digits of implied base {base:.2f} are a permutation of page value {_fmt(ours)}: possible digit transposition")
            break
    return "mismatch", "; ".join(notes)


def _match_lines(items: list[dict], lines: list[dict]):
    pairs = []
    for i, it in enumerate(items):
        for j, ln in enumerate(lines):
            sc = SequenceMatcher(None, _norm(it["description"]), _norm(ln.get("part_name", ""))).ratio()
            if sc >= MATCH_THRESHOLD:
                sa = _sys_amount(ln)[0]
                dist = abs(it["amount"] - sa) if it["amount"] is not None and sa is not None else 1e9
                pairs.append((-sc, dist, i, j, sc))
    pairs.sort()
    used_i, used_j, matched = set(), set(), []
    for _, _, i, j, sc in pairs:
        if i in used_i or j in used_j:
            continue
        used_i.add(i); used_j.add(j); matched.append((i, j, sc))
    return sorted(matched), [i for i in range(len(items)) if i not in used_i], [j for j in range(len(lines)) if j not in used_j]


def compare_with_system(data: dict, system: dict, name: str) -> list[dict]:
    pe, out = system["parsed_estimate"], []
    items, lines = data["items"], pe.get("lines", [])

    # 1. total
    ours, theirs, tax = data["total"], pe.get("stated_total_estimate_cost"), data.get("tax_added")
    a = ("total on page", ours, "extraction: total (read from image)")
    b = ("system stated total", theirs, f"{name}: parsed_estimate.stated_total_estimate_cost")
    if ours is None or theirs is None:
        out.append(_finding("total_vs_system", "not_checkable", a, b, "one side is null"))
    elif _close(ours, theirs, TOL_TOTAL):
        out.append(_finding("total_vs_system", "ok", a, b))
    elif tax is not None and _close(ours - tax, theirs, TOL_TOTAL):
        out.append(_finding("total_vs_system", "explained", a, b,
                            f"system total equals page total minus the tax rows ({_fmt(tax)}): system stored the pre-tax figure"))
    else:
        out.append(_finding("total_vs_system", "mismatch", a, b, f"difference {_fmt(round(ours - theirs, 2))}"))

    # 2. parts / labour split
    for kind, key in (("part", "total_part_estimation_cost"), ("labour", "labour_estimation_cost")):
        sel = [i for i in items if i["kind"] == kind]
        unknown = [i for i in items if i["kind"] is None or i["amount"] is None]
        s = round(sum(i["amount"] for i in sel if i["amount"] is not None), 2)
        a = (f"sum of extracted {kind} lines", s, f"extraction: sum(items[kind={kind}].amount)")
        b = (f"system {key}", pe.get(key), f"{name}: parsed_estimate.{key}")
        if pe.get(key) is None:
            out.append(_finding(f"{kind}_split_vs_system", "not_checkable", a, b, "system value missing"))
        elif unknown:
            out.append(_finding(f"{kind}_split_vs_system", "not_checkable", a, b,
                                f"{len(unknown)} line(s) have null kind or amount, so the split is unreliable"))
        elif _close(s, pe[key], TOL_TOTAL):
            out.append(_finding(f"{kind}_split_vs_system", "ok", a, b))
        else:
            out.append(_finding(f"{kind}_split_vs_system", "mismatch", a, b,
                                "kind is judged from the description (denting/painting/aligning = labour); the system may classify differently"))

    # 3. claims-system amounts are an assessed/settled view, not a transcription
    est = (system.get("claims_system_amounts") or {}).get("estimated_amount_listed")
    if est is not None and data["total"] is not None:
        out.append(_finding("page_total_vs_claims_amount", "info",
                            ("total on page", data["total"], "extraction: total"),
                            ("claims system estimated_amount_listed", est, f"{name}: claims_system_amounts.estimated_amount_listed"),
                            "not treated as a parsing error: this field is likely an assessed figure (depreciation/negotiation), not the page total"))

    # 4. estimate date vs loss date
    loss = system.get("loss_date")
    if data["date"] and loss:
        gap = (date.fromisoformat(data["date"]) - date.fromisoformat(loss)).days
        out.append(_finding("estimate_date_vs_loss_date", "mismatch" if gap < 0 else "info",
                            ("estimate date", data["date"], "extraction: date"),
                            ("loss date", loss, f"{name}: loss_date"),
                            "estimate is dated BEFORE the loss: date misread?" if gap < 0 else f"estimate is {gap} day(s) after the loss (plausible)"))

    # 5. lines
    matched, only_ours, only_sys = _match_lines(items, lines)
    for i, j, sc in matched:
        it, ln = items[i], lines[j]
        if _norm(it["description"]) != _norm(ln.get("part_name", "")):
            out.append(_finding("line_description", "mismatch",
                                (f"item[{i}] description", it["description"], f"extraction: items[{i}].description"),
                                (f"line[{j}] part_name", ln.get("part_name"), f"{name}: parsed_estimate.lines[{j}].part_name"),
                                f"matched by similarity {sc:.2f}"))
        sa, key = _sys_amount(ln)
        a = (f"item[{i}] amount ({it['description']})", it["amount"], f"extraction: items[{i}].amount (read from image)")
        b = (f"line[{j}] {key}", sa, f"{name}: parsed_estimate.lines[{j}].{key}")
        if it["amount"] is None or sa is None:
            out.append(_finding("line_amount", "not_checkable", a, b, "one side is null (unreadable/missing)"))
        else:
            st, note = explain_amount(it["amount"], sa, ln.get("part_gst_perc") or 0, ln.get("part_dep_perc") or 0)
            out.append(_finding("line_amount", st, a, b, note))
        if it["qty"] is not None and ln.get("part_quantity") is not None and it["qty"] != ln["part_quantity"]:
            out.append(_finding("line_qty", "mismatch",
                                (f"item[{i}] qty", it["qty"], f"extraction: items[{i}].qty"),
                                (f"line[{j}] part_quantity", ln["part_quantity"], f"{name}: parsed_estimate.lines[{j}].part_quantity")))
    for i in only_ours:
        it = items[i]
        out.append(_finding("line_missing_in_system", "mismatch",
                            (f"item[{i}]: {it['description']}", it["amount"], f"extraction: items[{i}] (read from image)"),
                            ("no matching system line", None, f"{name}: parsed_estimate.lines"),
                            "present on the page, absent from the system parse"))
    for j in only_sys:
        ln = lines[j]
        out.append(_finding("line_missing_in_extraction", "mismatch",
                            ("no matching extracted item", None, "extraction: items"),
                            (f"line[{j}]: {ln.get('part_name')}", _sys_amount(ln)[0], f"{name}: parsed_estimate.lines[{j}]"),
                            "in the system parse but not found in this extraction (extraction miss, or the system invented/duplicated it)"))
    return out


def reconcile(data: Optional[dict], system: Optional[dict], system_name: str = "system_json") -> dict:
    findings: list[dict] = []
    notes: list[str] = []
    if data is not None:
        findings += check_internal(data)
    else:
        notes.append("extraction failed: page-based checks skipped")
    if system is not None:
        findings += check_system_internal(system, system_name)
        if data is not None:
            findings += compare_with_system(data, system, system_name)
    else:
        notes.append("no system JSON supplied: comparison skipped")
    return {"summary": dict(Counter(f["status"] for f in findings)), "notes": notes, "findings": findings}


# --------------------------------------------------------------------------- markdown
def render_markdown(title: str, record: dict, rec: dict, show_ok: bool = False) -> str:
    L = [f"## {title}", ""]
    tok = (record.get("input_tokens"), record.get("output_tokens"))
    L.append(
        f"- extraction: **{record['status']}** via `{record['provider']}`"
        f"{' / ' + record['model'] if record.get('model') else ''}; attempts {record['attempts']} "
        f"(retries {record['retries']}); {record['elapsed_s']}s; tokens in/out {tok[0]}/{tok[1]}"
    )
    L.append(f"- checks: {', '.join(f'{k}={v}' for k, v in sorted(rec['summary'].items())) or 'none'}")
    for n in rec["notes"]:
        L.append(f"- note: {n}")
    L.append("")
    rows = [f for f in rec["findings"] if show_ok or f["status"] != "ok"]
    if rows:
        L += ["| status | check | our side | other side | note |", "|---|---|---|---|---|"]
        for f in rows:
            def cell(x):
                return f"{x['label']}: **{_fmt(x['value'])}**<br><sub>{x['source']}</sub>"
            L.append(f"| {f['status']} | {f['check']} | {cell(f['a'])} | {cell(f['b'])} | {f['note']} |")
        L.append("")
    return "\n".join(L)
