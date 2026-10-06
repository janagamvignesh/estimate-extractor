"""Rule-based OCR fallback (Tesseract). Use only when no model access is available.

Honest limits (also in README): it needs a clean printed table. It reads no handwriting, never sets qty,
takes the largest 2-decimal number on a line as the amount, and returns an error rather than an empty
result when it finds no line items. Tesseract's word confidence did NOT separate right from wrong digits on
the sample scans (e.g. 156.00 read as 156.09 with confidence 73), so amounts are NOT self-certified: rely on
the reconciliation report (items-vs-total) to see when OCR output cannot be trusted.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .providers import Provider, ProviderError, ProviderResult

MONEY = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)")
DATE = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
SKIP = re.compile(
    r"sub\s*-?\s*total|\bcgst\b|\bsgst\b|\bigst\b|ugst|taxable|net\s+estimate|grand\s+total|total\s+amount|"
    r"\btotal\b|rupees|amount\s+in\s+words|hsn|particulars|description|estimated\s+days|\btime\b|@\s*[\d.,]+\s*%",
    re.I,
)
TOTAL_LINE = re.compile(r"net\s+estimate|grand\s+total|total\s+amount|net\s+payable", re.I)
LABOUR_WORDS = re.compile(r"denting|painting|paint|labou?r|charge|aligning|repair|fitting|trim|seat|service", re.I)
LABOUR_HEADER = re.compile(r"^\s*(labou?r|body\s+shop|paint\s+shop|services?)\b[^0-9]*$", re.I)
PARTS_HEADER = re.compile(r"^\s*(parts?|drive\s+train.*|lights?)\s*$", re.I)


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


NOT_DOC_DATE = re.compile(r"sale|expiry|expires|valid|fir\b|dob|birth|warranty|insur", re.I)


def parse_ocr_text(text: str) -> dict:
    # Only dates on lines that are not clearly some other date (vehicle sale, policy expiry, FIR...).
    # If the remaining candidates disagree, return null rather than pick one.
    cands = set()
    for line in text.splitlines():
        if NOT_DOC_DATE.search(line):
            continue
        for m in DATE.finditer(line):
            d, mth, y = (int(g) for g in m.groups())
            try:
                cands.add(date(y, mth, d).isoformat())
            except ValueError:
                continue
    est_date = next(iter(cands)) if len(cands) == 1 else None

    total = None
    for line in text.splitlines():
        if TOTAL_LINE.search(line):
            nums = MONEY.findall(line)
            if nums:
                total = _to_float(nums[-1])

    items, mode = [], None
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if LABOUR_HEADER.match(line):
            mode = "labour"
            continue
        if PARTS_HEADER.match(line):
            mode = "part"
            continue
        nums = MONEY.findall(line)
        if not nums or SKIP.search(line):
            continue
        desc = line[: MONEY.search(line).start()].strip(" |:-_")
        desc = re.sub(r"^\d{1,2}\s+", "", desc)  # leading serial number
        if not re.search(r"[A-Za-z]{4,}", desc):   # needs at least one real word: drops OCR noise like "cost @"
            continue
        amount = max(_to_float(n) for n in nums)
        if amount <= 0:
            continue
        kind = mode or ("labour" if LABOUR_WORDS.search(desc) else None)
        items.append({"description": desc, "qty": None, "amount": amount, "kind": kind})
    return {"date": est_date, "items": items, "total": total, "tax_added": None}


class TesseractProvider(Provider):
    name = "tesseract"
    retry_on_invalid = False

    def extract(self, image_path: Path, system: str, prompt: str) -> ProviderResult:
        try:
            import pytesseract
            from PIL import Image, ImageOps

            im = ImageOps.exif_transpose(Image.open(image_path)).convert("L")
            im = ImageOps.autocontrast(im.resize((im.width * 2, im.height * 2), Image.LANCZOS), cutoff=2)
            text = pytesseract.image_to_string(im, config="--psm 6")
        except Exception as e:  # missing binary, unreadable file...
            raise ProviderError(f"OCR failed: {e}") from e
        parsed = parse_ocr_text(text)
        if not parsed["items"]:
            raise ProviderError("OCR found no line items (handwritten or too noisy for the rule-based parser)")
        return ProviderResult(text=json.dumps(parsed), model="tesseract+rules")
