"""LLM judge: reads one claim plus the regulation passages found by vector search and returns a verdict with quotes.

Guardrails:
- temperature 0 / fixed seed and a strict JSON schema;
- every quoted excerpt must occur word for word in the named snapshot (RuleBank.verify) and in a passage that was
  actually shown to the model; quotes that fail are dropped;
- a RED or AMBER answer with no surviving quote becomes AMBER "AI-UNGROUNDED" (needs human review);
- results are cached by a hash of claim, regime, product, model, prompt and index, so reruns are identical.
The pipeline then keeps the worse of this verdict and the deterministic rule verdict.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from engine import REGIMES, Product
from models import Citation, RuleHit, Verdict
from retrieve import Index, Passage, embed_model
from rulebank import REPO_ROOT, CitationError, RuleBank, squash

log = logging.getLogger("claimcheck.judge")
CACHE_DIR = REPO_ROOT / "extracted" / "judge"
MIN_QUOTE = 15

PROMPT = """You are an Australian regulatory compliance reviewer for health product marketing claims.
Regime: {regime_label}.
Product: {product}.

Decide whether the CLAIM below may be made for this product, using ONLY the numbered regulation PASSAGES.
Verdicts:
- RED: not permitted as worded (e.g. refers to a serious disease or condition, a therapeutic/medicinal claim
  that the regime does not allow, a health claim with no permitted basis, misleading or prohibited representation).
- AMBER: may be permitted only if conditions are met, the wording is changed, evidence is held, or the passages
  do not settle it.
- GREEN: permitted as worded, or not a health/therapeutic claim at all (branding, flavour, pack description,
  general marketing language).

Rules:
- Quote the exact words from the passages that support your verdict: copy them character for character, a
  sentence or clause long, never paraphrase. Give the passage id you quoted from.
- For GREEN because it is not a health claim, citations may be empty.
- "matched" lists the exact words from the CLAIM that drove the verdict.
- "reason" is 1-3 plain-English sentences a marketing person can act on.

CLAIM:
{claim}

PASSAGES:
{passages}"""

SCHEMA = {
    "name": "judgement",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["RED", "AMBER", "GREEN"]},
            "reason": {"type": "string"},
            "matched": {"type": "array", "items": {"type": "string"}},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "passage_id": {"type": "string"},
                        "section": {"type": "string"},
                        "excerpt": {"type": "string"},
                    },
                    "required": ["passage_id", "section", "excerpt"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["verdict", "reason", "matched", "citations"],
        "additionalProperties": False,
    },
}


def judge_model() -> str:
    return os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4.1").split("#")[0].strip()


def enabled() -> bool:
    flag = os.environ.get("CLAIMCHECK_JUDGE", "").split("#")[0].strip().lower()
    if flag in ("off", "0", "false", "no"):
        return False
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _product_line(p: Product) -> str:
    return p.name if p.id != "adhoc" else "(not specified)"


class Judge:
    def __init__(self, rb: RuleBank):
        self.rb = rb
        self.index = Index(rb)

    def _cache_key(self, claim: str, product: Product) -> str:
        fp = json.dumps({
            "claim": claim, "regime": product.regime, "product": _product_line(product), "model": judge_model(),
            "embed": embed_model(), "prompt": PROMPT, "schema": SCHEMA,
            "snapshots": self.index._fingerprint()["snapshots"],
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(fp.encode("utf-8")).hexdigest()

    def _ask(self, claim: str, product: Product, passages: list[Passage]) -> dict:
        from openai import OpenAI

        prompt = PROMPT.format(
            regime_label=REGIMES[product.regime], product=_product_line(product), claim=claim,
            passages="\n\n".join(f"[{p.id}] ({p.source}, {p.locator})\n{p.text}" for p in passages),
        )
        resp = OpenAI(timeout=120).chat.completions.create(
            model=judge_model(), temperature=0, seed=0,
            response_format={"type": "json_schema", "json_schema": SCHEMA},
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(resp.choices[0].message.content or "{}")

    def judge(self, claim: str, product: Product) -> RuleHit | None:
        """Returns an AI RuleHit, or None if the judge failed (the rule verdict then stands alone)."""
        key = self._cache_key(claim, product)
        cache = CACHE_DIR / f"{key}.json"
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            answer, passages = data["answer"], [Passage(**p) for p in data["passages"]]
            log.debug("judge cache hit: %.60s", claim)
        else:
            t0 = time.perf_counter()
            try:
                passages = self.index.search(claim, product.regime)
                answer = self._ask(claim, product, passages)
            except Exception as e:  # network, auth, quota, bad JSON: fall back to rules only
                log.error("AI judge failed for %.60r: %s: %s", claim, type(e).__name__, e)
                return None
            log.info("AI judge %s in %.1fs: %.70s", answer.get("verdict"), time.perf_counter() - t0, claim)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({
                "claim": claim, "regime": product.regime, "model": judge_model(),
                "passages": [p.__dict__ for p in passages], "answer": answer,
            }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return self._to_hit(claim, answer, passages)

    def _to_hit(self, claim: str, answer: dict, passages: list[Passage]) -> RuleHit:
        by_id = {p.id: p for p in passages}
        citations, rejected = [], 0
        for c in answer.get("citations", []):
            p = by_id.get(c.get("passage_id"))
            excerpt = squash(c.get("excerpt", "")).strip(" .\"'“”")
            if not p or len(excerpt) < MIN_QUOTE or excerpt not in squash(p.text):
                rejected += 1
                continue
            section = squash(c.get("section", ""))
            cit = Citation(p.source, f"{section} (snapshot {p.locator})" if section else f"snapshot {p.locator}", excerpt)
            try:
                self.rb.verify(cit)
            except CitationError:
                rejected += 1
                continue
            if cit not in citations:
                citations.append(cit)
        if rejected:
            log.warning("AI judge: dropped %d unverifiable quote(s) for %.60r", rejected, claim)

        verdict = Verdict(answer.get("verdict", "AMBER"))
        low = claim.lower()
        matched = [m for m in answer.get("matched", []) if m and m.lower() in low]
        reason = squash(answer.get("reason", ""))
        if verdict is not Verdict.GREEN and not citations:
            return RuleHit("AI-UNGROUNDED", Verdict.AMBER, "AI assessment could not be tied to a quoted rule",
                           f"The AI reviewer said {verdict.value}: {reason} It did not quote the regulations word "
                           "for word, so this needs a human check.", [], matched, source="ai")
        title = {"RED": "AI: not permitted as worded", "AMBER": "AI: permitted only with changes or conditions",
                 "GREEN": "AI: permitted / not a health claim"}[verdict.value]
        return RuleHit("AI-JUDGE", verdict, title, reason, citations, matched, source="ai")
