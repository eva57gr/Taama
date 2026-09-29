"""Loads the rule bank: snapshots of the regulatory sources plus the YAML rules and health-concept lexicon.

Everything the engine cites comes through here, so this is also where citation integrity is enforced:
an excerpt is only accepted if it occurs verbatim (after whitespace normalisation) in the snapshot it names.
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

from models import Citation, Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]
RULEBANK_DIR = REPO_ROOT / "rulebank" / "au"
SNAPSHOT_DIR = RULEBANK_DIR / "snapshots"

EVIDENCE_TYPES = (
    "Scientific or Traditional",
    "Scientific",
    "Traditional",
    "Traditional Chinese medicine",
    "Traditional Ayurvedic medicine",
)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class CitationError(ValueError):
    pass


@dataclass(frozen=True)
class Indication:
    """One row of Schedule 1 of the TG (Permissible Indications) Determination."""

    part: int
    part_title: str
    item: int
    text: str
    evidence: str
    requirements: tuple[str, ...]

    @property
    def locator(self) -> str:
        return f"Schedule 1, Part {self.part}, item {self.item}"

    @property
    def traditional_only(self) -> bool:
        return self.evidence.startswith("Traditional")

    def citation(self) -> Citation:
        return Citation("tga_permissible_indications", self.locator, self.text)


@dataclass(frozen=True)
class GeneralLevelClaim:
    """One (property of food, specific health effect) row of FSC Schedule 4, section S4—5."""

    property: str
    effect: str
    population: str | None
    condition: str

    def citation(self) -> Citation:
        return Citation("fsc_schedule_4", f"S4—5, {self.property}", self.effect)


@dataclass(frozen=True)
class NotifiedRelationship:
    """A food-health relationship notified to FSANZ under Std 1.2.7—18(3)(b)."""

    source: str
    property: str
    effect: str
    business: str
    notified: str

    def citation(self) -> Citation:
        return Citation(self.source, f"{self.business}, notified {self.notified}", self.effect)


@dataclass(frozen=True)
class Rule:
    id: str
    verdict: Verdict
    title: str
    why: str
    citations: tuple[Citation, ...]


@dataclass
class Concept:
    """A health effect in plain words, and what can make a claim about it permissible in each regime."""

    id: str
    label: str
    patterns: list[re.Pattern]
    tga: list[dict] = field(default_factory=list)
    food_s4: list[dict] = field(default_factory=list)
    food_notified: list[str] = field(default_factory=list)


class RuleBank:
    def __init__(self, root: Path = RULEBANK_DIR):
        self.root = root
        self.snapshot_dir = root / "snapshots"

    # ---- snapshots -------------------------------------------------------------------------

    @cached_property
    def manifest(self) -> dict:
        return json.loads((self.snapshot_dir / "manifest.json").read_text(encoding="utf-8"))["sources"]

    @cached_property
    def _snapshot_text(self) -> dict[str, str]:
        return {
            key: (self.snapshot_dir / f"{key}.txt").read_text(encoding="utf-8") for key in self.manifest
        }

    @cached_property
    def _squashed(self) -> dict[str, str]:
        return {key: squash(text) for key, text in self._snapshot_text.items()}

    def verify(self, citation: Citation) -> None:
        if citation.source not in self._squashed:
            raise CitationError(f"unknown source {citation.source!r}")
        if squash(citation.excerpt) not in self._squashed[citation.source]:
            raise CitationError(
                f"excerpt not found verbatim in {citation.source}: {citation.excerpt[:80]!r}"
            )

    @cached_property
    def _line_index(self) -> dict[str, tuple[str, list[int], list[int]]]:
        """Per source: squashed text plus (offset, line number) pairs to map a match back to a line."""
        out = {}
        for key, text in self._snapshot_text.items():
            parts, offsets, numbers, pos = [], [], [], 0
            for n, line in enumerate(text.splitlines(), start=1):
                s = squash(line)
                if not s:
                    continue
                offsets.append(pos)
                numbers.append(n)
                parts.append(s)
                pos += len(s) + 1
            out[key] = (" ".join(parts), offsets, numbers)
        return out

    def line_of(self, citation: Citation) -> int | None:
        """1-based line in the snapshot where the (first occurrence of the) excerpt starts."""
        joined, offsets, numbers = self._line_index[citation.source]
        at = joined.find(squash(citation.excerpt))
        if at < 0:
            return None
        return numbers[bisect.bisect_right(offsets, at) - 1]

    def describe(self, citation: Citation) -> dict:
        self.verify(citation)
        meta = self.manifest[citation.source]
        doc = meta["title"]
        if meta.get("register_id"):
            doc += f" ({meta['register_id']}"
            doc += f", compilation {meta['compilation']})" if meta.get("compilation") else ")"
        return {
            "source": citation.source,
            "document": doc,
            "locator": citation.locator,
            "excerpt": citation.excerpt,
            "snapshot": f"rulebank/au/snapshots/{citation.source}.txt",
            "snapshot_line": self.line_of(citation),
            "url": meta.get("url"),
        }

    # ---- TG (Permissible Indications) Determination --------------------------------------------

    @cached_property
    def indications(self) -> list[Indication]:
        lines = [squash(l) for l in self._snapshot_text["tga_permissible_indications"].splitlines()]
        lines = [l for l in lines if l]
        start = next(i for i, l in enumerate(lines) if l.startswith("Note: See sections 5 and 6."))
        end = next(i for i, l in enumerate(lines) if l.startswith("Schedule 2—Repeals") and i > start)
        body = lines[start + 1 : end]

        out: list[Indication] = []
        part, part_title, expect, i = 0, "", 1, 0
        while i < len(body):
            line = body[i]
            m = re.fullmatch(r"Part (\d+)—(.+)", line)
            if m:
                part, part_title, expect = int(m.group(1)), m.group(2), 1
                i += 1
                continue
            is_item = (
                line == str(expect)
                and i + 2 < len(body)
                and body[i + 2] in EVIDENCE_TYPES
            )
            if not is_item:
                if out and out[-1].part == part and line not in {"Column 1", "Column 2", "Column 3", "Column 4", "Item", "Indication", "Type of evidence", "Requirements", part_title}:
                    last = out[-1]
                    out[-1] = Indication(last.part, last.part_title, last.item, last.text, last.evidence, last.requirements + (line,))
                i += 1
                continue
            out.append(Indication(part, part_title, expect, body[i + 1], body[i + 2], ()))
            expect += 1
            i += 3
        return out

    def indication(self, text: str) -> Indication:
        matches = [ind for ind in self.indications if ind.text == text]
        if len(matches) != 1:
            raise CitationError(f"expected exactly one permitted indication {text!r}, found {len(matches)}")
        return matches[0]

    # ---- FSC Schedule 4, S4—5 ------------------------------------------------------------------

    def general_level_claim(self, prop: str, effect: str) -> GeneralLevelClaim:
        """Finds `effect` in the S4—5 block for `prop` and returns it with the row's conditions."""
        lines = [squash(l) for l in self._snapshot_text["fsc_schedule_4"].splitlines()]
        lines = [l for l in lines if l]
        s45 = next(i for i, l in enumerate(lines) if l.startswith("S4—5 Conditions for permitted general level health claims"))
        s46 = next(i for i, l in enumerate(lines) if l.startswith("S4—6 Nutrient profiling scoring criterion"))
        block = lines[s45:s46]
        # Layout per property: name, first effect, condition ("The food ..."), remaining effects,
        # with a population line (e.g. "Children") straight after any effect it restricts.
        heads = [i for i in range(len(block) - 2) if block[i + 2].startswith("The food")]
        idx = [i for i in heads if block[i] == prop]
        if len(idx) != 1:
            raise CitationError(f"property {prop!r} not found exactly once in S4—5")
        p = idx[0]
        stop = next((h for h in heads if h > p), len(block))
        cond = block[p + 2]
        effects = [block[p + 1]] + block[p + 3 : stop]
        if effect not in effects:
            raise CitationError(f"{effect!r} is not an S4—5 health effect for {prop}")
        after = effects[effects.index(effect) + 1] if effects.index(effect) + 1 < len(effects) else None
        population = after if after in {"Children", "Adults", "Women", "Men"} else None
        return GeneralLevelClaim(prop, effect, population, cond)

    # ---- FSANZ notified relationships ------------------------------------------------------------

    def notified(self, source: str) -> list[NotifiedRelationship]:
        text = self._snapshot_text[source]
        records = [
            [squash(l) for l in chunk.splitlines() if l.strip()]
            for chunk in re.split(r"\n\s*\n", text)
        ]
        out = []
        # A record is five lines: property, health effect, business, address, dd/mm/yyyy.
        flat = [l for rec in records for l in rec]
        for i in range(len(flat) - 4):
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", flat[i + 4]) and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", flat[i]):
                out.append(NotifiedRelationship(source, flat[i], flat[i + 1], flat[i + 2], flat[i + 4]))
        return out

    # ---- YAML --------------------------------------------------------------------------------

    @cached_property
    def rules(self) -> dict[str, Rule]:
        data = yaml.safe_load((self.root / "rules.yaml").read_text(encoding="utf-8"))
        out = {}
        for rule_id, r in data["rules"].items():
            cites = tuple(Citation(c["source"], c["locator"], c["excerpt"]) for c in r.get("cite", []))
            for c in cites:
                self.verify(c)
            out[rule_id] = Rule(rule_id, Verdict(r["verdict"]), r["title"], squash(r["why"]), cites)
        return out

    @cached_property
    def concepts(self) -> dict[str, Concept]:
        data = yaml.safe_load((self.root / "concepts.yaml").read_text(encoding="utf-8"))
        out = {}
        for cid, c in data["concepts"].items():
            concept = Concept(
                id=cid,
                label=c["label"],
                patterns=[re.compile(p, re.I) for p in c["patterns"]],
                tga=c.get("tga", []),
                food_s4=c.get("food_s4", []),
                food_notified=c.get("food_notified", []),
            )
            for entry in concept.tga:
                words = set(re.findall(r"[a-z]+", self.indication(entry["indication"]).text.lower()))
                for phrase in entry.get("actions", []) + entry.get("targets", []) + entry.get("nouns", []):
                    stray = set(re.findall(r"[a-z]+", phrase.lower())) - words
                    # inflections the matcher adds itself ("calm" for "Calms", "adaptogens") are fine
                    if any(not any(w.startswith(s) or s.startswith(w) for w in words) for s in stray):
                        raise CitationError(f"{cid}: {phrase!r} is not wording of {entry['indication']!r}")
            for entry in concept.food_s4:
                self.general_level_claim(entry["property"], entry["effect"])
            notified_effects = {
                n.effect for key in self.manifest if key.startswith("fsanz_notified_") for n in self.notified(key)
            }
            for effect in concept.food_notified:
                if effect not in notified_effects:
                    raise CitationError(f"{cid}: {effect!r} is not in any notified-relationships snapshot")
            out[cid] = concept
        return out

    @cached_property
    def lexicon(self) -> dict:
        return yaml.safe_load((self.root / "lexicon.yaml").read_text(encoding="utf-8"))
