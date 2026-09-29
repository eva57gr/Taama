"""Run the three sample products twice and write results/<id>.json.

The verdict path is the deterministic engine: the AI judge is off and OCR is local RapidOCR,
so a reviewer without an API key can reproduce the files. The second run reuses the OCR cache,
which is how a repeat check behaves in the app.

    python scripts/run_sample_bank.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Set before the pipeline imports OCR / the judge.
os.environ["CLAIMCHECK_JUDGE"] = "off"
os.environ["CLAIMCHECK_OCR"] = "rapidocr"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from engine import list_products  # noqa: E402
from pipeline import check  # noqa: E402

OUT = ROOT / "results"
PRODUCTS = ("comvita", "seed", "arepa")


def _signature(run: dict) -> list[tuple]:
    return [(c["id"], c["text"], c["verdict"], c["status"]) for c in run["claims"]]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    by_id = {p.id: p for p in list_products()}
    missing = [pid for pid in PRODUCTS if pid not in by_id]
    if missing:
        raise SystemExit(f"missing product profiles: {missing}")

    for pid in PRODUCTS:
        runs = []
        for n in (1, 2):
            t0 = time.perf_counter()
            run = check(by_id[pid], use_ai=False)
            run["elapsed_s"] = round(time.perf_counter() - t0, 2)
            runs.append(run)
            print(f"{pid} run {n}: {run['summary']} in {run['elapsed_s']}s", flush=True)
        identical = _signature(runs[0]) == _signature(runs[1])
        payload = {
            "product_id": pid,
            "name": by_id[pid].name,
            "regime": by_id[pid].regime,
            "ai_judge": False,
            "ocr": "rapidocr",
            "verdicts_identical": identical,
            "runs": runs,
        }
        path = OUT / f"{pid}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} identical={identical}", flush=True)


if __name__ == "__main__":
    main()
