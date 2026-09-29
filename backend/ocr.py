"""OpenAI vision OCR with an on-disk cache.

Results are stored under extracted/, keyed by image SHA-256, model and prompt, so a repeat of the
same file is identical and does not call the API again.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
from pathlib import Path

from rulebank import REPO_ROOT

CACHE_DIR = REPO_ROOT / "extracted"
log = logging.getLogger("claimcheck.ocr")


class OCRError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").split("#")[0].strip()
    if not key:
        raise OCRError("OPENAI_API_KEY is required for image OCR")
    return key


def ocr_model() -> str:
    return os.environ.get("OPENAI_OCR_MODEL", "gpt-4o").split("#")[0].strip() or "gpt-4o"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_path(image: Path) -> Path:
    try:
        rel = image.resolve().relative_to(REPO_ROOT)
    except ValueError:
        rel = Path("external") / f"{_sha256(image)}{image.suffix.lower()}"
    return CACHE_DIR / rel.with_name(rel.name + ".openai.json")


OPENAI_PROMPT = (
    "Transcribe ALL visible text in this product image (packaging, label, listing or web page) exactly as printed. "
    "Return one entry per text block in reading order: a heading, a paragraph, a bullet point, a badge or a table "
    "row. Join lines that wrap within the same sentence or bullet into one entry. Keep spelling, symbols "
    "(®, ™, %, $) and numbers verbatim; do not correct, translate, summarise or add anything. "
    "Skip barcodes and purely decorative marks."
)
OPENAI_SCHEMA = {
    "name": "ocr",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"blocks": {"type": "array", "items": {"type": "string"}}},
        "required": ["blocks"],
        "additionalProperties": False,
    },
}
NATIVE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def _data_url(image: Path) -> str:
    mime = NATIVE.get(image.suffix.lower())
    if mime:
        data = image.read_bytes()
    else:
        from PIL import Image

        buf = io.BytesIO()
        Image.open(image).convert("RGB").save(buf, "PNG")
        data, mime = buf.getvalue(), "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _run_openai(image: Path, model: str) -> list[str]:
    try:
        from openai import OpenAI, OpenAIError
    except ImportError as e:
        raise OCRError("the openai package is not installed (pip install openai)") from e
    log.info("OpenAI OCR request: %s (%d KB) model=%s", image.name, image.stat().st_size // 1024, model)
    t0 = time.perf_counter()
    try:
        resp = OpenAI(api_key=_api_key(), timeout=120).chat.completions.create(
            model=model,
            temperature=0,
            seed=0,
            response_format={"type": "json_schema", "json_schema": OPENAI_SCHEMA},
            messages=[{"role": "user", "content": [
                {"type": "text", "text": OPENAI_PROMPT},
                {"type": "image_url", "image_url": {"url": _data_url(image), "detail": "high"}},
            ]}],
        )
    except OpenAIError as e:
        log.error("OpenAI OCR request failed for %s after %.1fs: %s: %s",
                  image.name, time.perf_counter() - t0, type(e).__name__, e)
        raise OCRError(f"OpenAI OCR failed for {image.name}: {type(e).__name__}: {e}") from e
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    log.info("OpenAI OCR response for %s in %.1fs (finish=%s, tokens in/out=%s/%s)",
             image.name, time.perf_counter() - t0, resp.choices[0].finish_reason,
             getattr(usage, "prompt_tokens", "?"), getattr(usage, "completion_tokens", "?"))
    try:
        blocks = json.loads(content)["blocks"]
    except (ValueError, KeyError, TypeError) as e:
        log.error("OpenAI OCR unexpected response for %s: %r", image.name, content[:300])
        raise OCRError(f"OpenAI OCR returned an unexpected response for {image.name}") from e
    return [b.strip() for b in blocks if isinstance(b, str) and b.strip()]


def openai_blocks(image: Path, refresh: bool = False) -> tuple[list[str], str]:
    """Returns (text blocks in reading order, method) using an OpenAI vision model."""
    image = Path(image)
    model = ocr_model()
    cache = cache_path(image)
    digest = _sha256(image)
    if cache.exists() and not refresh:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sha256") == digest and data.get("model") == model and data.get("prompt") == OPENAI_PROMPT:
            log.info("OpenAI OCR cache hit: %s -> %d blocks", image.name, len(data["blocks"]))
            return data["blocks"], f"ocr-openai {model} (cached)"
        log.info("OpenAI OCR cache stale for %s (model/prompt/image changed)", image.name)
    _api_key()
    blocks = _run_openai(image, model)
    log.info("OpenAI OCR done: %s -> %d blocks", image.name, len(blocks))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "file": image.name, "sha256": digest, "model": model, "prompt": OPENAI_PROMPT, "blocks": blocks,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return blocks, f"ocr-openai {model}"
