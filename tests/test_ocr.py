"""OpenAI OCR: cache reuse, missing API key, and no leftover local-OCR engine."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import ocr
from ocr import OCRError, openai_blocks


class OpenaiOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_source_tree_has_no_local_ocr_engine(self):
        skip = {".venv", "node_modules", ".next", "extracted", ".git"}
        hits = []
        for path in self.root.rglob("*"):
            if any(part in skip for part in path.parts):
                continue
            if path.name == "test_ocr.py":
                continue
            if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".txt", ".example", ".json", ".tsx", ".ts"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "rapidocr" in text.lower() or "onnxruntime" in text.lower():
                hits.append(str(path.relative_to(self.root)))
        self.assertEqual(hits, [], f"local OCR engine still referenced in {hits}")

    def test_missing_key_is_an_ocr_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "label.png"
            image.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
                b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            with patch.dict("os.environ", {"OPENAI_API_KEY": "", "OPENAI_OCR_MODEL": "gpt-4o"}, clear=False):
                with self.assertRaises(OCRError):
                    openai_blocks(image)

    def test_cache_avoids_a_second_api_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "pack.webp"
            image.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
            cache = ocr.cache_path(image)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({
                "file": image.name,
                "sha256": ocr._sha256(image),
                "model": "gpt-4o",
                "prompt": ocr.OPENAI_PROMPT,
                "blocks": ["Clinically proven to boost focus."],
            }), encoding="utf-8")
            with patch.object(ocr, "_run_openai", side_effect=AssertionError("API should not be called")):
                with patch.dict("os.environ", {"OPENAI_OCR_MODEL": "gpt-4o"}, clear=False):
                    blocks, method = openai_blocks(image)
            self.assertEqual(blocks, ["Clinically proven to boost focus."])
            self.assertIn("cached", method)


if __name__ == "__main__":
    unittest.main()
