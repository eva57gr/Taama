"""End to end: sources -> blocks -> claims -> verdicts."""

from __future__ import annotations

import glob
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import judge as judge_mod
from engine import Engine, Product
from ingest import from_text, ingest
from models import Block, ClaimResult, most_severe
from rulebank import REPO_ROOT
from segment import segment

log = logging.getLogger("claimcheck.pipeline")
_JUDGES: dict[int, judge_mod.Judge] = {}
_JUDGE_LOCK = threading.Lock()


def _judge_for(engine: Engine) -> judge_mod.Judge:
    with _JUDGE_LOCK:
        if id(engine) not in _JUDGES:
            _JUDGES[id(engine)] = judge_mod.Judge(engine.rb)
        return _JUDGES[id(engine)]


def _combine(result: ClaimResult, hit) -> ClaimResult:
    """Hybrid verdict: the worse of the rule verdict and the AI verdict."""
    if hit is None:
        return result
    result.hits.append(hit)
    result.verdict = most_severe([h.verdict for h in result.hits])
    result.status = Engine._status(result.verdict, result.hits)
    result.justification = Engine._justify(result.verdict, result.hits)
    return result

VERDICT_ORDER = ("RED", "AMBER", "GREEN")


def expand_inputs(patterns: list[str]) -> list[str]:
    out = []
    for p in patterns:
        if re.match(r"https?://", p) or not glob.has_magic(p):
            out.append(p)
        else:
            matches = sorted(glob.glob(str(REPO_ROOT / p)))
            if not matches:
                raise FileNotFoundError(f"input pattern matched nothing: {p}")
            out += matches
    return [s if re.match(r"https?://", s) else str(REPO_ROOT / s) if not Path(s).is_absolute() else s for s in out]


def protected_words(product: Product) -> set[str]:
    words = {w.lower() for m in product.mask for w in re.findall(r"[\w'-]+", m)}
    return words | {p.lower() for p in product.properties}


def load_blocks(sources: list[str], product: Product, text: str | None = None, refresh_ocr: bool = False) -> list[Block]:
    blocks = from_text(text) if text else []
    for s in sources:
        blocks += ingest(s, protected_words(product), refresh_ocr)
    return blocks


def check(product: Product, sources: list[str] | None = None, text: str | None = None,
          engine: Engine | None = None, refresh_ocr: bool = False, use_ai: bool | None = None) -> dict:
    """One full run. Returns the assessed claims plus the text that was read but not assessed, and why."""
    engine = engine or Engine()
    sources = expand_inputs(product.inputs) if sources is None and text is None else (sources or [])
    extraction = segment(load_blocks(sources, product, text, refresh_ocr), product)
    results = [engine.check(c, product) for c in extraction.claims]
    use_ai = judge_mod.enabled() if use_ai is None else use_ai
    log.info("%d claims for %s (%s); AI judge %s", len(results), product.id, product.regime, "on" if use_ai else "off")
    if use_ai and results:
        j, t0 = _judge_for(engine), time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            hits = list(pool.map(lambda r: j.judge(r.claim.text, product), results))
        results = [_combine(r, h) for r, h in zip(results, hits)]
        log.info("AI judge finished %d claims in %.1fs (%d failed)", len(results), time.perf_counter() - t0,
                 sum(h is None for h in hits))
    claims = [r.to_dict(engine.rb.describe) for r in results]
    counts = Counter(c["verdict"] for c in claims)
    return {
        "summary": {v: counts.get(v, 0) for v in VERDICT_ORDER}
                   | {"claims": len(claims), "needs_review": sum(c["status"] == "NEEDS_REVIEW" for c in claims),
                      "ai": use_ai},
        "claims": claims,
        "not_assessed": extraction.dropped,
    }
