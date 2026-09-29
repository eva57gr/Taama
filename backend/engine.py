"""Deterministic verdict engine: claim text + product profile -> ClaimResult.

No model calls and no randomness: the same claim, profile and rule bank always give the same result.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

from models import Citation, Claim, ClaimResult, Condition, RuleHit, Verdict, most_severe
from rulebank import (
    REPO_ROOT,
    Concept,
    GeneralLevelClaim,
    Indication,
    NotifiedRelationship,
    RuleBank,
    squash,
)

PRODUCTS_DIR = Path(os.environ.get("CLAIMCHECK_PRODUCTS_DIR") or REPO_ROOT / "products")
PRODUCT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

REGIMES = {"tga_listed": "TGA listed medicine (AUST L)", "fsanz_food": "Food (FSANZ Food Standards Code)"}

# AMBER hits that need a human judgement call (as opposed to "fine if the stated condition is met").
REVIEW_RULES = {"TGA-IND-VARIED", "NEEDS-REVIEW", "FOOD-HC-UNAUTHORISED", "FOOD-COMPARATIVE", "FOOD-NZ-CATEGORY",
                "AI-UNGROUNDED"}

UNIT_MG = {"mg": 1.0, "g": 1000.0, "mcg": 0.001, "µg": 0.001, "ug": 0.001}


# ---------------------------------------------------------------------------------------- products


@dataclass
class Product:
    id: str
    name: str
    regime: str
    regime_basis: dict
    inputs: list[str]
    mask: list[str]
    record: dict
    record_source: str
    serving: dict | None = None
    liquid: bool = False
    properties: dict[str, list[str]] = field(default_factory=dict)
    business: str | None = None
    notified_source: str | None = None
    npsc: bool | None = None
    review_notes: list[dict] = field(default_factory=list)

    def regime_citation(self) -> Citation:
        c = self.regime_basis["cite"]
        return Citation(c["source"], c["locator"], c["excerpt"])


def product_path(product_id: str) -> Path:
    if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
        raise ValueError(f"invalid product id {product_id!r} (lower-case letters, digits, '-' and '_')")
    return PRODUCTS_DIR / f"{product_id}.yaml"


def list_products() -> list[Product]:
    return [load_product(p.stem) for p in sorted(PRODUCTS_DIR.glob("*.yaml")) if PRODUCT_ID.fullmatch(p.stem)]


def load_product(product_id: str, path: Path | None = None) -> Product:
    path = path or product_path(product_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown product {product_id!r}")
    try:
        return product_from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


def _amount(v, where: str) -> list:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], (int, float))
            and not isinstance(v[0], bool) and isinstance(v[1], str)):
        raise ValueError(f"{where} must be [number, unit]")
    return [v[0], v[1]]


def product_from_dict(d: dict) -> Product:
    """Builds and validates a profile (from YAML or an API payload)."""
    if not isinstance(d, dict):
        raise ValueError("product profile must be a mapping")
    pid, name, regime = d.get("id"), d.get("name"), d.get("regime")
    product_path(pid)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}; expected one of {sorted(REGIMES)}")
    basis = d.get("regime_basis") or {}
    if not isinstance(basis, dict):
        raise ValueError("regime_basis must be a mapping")
    basis = {"reason": str(basis.get("reason") or "regime declared by the user"), "cite": basis.get("cite")}
    mask = d.get("mask") or []
    if not isinstance(mask, list) or not all(isinstance(m, str) for m in mask):
        raise ValueError("mask must be a list of strings")
    record = {}
    for key, rec in (d.get("record") or {}).items():
        if not isinstance(rec, dict):
            raise ValueError(f"record[{key!r}] must be a mapping")
        clean = {}
        for f in ("per_serving", "per_100"):
            if rec.get(f) is not None:
                clean[f] = _amount(rec[f], f"record[{key!r}].{f}")
        if rec.get("rdi_pct") is not None:
            if not isinstance(rec["rdi_pct"], (int, float)) or isinstance(rec["rdi_pct"], bool):
                raise ValueError(f"record[{key!r}].rdi_pct must be a number")
            clean["rdi_pct"] = rec["rdi_pct"]
        record[str(key).lower()] = clean
    properties = {}
    for key, pats in (d.get("properties") or {}).items():
        if not isinstance(pats, list) or not all(isinstance(p, str) and len(p) <= 200 for p in pats):
            raise ValueError(f"properties[{key!r}] must be a list of patterns (max 200 chars each)")
        for p in pats:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"properties[{key!r}]: bad pattern {p!r}: {e}") from e
        properties[str(key).lower()] = pats
    npsc = d.get("npsc")
    if npsc not in (True, False, None):
        raise ValueError("npsc must be true, false or null")
    return Product(
        id=pid,
        name=name.strip(),
        regime=regime,
        regime_basis=basis,
        inputs=d.get("inputs") or [],
        mask=mask,
        record=record,
        record_source=str(d.get("record_source") or ""),
        serving=d.get("serving"),
        liquid=bool(d.get("liquid", False)),
        properties=properties,
        business=d.get("business") or None,
        notified_source=d.get("notified_source") or None,
        npsc=npsc,
        review_notes=d.get("review_notes") or [],
    )


def product_to_dict(p: Product) -> dict:
    return {
        "id": p.id, "name": p.name, "market": "AU", "regime": p.regime, "regime_basis": p.regime_basis,
        "inputs": p.inputs, "mask": p.mask, "business": p.business, "notified_source": p.notified_source,
        "record_source": p.record_source, "serving": p.serving, "liquid": p.liquid, "record": p.record,
        "properties": p.properties, "npsc": p.npsc, "review_notes": p.review_notes,
    }


def save_product(p: Product) -> Path:
    path = product_path(p.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(product_to_dict(p), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def delete_product(product_id: str) -> None:
    path = product_path(product_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown product {product_id!r}")
    path.unlink()


# ------------------------------------------------------------------------------------------- text


def fold(text: str) -> str:
    """Unicode-folds claim text: no trademark signs, accents or typographic quotes/dashes."""
    t = re.sub(r"[™®©]", "", text)
    t = t.replace("¯ ", "").replace("¯", "")  # "Ma¯ nuka" artefact in the Comvita artwork
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for a, b in {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-"}.items():
        t = t.replace(a, b)
    return squash(t)


def _prep(text: str) -> str:
    t = fold(text).lower().replace("&", " and ")
    t = re.sub(r"\s*\+\s*", " + ", t)  # "Energy+Focus" and "Energy + Focus" mask alike
    return squash(t)


def matching_text(text: str, product: Product) -> str:
    """Lower-cased, folded claim text with product/brand/blend names replaced by "[name]"."""
    t = re.sub(r"\[[^\]]*\]", " [name] ", _prep(text))
    for term in sorted((_prep(x) for x in product.mask), key=len, reverse=True):
        t = re.sub(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", "[name]", t)
    return squash(t)


def _inflections(verb: str) -> list[str]:
    v = verb.lower()
    forms = {v, v + "s", v + "ed", v + "ing"}
    if v.endswith("e"):
        forms |= {v + "d", v[:-1] + "ing"}
    return sorted(forms, key=len, reverse=True)


PREFIX = r"(?:(?:helps?|may help|can help)(?: to)? )?"
QUALIFIER = (
    r"(?:(?:your |the |a |their )?(?:kids?|children|child|adults?|women|men|teens?|toddlers?|little ones|babies|baby)"
    r"(?:'s|s'|')? )?"
)


def _phrase(words: str) -> str:
    return r"\s+".join(re.escape(w) for w in words.lower().split())


@dataclass
class ConceptHit:
    concept: Concept
    start: int
    end: int
    text: str


@dataclass
class ExactMatch:
    indication: Indication
    start: int
    end: int
    text: str
    via: str  # "wording" | "linking word" | "term"


@dataclass
class Amount:
    key: str
    value_mg: float
    shown: str
    basis: str  # "per_serving" | "per_100"


# ----------------------------------------------------------------------------------------- engine


class Engine:
    def __init__(self, rulebank: RuleBank | None = None):
        self.rb = rulebank or RuleBank()

    # ---- compiled lexicon ------------------------------------------------------------------------

    @cached_property
    def lex(self) -> dict[str, list[re.Pattern]]:
        out = {}
        for key, value in self.rb.lexicon.items():
            if isinstance(value, list):
                out[key] = [re.compile(p, re.I) for p in value]
        return out

    @cached_property
    def lex_groups(self) -> dict[str, dict[str, list[re.Pattern]]]:
        out = {}
        for key, value in self.rb.lexicon.items():
            if isinstance(value, dict):
                out[key] = {k: [re.compile(p, re.I) for p in v] for k, v in value.items()}
        return out

    @cached_property
    def exact_patterns(self) -> list[tuple[re.Pattern, Indication, str]]:
        out, seen = [], set()
        for concept in self.rb.concepts.values():
            for entry in concept.tga:
                ind = self.rb.indication(entry["indication"])
                for target in entry.get("targets", []):
                    actions = [f for a in entry.get("actions", []) for f in _inflections(a)]
                    if actions:
                        p = rf"\b{PREFIX}(?:{'|'.join(map(re.escape, actions))})\s+{QUALIFIER}{_phrase(target)}\b"
                        out.append((p, ind, "wording"))
                    if len(target.split()) >= 2:
                        out.append((rf"\bfor\s+{QUALIFIER}{_phrase(target)}\b", ind, "linking word"))
                for noun in entry.get("nouns", []):
                    out.append((rf"\b{_phrase(noun)}\b", ind, "term"))
        compiled = []
        for p, ind, via in out:
            if (p, ind.text) not in seen:
                seen.add((p, ind.text))
                compiled.append((re.compile(p, re.I), ind, via))
        return compiled

    def _any(self, key: str, m: str) -> list[str]:
        return [mo.group(0) for p in self.lex.get(key, []) for mo in p.finditer(m)]

    def _grouped(self, key: str, m: str) -> list[tuple[str, str]]:
        return [
            (label, mo.group(0))
            for label, pats in self.lex_groups[key].items()
            for p in pats
            for mo in p.finditer(m)
        ]

    def _rule(self, rule_id: str, matched: list[str] | None = None, extra: list[Citation] | None = None,
              conditions: list[Condition] | None = None, **fmt) -> RuleHit:
        rule = self.rb.rules[rule_id]
        matched = list(dict.fromkeys(matched or []))
        fmt.setdefault("matched", ", ".join(f'"{x}"' for x in matched))
        cites = list(dict.fromkeys(list(rule.citations) + list(extra or [])))
        for c in cites:
            self.rb.verify(c)
        return RuleHit(rule_id, rule.verdict, rule.title, rule.why.format(**fmt), cites, matched, conditions or [])

    # ---- detection -------------------------------------------------------------------------------

    def concept_hits(self, m: str) -> list[ConceptHit]:
        hits = [
            ConceptHit(c, mo.start(), mo.end(), mo.group(0))
            for c in self.rb.concepts.values()
            for p in c.patterns
            for mo in p.finditer(m)
        ]
        keep = []
        for h in hits:
            inside_longer = any(
                o.concept.id != h.concept.id and o.start <= h.start and h.end <= o.end and (o.end - o.start) > (h.end - h.start)
                for o in hits
            )
            if not inside_longer and not any(k.concept.id == h.concept.id and (k.start, k.end) == (h.start, h.end) for k in keep):
                keep.append(h)
        return sorted(keep, key=lambda h: (h.start, h.concept.id))

    def exact_matches(self, m: str) -> list[ExactMatch]:
        out = []
        for pattern, ind, via in self.exact_patterns:
            for mo in pattern.finditer(m):
                out.append(ExactMatch(ind, mo.start(), mo.end(), mo.group(0), via))
        return sorted(out, key=lambda e: (e.start, e.indication.part, e.indication.item))

    def _names(self, key: str, product: Product) -> list[str]:
        key = key.lower()
        if key in product.properties:
            return product.properties[key]
        vit = self.rb.lexicon["vitamins_minerals"]
        if key in vit:
            return vit[key]
        if key == "sugars":
            return [r"\bsugars?\b"]
        return [r"\b" + re.escape(key) + r"\b"]

    def mentions(self, m: str, product: Product, key: str) -> bool:
        return any(re.search(p, m, re.I) for p in self._names(key, product))

    def stated_amounts(self, m: str, product: Product) -> list[Amount]:
        out = []
        basis = "per_100" if re.search(r"\bper 100 ?(?:ml|g)\b", m) else "per_serving"
        for key in product.record:
            name = "(?:" + "|".join(self._names(key, product)) + ")"
            for p in (
                rf"(\d+(?:\.\d+)?)\s?(mg|mcg|µg|g)\b(?:\s+(?:of|total))?\s+(?:\w+\s+){{0,1}}?{name}",
                rf"{name}\s*(?::|-|\()?\s*(\d+(?:\.\d+)?)\s?(mg|mcg|µg|g)\b",
            ):
                for mo in re.finditer(p, m):
                    value, unit = float(mo.group(1)), mo.group(2)
                    out.append(Amount(key, value * UNIT_MG[unit], f"{mo.group(1)} {unit} {key}", basis))
        return out

    # ---- entry point -----------------------------------------------------------------------------

    def check(self, claim: Claim, product: Product) -> ClaimResult:
        m = matching_text(claim.text, product)
        concept_hits = self.concept_hits(m)
        if product.regime == "tga_listed":
            hits = self._tga(m, product, concept_hits)
        else:
            hits = self._food(m, product, concept_hits)
        hits += self._amount_checks(m, product)

        # A benefit verb with health wording that maps to no concept stays open, even if another rule (e.g. a
        # nutrient content check) already passed: the unmapped part may be a health claim.
        if not concept_hits and self._any("health_adjacent", m) and self._any("benefit_verbs", m):
            hits.append(self._rule("NEEDS-REVIEW", self._any("health_adjacent", m) + self._any("benefit_verbs", m)))
        if not hits:
            kind = self._kind_sentence(self.claim_type(m, concept_hits, []))
            rule = "TGA-NO-CLAIM" if product.regime == "tga_listed" else "FOOD-NO-CLAIM"
            hits.append(self._rule(rule, kind=kind))

        verdict = most_severe([h.verdict for h in hits])
        return ClaimResult(
            claim=claim,
            claim_type=self.claim_type(m, concept_hits, hits),
            verdict=verdict,
            status=self._status(verdict, hits),
            justification=self._justify(verdict, hits),
            hits=hits,
        )

    # ---- TGA listed medicines ---------------------------------------------------------------------

    def _tga(self, m: str, product: Product, concept_hits: list[ConceptHit]) -> list[RuleHit]:
        hits: list[RuleHit] = []
        health_context = bool(concept_hits) or bool(self._any("health_adjacent", m))

        serious = self._serious(m)
        if serious:
            hits.append(self._rule("TGA-SERIOUS", serious))

        exact = self.exact_matches(m)
        has_trad_qualifier = bool(self._any("traditional_qualifier", m))
        research = self._any("research", m)

        used: dict[tuple[int, int], ExactMatch] = {}
        by_concept: dict[str, list[ConceptHit]] = {}
        for h in concept_hits:
            by_concept.setdefault(h.concept.id, []).append(h)

        for cid, chs in by_concept.items():
            concept = chs[0].concept
            covering = [[e for e in exact if e.start < h.end and h.start < e.end] for h in chs]
            if all(covering):
                for cov in covering:
                    # one indication per matched phrase: the first in Schedule order
                    e = cov[0]
                    if not any((u.start, u.end) == (e.start, e.end) for u in used.values()):
                        used[(e.indication.part, e.indication.item)] = e
                continue
            loose = [h.text for h, cov in zip(chs, covering) if not cov]
            if not concept.tga:
                hits.append(self._rule("TGA-IND-UNCOVERED", loose, concept=concept.label))
                continue
            closest = [self.rb.indication(e["indication"]) for e in concept.tga]
            closest = list(dict.fromkeys(closest))
            if all(i.traditional_only for i in closest) and not has_trad_qualifier:
                hits.append(self._rule(
                    "TGA-TRAD-QUALIFIER", loose, [i.citation() for i in closest],
                    closest=self._list_indications(closest),
                    conflict=self._conflict(research),
                ))
            else:
                hits.append(self._rule(
                    "TGA-IND-VARIED", loose, [i.citation() for i in closest],
                    concept=concept.label, closest=self._list_indications(closest),
                ))

        for e in sorted(used.values(), key=lambda e: e.start):
            ind = e.indication
            if ind.traditional_only and not has_trad_qualifier:
                hits.append(self._rule(
                    "TGA-TRAD-QUALIFIER", [e.text], [ind.citation()],
                    closest=self._list_indications([ind]), conflict=self._conflict(research),
                ))
            else:
                via_note = (
                    " The indication's action verb is replaced only by the linking word \"for\", which keeps its meaning."
                    if e.via == "linking word" else ""
                )
                hits.append(self._rule(
                    "TGA-IND-EXACT", [e.text], [ind.citation()],
                    indication=ind.text, locator=ind.locator, via_note=via_note,
                ))
            hits += self._requirements(m, ind, e)

        prevention = self._any("prevention", m)
        permitted_protect = any(re.search(r"\bprotect", e.text) for e in used.values())
        if prevention and health_context and not permitted_protect:
            hits.append(self._rule("TGA-PREVENTION", prevention))

        for key, rule in (("safety", "TGA-SAFETY"), ("guarantee", "TGA-GUARANTEE"),
                          ("substantiate", "TGA-SUBSTANTIATE"), ("research", "TGA-RESEARCH"),
                          ("excessive_use", "TGA-EXCESSIVE-USE")):
            found = self._any(key, m)
            if found:
                hits.append(self._rule(rule, found))
        return hits

    def _requirements(self, m: str, ind: Indication, e: ExactMatch) -> list[RuleHit]:
        out, unverified = [], []
        for req in ind.requirements:
            cite = Citation("tga_permissible_indications", f"{ind.locator}, column 4", req)
            low = req.lower()
            if "mental illnesses, disorders or conditions" in low:
                bad = self._any("mental_illness", m)
            elif "chronic fatigue syndrome" in low:
                bad = re.findall(r"\bchronic fatigue\b", m)
            else:
                unverified.append((req, cite))
                continue
            if bad:
                out.append(self._rule("TGA-REQUIREMENT-BREACH", bad, [cite], requirements=f'"{req}"'))
        if unverified:
            out.append(self._rule(
                "TGA-REQUIREMENT", [e.text], [c for _, c in unverified],
                conditions=[Condition(r, "unverified", c) for r, c in unverified],
                requirements="; ".join(f'"{r}"' for r, _ in unverified),
            ))
        return out

    @staticmethod
    def _list_indications(inds: list[Indication]) -> str:
        return "; ".join(f'"{i.text}" ({i.locator}, {i.evidence} evidence)' for i in inds)

    @staticmethod
    def _conflict(research: list[str]) -> str:
        if research:
            return f'; the claim instead asserts scientific proof ("{research[0]}"), an evidence type this indication does not allow'
        return ""

    def _serious(self, m: str) -> list[str]:
        found = self._any("serious", m)
        # "a (tasty/sweet) treat" is not a treatment claim
        return [f for f in found if not (f.startswith("treat") and re.search(r"\b(?:a|tasty|sweet|little) treat\b", m))]

    # ---- FSANZ foods --------------------------------------------------------------------------------

    def _food(self, m: str, product: Product, concept_hits: list[ConceptHit]) -> list[RuleHit]:
        hits: list[RuleHit] = []
        serious = self._serious(m)
        if serious:
            hits.append(self._rule("FOOD-THERAPEUTIC", serious))

        hits += self._food_health(m, product, concept_hits)
        hits += self._food_ncc(m, product)

        comparative = self._any("food_comparative", m)
        if comparative:
            hits.append(self._rule("FOOD-COMPARATIVE", comparative))
        nz = self._any("nz_only_category", m)
        if nz:
            hits.append(self._rule("FOOD-NZ-CATEGORY", nz))
        subst = self._grouped("food_substantiate", m)
        if subst:
            kinds = list(dict.fromkeys(k for k, _ in subst))
            hits.append(self._rule("FOOD-SUBSTANTIATE", [x for _, x in subst], kind=", ".join(kinds)))
        return hits

    def _food_health(self, m: str, product: Product, concept_hits: list[ConceptHit]) -> list[RuleHit]:
        hits: list[RuleHit] = []
        by_concept: dict[str, list[ConceptHit]] = {}
        for h in concept_hits:
            by_concept.setdefault(h.concept.id, []).append(h)

        notified = self.rb.notified(product.notified_source) if product.notified_source else []
        based: list[tuple[Concept, list[ConceptHit], list]] = []
        for cid, chs in by_concept.items():
            c = chs[0].concept
            bases: list = []
            for e in c.food_s4:
                if e["property"].lower() in product.record:
                    bases.append(self.rb.general_level_claim(e["property"], e["effect"]))
            for n in notified:
                if n.effect in c.food_notified and n.business == product.business:
                    bases.append(n)
            if bases:
                based.append((c, chs, bases))
            else:
                hits.append(self._rule("FOOD-HC-UNAUTHORISED", [h.text for h in chs], concept=c.label))
        if not based:
            return hits

        conds: list[Condition] = []
        for c, chs, bases in based:
            props = sorted({b.property for b in bases})
            stated = any(self.mentions(m, product, p) for p in props)
            conds.append(Condition(
                f"the claim names the property of food behind the {c.label} effect ({' or '.join(props)})",
                "met" if stated else "not_met",
                Citation("fsc_std_1_2_7", "1.2.7—20(2)(a)", "state the food or the *property of food and the specific health effect"),
                "" if stated else "not named in this statement; a split claim must point to where the full claim appears (1.2.7—21)",
            ))
        diet = self._any("dietary_context", m)
        conds.append(Condition(
            "a dietary context statement accompanies the claim",
            "met" if diet else "not_met",
            Citation("fsc_std_1_2_7", "1.2.7—20(6)(a)", "state that the *health effect must be considered in the context of a healthy diet involving the consumption of a variety of foods"),
            f'"{diet[0]}"' if diet else "none in this statement (only small packages are exempt, 1.2.7—20(4))",
        ))
        conds.append(Condition(
            "the food meets the Nutrient Profiling Scoring Criterion",
            {True: "met", False: "not_met", None: "unverified"}[product.npsc],
            Citation("fsc_std_1_2_7", "1.2.7—18(1)(a)", "the food to which the health claim relates *meets the NPSC"),
            "no NPSC calculation in the product record" if product.npsc is None else "",
        ))
        for b in sorted({b for _, _, bs in based for b in bs if isinstance(b, GeneralLevelClaim)}, key=lambda b: (b.property, b.effect)):
            rec = product.record.get(b.property.lower(), {})
            rdi = rec.get("rdi_pct")
            ok = rdi is not None and rdi >= 10
            cond_text = f"{b.property}: {b.condition}"
            if not any(x.text == cond_text for x in conds):
                conds.append(Condition(
                    cond_text, "met" if ok else ("unverified" if rdi is None else "not_met"),
                    Citation("fsc_schedule_4", f"S4—5, {b.property}, column 5", b.condition),
                    f"{rdi}% RDI per serving in {product.record_source} (S4—3 needs at least 10%)" if rdi is not None else "",
                ))
            if b.population and not re.search(r"\b(?:kids?|children|child)\b", m):
                conds.append(Condition(
                    f'the claim states the relevant population ("{b.population}") for "{b.effect}"', "not_met",
                    Citation("fsc_std_1_2_7", "1.2.7—20(1)(b)", "include a statement of that population group in conjunction with the health claim"),
                ))

        basis = self._basis_text([b for _, _, bs in based for b in bs])
        extra = list(dict.fromkeys(b.citation() for _, _, bs in based for b in bs))
        matched = [h.text for _, chs, _ in based for h in chs]
        if all(c.status == "met" for c in conds):
            hits.append(self._rule("FOOD-HC-PERMITTED", matched, extra, conds, basis=basis))
        else:
            open_ = "; ".join(f"{c.text} [{c.status.replace('_', ' ')}]" for c in conds if c.status != "met")
            hits.append(self._rule("FOOD-HC-CONDITIONS", matched, extra, conds, basis=basis, open_conditions=open_))
        return hits

    @staticmethod
    def _basis_text(bases: list) -> str:
        s4 = [b for b in dict.fromkeys(bases) if isinstance(b, GeneralLevelClaim)]
        nt = [b for b in dict.fromkeys(bases) if isinstance(b, NotifiedRelationship)]
        parts = []
        if s4:
            parts.append("Schedule 4 S4—5: " + "; ".join(f'{b.property} "{b.effect}"' for b in s4))
        if nt:
            parts.append(f"relationships notified to FSANZ by {nt[0].business}: " + "; ".join(f'{b.property} "{b.effect}"' for b in nt))
        return " and ".join(parts)

    def _food_ncc(self, m: str, product: Product) -> list[RuleHit]:
        hits: list[RuleHit] = []
        high = self._any("vitmin_high_descriptor", m)
        presence = self._any("presence", m)
        free = self._any("free_from", m)

        for vit, pats in self.rb.lexicon["vitamins_minerals"].items():
            names = [mo.group(0) for p in pats for mo in re.finditer(p, m)]
            if not names:
                continue
            pct = re.search(r"(\d+(?:\.\d+)?)\s?% ?rdi", m)
            if not (pct or high or presence or re.search(r"\d\s?(?:mg|mcg|µg)\b", m)):
                continue
            rec = product.record.get(vit)
            if not rec or rec.get("rdi_pct") is None:
                hits.append(self._rule("FOOD-NCC-UNVERIFIED", names, detail=(
                    f'"{names[0]}" is a nutrition content claim about {vit}, but the product record has no %RDI for it, '
                    f"so the S4—3 minimum (10% RDI per serving) cannot be checked.")))
                continue
            rdi = rec["rdi_pct"]
            need, label = (25, "Good source") if high else (10, "general claim condition")
            cite = Citation("fsc_schedule_4", "S4—3, Vitamin or mineral",
                            "A serving of the food contains no less than 25% *RDI or *ESADDI for that vitamin or mineral." if high
                            else "a serving of the food contains at least 10% *RDI or *ESADDI for that vitamin or mineral")
            words = f'"{high[0]}" ({label} descriptor)' if high else "the claim"
            if rdi >= need:
                hits.append(self._rule("FOOD-NCC-MET", names, [cite], detail=(
                    f"Nutrition content claim about {vit}: {words} requires at least {need}% RDI per serving; "
                    f"the product has {rdi}% ({product.record_source}).")))
            else:
                hits.append(self._rule("FOOD-NCC-UNMET", names, [cite], detail=(
                    f"Nutrition content claim about {vit}: {words} requires at least {need}% RDI per serving, "
                    f"but the product has only {rdi}% ({product.record_source}).")))
            if pct and abs(float(pct.group(1)) - rdi) > 0.5:
                hits.append(self._rule(
                    "FOOD-AMOUNT-MISMATCH", [pct.group(0)], claimed=f"{pct.group(1)}% RDI {vit}",
                    recorded=f"{rdi}% RDI", record_source=product.record_source))
            if self._any("vitmin_compare", m):
                hits.append(self._rule("FOOD-VITMIN-COMPARE", self._any("vitmin_compare", m) + names))

        if self.mentions(m, product, "sugars"):
            low = re.search(r"\blow\b(?:\s+\w+)?\s+sugars?\b|\bsugars?[- ]free\b", m)
            amount = re.search(r"\d+(?:\.\d+)?\s?g\b(?:\s+of)?\s+(?:\w+\s+)?sugars?\b|\bsugars?\s*(?::|-)?\s*\d+(?:\.\d+)?\s?g\b", m)
            if low:
                per100 = (product.record.get("sugars") or {}).get("per_100")
                limit = 2.5 if product.liquid else 5.0
                cite = Citation("fsc_schedule_4", "S4—3, Sugar or sugars, Low",
                                "(a) 2.5 g/100 mL for liquid food; or" if product.liquid else "(b) 5 g/100 g for solid food.")
                if per100 is None:
                    hits.append(self._rule("FOOD-NCC-UNVERIFIED", [low.group(0)], [cite],
                                           detail=f'"{low.group(0)}" needs sugars per 100 {"mL" if product.liquid else "g"}, which the product record lacks.'))
                else:
                    ok = per100[0] <= limit
                    hits.append(self._rule("FOOD-NCC-MET" if ok else "FOOD-NCC-UNMET", [low.group(0)], [cite], detail=(
                        f'"{low.group(0)}" requires no more than {limit} g sugars per 100 {"mL" if product.liquid else "g"}; '
                        f"the product has {per100[0]} {per100[1]} ({product.record_source}).")))
            elif amount:
                hits.append(self._rule("FOOD-NCC-MET", [amount.group(0)], [Citation(
                    "fsc_std_1_2_7", "1.2.7—12(8)",
                    "any descriptor that is not mentioned in Column 3 of the nutrition content claims table, including a descriptor expressed as a number or in numeric form, may be used in conjunction with a *property of food that is mentioned in Column 1 of the table.")],
                    detail=f'"{amount.group(0)}" states the sugar content as a number, which Standard 1.2.7 allows for a property in the S4—3 table (the stated amount is checked against the NIP separately).'))

        for prop in product.properties:
            names = [mo.group(0) for p in self._names(prop, product) for mo in re.finditer(p, m)]
            if not names:
                continue
            name_re = "(?:" + "|".join(self._names(prop, product)) + ")"
            descriptor = re.search(rf"\b(?:high|rich|packed|loaded|boosted|source)(?: in| with| levels? of| of)?\s+(?:\w+\s+){{0,2}}?{name_re}", m)
            freed = re.search(rf"{name_re}[- ]?free\b|\bno (?:added )?{name_re}|\b(?:zero|0 ?mg) {name_re}", m)
            amounted = re.search(rf"\d+(?:\.\d+)?\s?(?:mg|mcg|µg|g)\b(?:\s+of)?\s+{name_re}|{name_re}\s*\(?\d", m)
            present = re.search(rf"\b(?:with|contains?|containing|has|have|made with|including)\s+(?:\w+\s+){{0,4}}?{name_re}", m)
            if descriptor and not freed:
                hits.append(self._rule("FOOD-NCC-OTHER-DESCRIPTOR", [descriptor.group(0)], property=prop))
            elif freed:
                rec = (product.record.get(prop) or {}).get("per_serving")
                if rec is not None and rec[0] > 0:
                    hits.append(self._rule("FOOD-NCC-UNMET", [freed.group(0)], detail=(
                        f'"{freed.group(0)}" says the food does not contain {prop}, but the product record shows '
                        f"{rec[0]} {rec[1]} per serving ({product.record_source}).")))
                else:
                    detail = f" The product record shows {rec[0]} {rec[1]} per serving." if rec is not None else " Absence should be confirmed by testing."
                    hits.append(self._rule("FOOD-NCC-OTHER", [freed.group(0)], property=prop, detail=detail))
            elif amounted or present:
                hits.append(self._rule("FOOD-NCC-OTHER", [(amounted or present).group(0)], property=prop, detail=""))
        return hits

    # ---- both regimes ---------------------------------------------------------------------------

    def _amount_checks(self, m: str, product: Product) -> list[RuleHit]:
        hits = []
        rule = "TGA-AMOUNT-MISMATCH" if product.regime == "tga_listed" else "FOOD-AMOUNT-MISMATCH"
        for a in self.stated_amounts(m, product):
            rec = (product.record.get(a.key) or {}).get(a.basis)
            if not rec:
                continue
            rec_mg = rec[0] * UNIT_MG[rec[1]]
            if abs(rec_mg - a.value_mg) > 1e-6 * max(1.0, rec_mg):
                where = "per serving" if a.basis == "per_serving" else "per 100"
                hits.append(self._rule(rule, [a.shown], claimed=f'"{a.shown}"',
                                       recorded=f"{rec[0]} {rec[1]} {where}", record_source=product.record_source))
        return hits

    def claim_type(self, m: str, concept_hits: list[ConceptHit], hits: list[RuleHit]) -> str:
        ids = {h.rule_id for h in hits}
        if concept_hits or ids & {"TGA-SERIOUS", "FOOD-THERAPEUTIC", "TGA-PREVENTION", "NEEDS-REVIEW"}:
            return "therapeutic"
        if any(i.startswith("FOOD-NCC") for i in ids) or re.search(r"\d\s?(?:mg|mcg|g)\b|% ?rdi|\bmg/kg\b", m):
            return "nutrition_content"
        for t in ("other", "identity_or_origin", "compositional"):
            if self._any_type(t, m):
                return t
        return "marketing_puffery"

    def _any_type(self, t: str, m: str) -> bool:
        return any(re.search(p, m, re.I) for p in self.rb.lexicon["types"][t])

    @staticmethod
    def _kind_sentence(claim_type: str) -> str:
        return {
            "compositional": "This describes what the product contains or is free from.",
            "identity_or_origin": "This is a statement about origin, provenance or the company.",
            "other": "This is a price, availability or cross-sell statement.",
            "nutrition_content": "This states the amount of an ingredient or component.",
            "marketing_puffery": "This is general marketing language.",
        }.get(claim_type, "This is general marketing language.")

    @staticmethod
    def _status(verdict: Verdict, hits: list[RuleHit]) -> str:
        if verdict is Verdict.GREEN:
            return "PASS"
        if verdict is Verdict.RED:
            return "FAIL"
        return "NEEDS_REVIEW" if any(h.rule_id in REVIEW_RULES for h in hits if h.verdict is Verdict.AMBER) else "CONDITIONS"

    @staticmethod
    def _justify(verdict: Verdict, hits: list[RuleHit]) -> str:
        top = [h for h in hits if h.verdict is verdict]
        text = " ".join(dict.fromkeys(h.justification for h in top))
        lesser = [h.title for h in hits if h.verdict.severity < verdict.severity and h.verdict is not Verdict.GREEN]
        if lesser:
            text += " Also flagged: " + "; ".join(dict.fromkeys(lesser)) + "."
        return text
