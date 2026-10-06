# Estimate extraction (Fusion Galaxxy assignment 3)

Reads a garage repair-estimate image with a vision model, returns schema-checked JSON (`null` when unreadable,
never a guess), then cross-checks the result against the page's own arithmetic and against the claims-system
parse. Every disagreement is listed with both numbers and where each came from.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # never commit keys; see .env.example

python -m extractor.cli data/sample -v                    # LLM path (default provider: anthropic)
python -m extractor.cli new_images/ --system-dir new_json # unseen estimates; CLAIM_X_data.json is matched by name
python -m extractor.cli data/sample --provider tesseract  # no API key: OCR + rules (needs the `tesseract` binary)
python -m extractor.cli data/sample --provider replay     # offline demo of validate/reconcile; replays hand-written fixtures, NOT a model
python -m pytest -q                                        # 46 tests, no network or key needed
```

Each run writes `runs/<timestamp>/`: `<image>.extraction.json` (status, data, attempts, retries, time, tokens,
errors), `<image>.reconciliation.json` (all findings), `report.md`, and `calls.jsonl` (one line per model call
including retries: time, retry flag, outcome, input/output tokens when the provider returns them).
Model: `$EXTRACT_MODEL` or `--model` (default `claude-sonnet-5-5`). `--structured` forces a tool call with the schema.

## Output contract

```json
{"date": "YYYY-MM-DD|null",
 "items": [{"description": "str", "qty": "number|null", "amount": "number|null", "kind": "part|labour|null"}],
 "total": "number|null", "tax_added": "number|null"}
```
`tax_added` is one extension to the brief: tax rows the page adds on top of the line amounts (e.g. CGST+SGST under
the table). Without it, "items + tax = total" looks identical to "a line was misread". `null` if none/included.
Prompt rules worth knowing: day-first dates; a running 1..N number is a serial, not a quantity (Claim A's "Qty"
column is one); ignore print/scan stamps and vehicle sale dates; `total` is what is written, never my sum.

## Pipeline

1. `providers.py` call (image is EXIF-rotated, downsized to <=1800px, sent as base64).
2. `schema.py` tolerant JSON parse (fences/prose) -> JSON-Schema validation (+ real-calendar-date check).
3. Invalid -> retry up to 2 times; the retry prompt quotes the validator's errors and the last one adds the exact
   shape to follow. Network/API errors count as attempts too (with backoff). After the last failure the record is
   `status: "failed"` with `last_raw` kept; nothing raises, the batch continues.
4. `reconcile.py` (statuses: `ok`, `explained`, `mismatch`, `not_checkable`, `info`):
   - items sum vs page total (accepting `items + tax_added = total`); null amounts make it `not_checkable`, not a pass
   - system record internal consistency (its lines vs its own stated total, parts+labour+paint vs total)
   - total, parts/labour split, and estimate-date-vs-loss-date vs the system record
   - line matching by description similarity, then per line: description differences, amount differences, and whether
     the system amount is the page amount with GST and/or depreciation applied (`explained`) or unexplained
     (`mismatch`, plus the implied base amount and a digit-transposition hint); lines missing on either side
   - `estimated_amount_listed` is reported as `info` only: it looks like an assessed figure, not a transcription

## What I found in the three samples

Claims B and C are printed, and both pass their own arithmetic, so I hand-verified them line by line
(`tests/fixtures/*.reference.json`; B items = 29,764; C parts 18,855 + labour 21,795 + CGST/SGST 3,923.10 = 44,573.10).
Full tables: `docs/sample_reconciliation_replay.md`. The system data is wrong in ways the checks catch:

| Claim | page says | system parse says |
|---|---|---|
| B fascia | `ASSY RR FASCIA ... 4030.00` | `FR FASCIA`, part number differs, 5169.92 (= 4039 + 28% GST: base misread, and GST added to an amount that already includes it) |
| B bracket | `BRAKIT ... 156.00` | 147.20 (= 115 + 28% GST) |
| B back light | `3925.00` | 4631.50 (= 3925 + 18% GST, double-counted) and a part number with a digit dropped |
| B system total | 29,764 | its own lines sum to 31,601.62 |
| C panel quarter RH | `5,945.00` | 6,484.10 (= 5,495 + 18%: digits 5945/5495 transposed) |
| C labour | 11 labour lines, 21,795 | none of the 11 lines exist in the system's `lines` (only the aggregate) |
| C total | 44,573.10 | 40,650 (the pre-tax figure; page adds 3,923.10 CGST+SGST) |
| A system total | 43,100 | its own lines sum to 24,855 (lines look post-depreciation, the total is not) |

Claim A (handwritten): I did not write a reference. The amount column drifts against the rows and has stray marks,
and digits such as 700/7000, 1200/12000, 1450/950 and 8800/800 are not reliably readable by eye. The printed total
(43,100) cannot settle it, because two misreads can offset each other. The intended behaviour is `null` for such
amounts, which makes the sum check `not_checkable` rather than falsely passing. One example I could not reconcile
and did not verify: the system's 4140 for "Front Bumper OF" at 50% depreciation implies a page amount of 8280.

## What is and is not verified

- Verified (tests): schema acceptance/rejection, retry and failure paths, per-call logging, the real `anthropic` SDK
  request/response handling against a mock HTTP transport (text mode, forced-tool mode, retry, HTTP 401),
  reconciliation on B and C, and the OCR rule parser.
- **Not verified in this repo: model accuracy.** No live LLM call has been made from the code here, so there are no
  real token/latency numbers yet. TODO before submitting: run `--provider anthropic` on the samples and commit
  the resulting `calls.jsonl` and `report.md` under `docs/`, and note any model mistakes.
- The OCR fallback was run for real on the samples (`docs/ocr_fallback_run.md`). A: no items found, recorded as
  failed. C: all 19 amounts correct, items sum to 40,650, but date is null (OCR never read it), `tax_added` is
  not extracted so the sum check reads `mismatch`, descriptions and kinds are noisy. B: the scan's slashed-zero
  font is read as 8/9 (156.00 -> 156.09, 18290.00 -> 18298.28), so items sum to 29,772.83 against a printed
  29,764.08 and the reconciler flags it. Tesseract's confidence did not separate right from wrong digits, so the
  fallback cannot certify its own amounts. Treat it as a degraded mode, not an answer.

## Assumptions and limits

- `kind` is judged from the description: denting/painting/aligning/fitting/trim = labour. The system classifies
  Claim A's denting and painting as parts, so the labour split shows a mismatch there.
- Tolerances: 1.00 rupee for totals, 0.51 for lines.
- Only images are accepted (no PDF). Black boxes are never interpreted.
- Files are redacted real claim data: repo must stay private; do not publish `data/` or `runs/` (`runs/` is git-ignored).

## Next steps

Second-pass verification of amounts the model marks uncertain (crop and re-read); per-field confidence in the schema;
an eval set with more hand-labelled estimates; PDF input; the optional bonus (a voice-note or damage-photo input,
which would help cross-check which panels were actually damaged against the lines billed).

## AI tools used

TODO (edit to be true): Claude (claude.ai) was used to draft the scaffold, tests and this README. Things I changed
or checked by hand: ______.
