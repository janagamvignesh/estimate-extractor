"""CLI:  python -m extractor.cli data/sample --provider anthropic

For each image: extract -> validate/retry -> save JSON -> reconcile with the matching *_data.json -> report.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

from .logging_utils import CallLogger
from .pipeline import extract_estimate
from .providers import AnthropicProvider, ProviderError, ReplayProvider
from .reconcile import reconcile, render_markdown

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def find_images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        out += sorted(x for x in p.iterdir() if x.suffix.lower() in IMG_EXT) if p.is_dir() else [p]
    return out


def find_system_json(image: Path, search_dirs: list[Path]) -> Path | None:
    m = re.search(r"(CLAIM_[A-Z0-9]+)", image.name)
    for d in search_dirs:
        if m and (d / f"{m.group(1)}_data.json").exists():
            return d / f"{m.group(1)}_data.json"
        if (d / f"{image.stem}_data.json").exists():
            return d / f"{image.stem}_data.json"
    return None


def build_provider(args):
    if args.provider == "anthropic":
        return AnthropicProvider(model=args.model, structured=args.structured)
    if args.provider == "tesseract":
        from .ocr_fallback import TesseractProvider
        return TesseractProvider()
    if args.provider == "replay":
        return ReplayProvider(Path(args.replay_dir))
    raise SystemExit(f"unknown provider {args.provider}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="image files and/or directories")
    ap.add_argument("--provider", choices=["anthropic", "tesseract", "replay"], default="anthropic")
    ap.add_argument("--model", default=None, help="override model (default: $EXTRACT_MODEL or built-in)")
    ap.add_argument("--structured", action="store_true", help="anthropic: force a tool call with the JSON schema")
    ap.add_argument("--replay-dir", default="tests/fixtures", help="replay provider: dir of CLAIM_X*.json (offline demo)")
    ap.add_argument("--system-dir", action="append", default=[], help="where to look for CLAIM_X_data.json (default: image dir)")
    ap.add_argument("--out", default="runs", help="output root")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    images = find_images(args.inputs)
    if not images:
        print("no images found", file=sys.stderr)
        return 2
    try:
        provider = build_provider(args)
    except ProviderError as e:
        print(f"provider setup failed: {e}", file=sys.stderr)
        return 2

    run_dir = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    call_logger = CallLogger(run_dir / "calls.jsonl")
    report = [f"# Extraction run {run_dir.name}", f"provider: `{provider.name}`", ""]
    n_ok = 0
    for img in images:
        record = extract_estimate(img, provider, max_retries=args.max_retries, call_logger=call_logger)
        n_ok += record["status"] == "ok"
        sys_path = find_system_json(img, [Path(d) for d in args.system_dir] + [img.parent])
        system = json.loads(sys_path.read_text(encoding="utf-8")) if sys_path else None
        rec = reconcile(record["data"], system, sys_path.name if sys_path else "system_json")
        stem = img.stem
        (run_dir / f"{stem}.extraction.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        (run_dir / f"{stem}.reconciliation.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        report.append(render_markdown(img.name, record, rec))
        print(f"{img.name}: {record['status']} (attempts={record['attempts']}, {record['elapsed_s']}s) checks={rec['summary']}")
    (run_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\n{n_ok}/{len(images)} extracted. Outputs in {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
