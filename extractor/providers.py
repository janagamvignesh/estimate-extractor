"""Model back-ends. Each returns raw text; validation/retry lives in pipeline.py."""
from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import ESTIMATE_SCHEMA

DEFAULT_MODEL = "claude-sonnet-5-5"


class ProviderError(RuntimeError):
    """Back-end could not produce a response (network, auth, nothing to parse...)."""


@dataclass
class ProviderResult:
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    model: Optional[str] = None


class Provider:
    name = "base"
    #: False for deterministic back-ends (OCR): re-asking cannot change the answer.
    retry_on_invalid = True

    def extract(self, image_path: Path, system: str, prompt: str) -> ProviderResult:
        raise NotImplementedError


def load_image_b64(path: Path, max_side: int = 1800) -> tuple[str, str]:
    """Return (base64, media_type). Applies EXIF rotation and downsizes very large images."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((round(im.size[0] * scale), round(im.size[1] * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


class AnthropicProvider(Provider):
    """Vision model via the Anthropic Messages API. Needs ANTHROPIC_API_KEY.

    structured=True forces a tool call whose input_schema is ESTIMATE_SCHEMA. Default is plain JSON
    text (schema is enforced by our own validator either way).
    """

    name = "anthropic"

    def __init__(self, model: str | None = None, max_tokens: int = 4096, structured: bool = False, client=None):
        self.model = model or os.environ.get("EXTRACT_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.structured = structured
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ProviderError("ANTHROPIC_API_KEY is not set")
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def extract(self, image_path: Path, system: str, prompt: str) -> ProviderResult:
        data, media_type = load_image_b64(image_path)
        kwargs = {}
        if self.structured:
            kwargs["tools"] = [
                {"name": "submit_estimate", "description": "Submit the transcribed estimate.", "input_schema": ESTIMATE_SCHEMA}
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "submit_estimate"}
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            **kwargs,
        )
        text = None
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                text = json.dumps(block.input)
                break
        if text is None:
            text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = getattr(resp, "usage", None)
        return ProviderResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            model=self.model,
        )


class ReplayProvider(Provider):
    """OFFLINE DEMO ONLY: replays a pre-written JSON per claim (file name contains e.g. CLAIM_B).

    Not a model. Exists so the validate -> reconcile -> report path can be run without an API key.
    """

    name = "replay"
    retry_on_invalid = False

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def extract(self, image_path: Path, system: str, prompt: str) -> ProviderResult:
        m = re.search(r"(CLAIM_[A-Z0-9]+)", Path(image_path).name)
        if not m:
            raise ProviderError(f"cannot infer claim key from {Path(image_path).name}")
        matches = sorted(self.directory.glob(f"{m.group(1)}*.json"))
        if not matches:
            raise ProviderError(f"no replay file for {m.group(1)} in {self.directory}")
        return ProviderResult(text=matches[0].read_text(encoding="utf-8"), model="replay")
