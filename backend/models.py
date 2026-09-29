from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable


class Verdict(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"

    @property
    def severity(self) -> int:
        return {"GREEN": 0, "AMBER": 1, "RED": 2}[self.value]


def most_severe(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: v.severity)


@dataclass(frozen=True)
class Citation:
    source: str  # snapshot key in rulebank/au/snapshots/manifest.json
    locator: str  # human-readable section / clause / table row
    excerpt: str  # verbatim text from the source snapshot


@dataclass(frozen=True)
class Provenance:
    file: str  # path relative to the repo root, or "<text>" for raw text input
    location: str  # page / block / line information
    method: str  # "text", "pdf-text-layer", "ocr"


@dataclass
class Block:
    """A contiguous piece of text as laid out in the input (a paragraph, a bullet, a panel)."""

    text: str
    provenance: Provenance
    box: tuple[float, float, float, float] | None = None  # image pixels (x0, y0, x1, y1), OCR only
    confidence: float | None = None  # unused for vision OCR blocks; kept for PDF-derived layout


@dataclass
class Claim:
    id: str
    text: str
    provenance: list[Provenance]


@dataclass
class Condition:
    text: str
    status: str  # "met", "not_met", "unverified"
    citation: Citation | None = None
    evidence: str = ""


@dataclass
class RuleHit:
    rule_id: str
    verdict: Verdict
    title: str
    justification: str
    citations: list[Citation]
    matched: list[str] = field(default_factory=list)  # the words in the claim that triggered the rule
    conditions: list[Condition] = field(default_factory=list)
    source: str = "rules"  # "rules" (deterministic engine) or "ai" (LLM judge over retrieved passages)


@dataclass
class ClaimResult:
    claim: Claim
    claim_type: str
    verdict: Verdict
    status: str  # "PASS", "CONDITIONS", "NEEDS_REVIEW" or "FAIL"
    justification: str
    hits: list[RuleHit]

    @property
    def decided_by(self) -> str:
        top = {h.source for h in self.hits if h.verdict is self.verdict}
        return "both" if len(top) > 1 else (top.pop() if top else "rules")

    def to_dict(self, describe: Callable[[Citation], dict] = asdict) -> dict:
        return {
            "id": self.claim.id,
            "text": self.claim.text,
            "claim_type": self.claim_type,
            "verdict": self.verdict.value,
            "decided_by": self.decided_by,
            "status": self.status,
            "justification": self.justification,
            "rules": [
                {
                    "rule_id": h.rule_id,
                    "source": h.source,
                    "verdict": h.verdict.value,
                    "title": h.title,
                    "why": h.justification,
                    "matched": h.matched,
                    "citations": [describe(c) for c in h.citations],
                    "conditions": [
                        {
                            "text": c.text,
                            "status": c.status,
                            "evidence": c.evidence,
                            "citation": describe(c.citation) if c.citation else None,
                        }
                        for c in h.conditions
                    ],
                }
                for h in self.hits
            ],
            "found_in": [asdict(p) for p in self.claim.provenance],
        }
