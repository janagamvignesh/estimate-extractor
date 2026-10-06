"""Per-call JSONL log: one line per model call (including retries)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("extractor")


class CallLogger:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **fields) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        log.info(
            "call image=%s attempt=%s outcome=%s %.2fs tokens(in/out)=%s/%s",
            rec.get("image"), rec.get("attempt"), rec.get("outcome"), rec.get("elapsed_s", 0),
            rec.get("input_tokens"), rec.get("output_tokens"),
        )
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
