"""OCR with an on-disk cache: OpenAI vision (when OPENAI_API_KEY is set) or local RapidOCR (ONNX runtime).

The cache (extracted/<path>.ocr.json / .openai.json) means repeated checks of the same image skip OCR and always
see identical text. A cache entry is only reused if the image's SHA-256 (and, for OpenAI, the model) matches.
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


def backend() -> str:
    """CLAIMCHECK_OCR=openai|rapidocr; defaults to openai when an API key is configured."""
    forced = os.environ.get("CLAIMCHECK_OCR", "").split("#")[0].strip().lower()
    has_key = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if forced in ("openai", "rapidocr"):
        choice = forced
    else:
        choice = "openai" if has_key else "rapidocr"
    if choice == "openai" and not has_key:
        log.warning("OCR engine: openai requested but OPENAI_API_KEY is not set")
    log.info("OCR engine: %s (CLAIMCHECK_OCR=%r, api key %s)", choice, forced or None, "set" if has_key else "missing")
    return choice


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_path(image: Path) -> Path:
    try:
        rel = image.resolve().relative_to(REPO_ROOT)
    except ValueError:
        # Uploads and other outside files: content-addressed, so temp names don't pile up.
        rel = Path("external") / f"{_sha256(image)}{image.suffix.lower()}"
    return CACHE_DIR / rel.with_name(rel.name + ".ocr.json")


ENGINE = "-onnxruntime: full image (short side >= 1600 px) + overlapping full-width bands"
CACHE_VERSION = 3


def _ocr_region(engine, img, origin: tuple[int, int], min_short_side: int, tag: str) -> list[dict]:
    import numpy as np
    from PIL import Image

    scale = max(1.0, min_short_side / min(img.size))
    if scale > 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    result, _ = engine(np.asarray(img)[:, :, ::-1])  # RGB -> BGR
    lines = []
    for box, text, score in result or []:
        xs = [origin[0] + p[0] / scale for p in box]
        ys = [origin[1] + p[1] / scale for p in box]
        if text.strip():
            lines.append({
                "text": text.strip(),
                "box": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
                "score": round(float(score), 4),
                "pass": tag,
            })
    return lines


def _covered(box: list[float], by: list[float]) -> float:
    w = min(box[2], by[2]) - max(box[0], by[0])
    h = min(box[3], by[3]) - max(box[1], by[1])
    area = (box[2] - box[0]) * (box[3] - box[1])
    return max(w, 0) * max(h, 0) / area if area > 0 else 0.0


def _run_ocr(image: Path) -> list[dict]:
    try:
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:  # pragma: no cover - depends on optional dependency
        raise RuntimeError(
            f"No OCR cache for {image} and rapidocr-onnxruntime is not installed "
            "(pip install rapidocr-onnxruntime)"
        ) from e
    engine = RapidOCR()
    img = Image.open(image).convert("RGB")
    lines = _ocr_region(engine, img, (0, 0), 1600, "full")

    # The detector skips some lines on busy artwork; a second pass over upscaled, half-overlapping full-width
    # bands finds most of them. A band line is kept only if it isn't cut by the band edge and no accepted line
    # already covers it.
    W, H = img.size
    bh = round(H / 4)
    extra = []
    for y0 in sorted(set(range(0, H - bh, bh // 2)) | {H - bh}):
        for l in _ocr_region(engine, img.crop((0, y0, W, y0 + bh)), (0, y0), 1200, "band"):
            b = l["box"]
            if not ((b[1] - y0 < 3 and y0 > 0) or (y0 + bh - b[3] < 3 and y0 + bh < H)):
                extra.append(l)
    for l in sorted(extra, key=lambda l: -len(l["text"])):
        if all(_covered(l["box"], o["box"]) < 0.3 and _covered(o["box"], l["box"]) < 0.3 for o in lines):
            lines.append(l)
    return sorted(lines, key=lambda l: (l["box"][1], l["box"][0]))


def ocr_lines(image: Path, refresh: bool = False) -> tuple[list[dict], str]:
    """Returns (lines, method); lines carry text, [x0, y0, x1, y1] box and confidence."""
    image = Path(image)
    cache = cache_path(image)
    digest = _sha256(image)
    if cache.exists() and not refresh:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sha256") == digest and data.get("version") == CACHE_VERSION:
            return data["lines"], "ocr (cached)"
    lines = _run_ocr(image)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "file": image.name,
        "sha256": digest,
        "version": CACHE_VERSION,
        "engine": ENGINE,
        "lines": lines,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return lines, "ocr"


# --------------------------------------------------------------------------------------------- OpenAI

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
    else:  # bmp / tiff: not accepted by the API, send as PNG
        from PIL import Image

        buf = io.BytesIO()
        Image.open(image).convert("RGB").save(buf, "PNG")
        data, mime = buf.getvalue(), "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _run_openai(image: Path, model: str) -> list[str]:
    try:
        from openai import OpenAI, OpenAIError
    except ImportError as e:  # pragma: no cover
        raise OCRError("OpenAI OCR selected but the openai package is not installed (pip install openai)") from e
    log.info("OpenAI OCR request: %s (%d KB) model=%s", image.name, image.stat().st_size // 1024, model)
    t0 = time.perf_counter()
    try:
        resp = OpenAI(timeout=120).chat.completions.create(
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
    model = os.environ.get("OPENAI_OCR_MODEL", "gpt-4o")
    cache = cache_path(image).with_suffix(".openai.json")
    digest = _sha256(image)
    if cache.exists() and not refresh:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sha256") == digest and data.get("model") == model and data.get("prompt") == OPENAI_PROMPT:
            log.info("OpenAI OCR cache hit: %s -> %d blocks", image.name, len(data["blocks"]))
            return data["blocks"], f"ocr-openai {model} (cached)"
        log.info("OpenAI OCR cache stale for %s (model/prompt/image changed)", image.name)
    blocks = _run_openai(image, model)
    log.info("OpenAI OCR done: %s -> %d blocks", image.name, len(blocks))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "file": image.name, "sha256": digest, "model": model, "prompt": OPENAI_PROMPT, "blocks": blocks,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return blocks, f"ocr-openai {model}"
