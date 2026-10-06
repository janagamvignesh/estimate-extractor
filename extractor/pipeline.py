"""extract -> parse -> validate -> retry (<=2, clearer prompt) -> ok | failed. Never raises on bad model output."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from . import prompts
from .logging_utils import CallLogger
from .providers import Provider
from .schema import ParseError, normalize, parse_json_text, validate_estimate


def _sum_opt(values):
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def extract_estimate(
    image_path: Path,
    provider: Provider,
    *,
    max_retries: int = 2,
    call_logger: Optional[CallLogger] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Returns a record: status 'ok' (data=validated dict) or 'failed' (data=None). Never raises."""
    image_path = Path(image_path)
    call_logger = call_logger or CallLogger(None)
    max_attempts = 1 + (max_retries if provider.retry_on_invalid else 0)

    errors: list[dict] = []
    in_tok: list = []
    out_tok: list = []
    model = None
    prev_errors: list[str] = []
    prev_raw: Optional[str] = None
    last_raw: Optional[str] = None
    t_start = time.perf_counter()
    data = None
    attempts = 0

    for attempt in range(max_attempts):
        attempts = attempt + 1
        prompt = prompts.first_prompt() if attempt == 0 else prompts.retry_prompt(attempt, prev_errors, prev_raw)
        t0 = time.perf_counter()
        outcome, messages, result = "valid", [], None
        try:
            result = provider.extract(image_path, prompts.SYSTEM, prompt)
            model = result.model or model
            in_tok.append(result.input_tokens)
            out_tok.append(result.output_tokens)
            last_raw = result.text
            try:
                obj = parse_json_text(result.text)
            except ParseError as e:
                outcome, messages = "invalid_json", [str(e)]
            else:
                messages = validate_estimate(obj)
                if messages:
                    outcome = "schema_invalid"
                else:
                    data = normalize(obj)
        except Exception as e:  # provider/network/auth errors: record, don't crash
            outcome, messages = "provider_error", [f"{type(e).__name__}: {e}"[:300]]
        elapsed = time.perf_counter() - t0

        call_logger.log(
            image=image_path.name,
            provider=provider.name,
            model=(result.model if result else None) or model,
            attempt=attempt + 1,
            is_retry=attempt > 0,
            outcome=outcome,
            elapsed_s=round(elapsed, 3),
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            errors=messages,
        )
        if outcome == "valid":
            break
        errors.append({"attempt": attempt + 1, "kind": outcome, "messages": messages})
        prev_errors, prev_raw = messages, last_raw
        if outcome == "provider_error" and attempt + 1 < max_attempts:
            sleep(min(2 ** attempt, 8))

    return {
        "image": image_path.name,
        "provider": provider.name,
        "model": model,
        "status": "ok" if data is not None else "failed",
        "data": data,
        "attempts": attempts,
        "retries": max(0, attempts - 1),
        "elapsed_s": round(time.perf_counter() - t_start, 3),
        "input_tokens": _sum_opt(in_tok),
        "output_tokens": _sum_opt(out_tok),
        "errors": errors,
        "last_raw": None if data is not None else (last_raw[:2000] if last_raw else None),
    }
