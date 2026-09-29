"""Run the three sample products twice and write results/<id>.json.

OCR stays on local RapidOCR either way, so a reviewer without an API key can reproduce the
default files. The second run reuses the OCR cache.

    python scripts/run_sample_bank.py              # judge off -> results/<id>.json
    python scripts/run_sample_bank.py --judge on   # judge on  -> results/<id>.judge-on.json
    python scripts/run_sample_bank.py --judge off
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from engine import list_products  # noqa: E402
from pipeline import check  # noqa: E402

OUT = ROOT / "results"
PRODUCTS = ("comvita", "seed", "arepa")


def _signature(run: dict) -> list[tuple]:
    return [(c["id"], c["text"], c["verdict"], c["status"]) for c in run["claims"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Comvita, Seed and Arepa twice.")
    parser.add_argument("--judge", choices=("on", "off"), default="off",
                        help="AI judge. Default off. On requires OPENAI_API_KEY in backend/.env.")
    args = parser.parse_args()
    use_ai = args.judge == "on"
    # Set after parse and before any OCR/judge call. Force these so backend/.env cannot flip them.
    os.environ["CLAIMCHECK_JUDGE"] = args.judge
    os.environ["CLAIMCHECK_OCR"] = "rapidocr"
    if use_ai:
        from dotenv import load_dotenv
        load_dotenv(ROOT / "backend" / ".env")
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise SystemExit("--judge on requires OPENAI_API_KEY in backend/.env")

    OUT.mkdir(exist_ok=True)
    by_id = {p.id: p for p in list_products()}
    missing = [pid for pid in PRODUCTS if pid not in by_id]
    if missing:
        raise SystemExit(f"missing product profiles: {missing}")

    suffix = ".judge-on.json" if use_ai else ".json"
    for pid in PRODUCTS:
        runs = []
        for n in (1, 2):
            t0 = time.perf_counter()
            run = check(by_id[pid], use_ai=use_ai)
            run["elapsed_s"] = round(time.perf_counter() - t0, 2)
            runs.append(run)
            print(f"{pid} run {n}: {run['summary']} in {run['elapsed_s']}s", flush=True)
        identical = _signature(runs[0]) == _signature(runs[1])
        payload = {
            "product_id": pid,
            "name": by_id[pid].name,
            "regime": by_id[pid].regime,
            "ai_judge": use_ai,
            "ocr": "rapidocr",
            "verdicts_identical": identical,
            "runs": runs,
        }
        path = OUT / f"{pid}{suffix}"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} identical={identical}", flush=True)


if __name__ == "__main__":
    main()
