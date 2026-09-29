"""Blocks -> claims: drop non-claim text (panels, print specs, directions, legal boilerplate), split blocks
into claim-sized statements and de-duplicate across files while keeping every place a claim was found."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine import Product, fold, matching_text
from models import Block, Claim, Provenance
from rulebank import squash

# Whole blocks that are never claims.
BLOCK_NOISE = [
    r"^(?:full |other |key |active )?ingredients\b", r"\bingredients:", r"^imported by\b", r"\bimported by:",
    r"^nutrition(?:al)? (?:in|ln) ?formation\b", r"\bserves? per (?:can|pack|bottle)\b", r"^supplement facts\b", r"^servings? (?:per|size)\b", r"^serving size\b",
    r"^amount per serving\b", r"^% daily value\b", r"daily value not established", r"^quantity per\b", r"^av\.? ?qty\b",
    r"^per (?:serv(?:e|ing)|100)", r"recommended dietary intake", r"^values are based on",
    r"\b(?:energy|protein|fat|carbohydrate|sugars|sodium)\b.*\b(?:protein|fat|carbohydrate|sodium)\b.*\b(?:carbohydrate|sodium|sugars)\b",
    r"\(as [a-z]", r"^\[", r"\[[^\]]*\].*\(|\(.*\[[^\]]*\]", r"\((?:inner|outer) capsule\)",
    r"\bpms ?\d|\bcmyk\b|\boverglos|\bmatched\b|\bvarnish\b|\bdieline\b|^file name\b|^date$|^(?:created|amended) by\b",
    r"^stock$|\bsemigloss\b|\bself adhesive\b|\bpefc\b|^\d{5,}\b|^barcode\b|^batch\b|^use by\b|^best before\b",
    r"^(?:unit|level) \d|\bnew york\b|\bnew north (?:rd|road)\b|\bmanufactured by\b|\bdistributed by\b",
    r"not been evaluated by the food and drug administration|not intended to diagnose, treat, cure",
    r"^\d{1,2} (?:january|february|march|april|may|june|july|august|september|october|november|december) \d{4}$",
    r"\bis a (?:registered )?trademark\b|\bused under licen[cs]e\b|\bpatents? pending\b",
    r"\brefund at\b|\bcollection depots\b|\bdepots/points\b|\bstate/territory of purchase\b",
    r"^please (?:see|recycle)\b|^(?:top|front|back|side)$",
    r"^(?:how to use|directions|for optimal use|volume|storage|key ingredients|active ingredients)$",
    r"^(?:inner|outer) capsule$", r"^week \d+\+?$", r"^dietary supplement\b", r"^\d+ capsules\b",
    r"\bco-?founder$", r"^formulated to comply with prop", r"^keep out of reach\b", r"^seal is broken",
    r"\bjuice\b.*\bsweetener\b|\bsweetener\b.*\bjuice\b|\bwater, .*\bsweetener\b",
]

# Sentences inside a claim block that are directions, warnings or dose statements.
SENTENCE_NOISE = [
    r"^recommended servings?\b", r"^children \d", r"\b\d+ ?ml once daily\b", r"^enjoy as is\b", r"^best taken with\b",
    r"^shake well\b", r"^not intended for use\b", r"^do not (?:exceed|use)\b", r"^storage\b", r"^store\b",
    r"^keep out of reach\b", r"^repeat daily\.?$", r"^take (?:one|two|\d+) (?:capsule|tablet|scoop|serve)s?\b",
    r"^consume one\b", r"^as with any supplement\b", r"^consult your\b", r"^for storage\b", r"^please see\b",
    r"^umfha\b", r"^\*? ?to learn more\b", r"^refrigerated\.?$",
    r"\bwww\.|\.com\b|\.co\.nz\b|\bweb:|\bemail:|\bpte ltd\b|\bsdn bhd\b",
    r"\bglycosides\b|\berythritol\b|\bhypromellose\b|\bacidity regulator\b|\bjuices? from\b|\(nz\) wat",
    r"^how\b.*\bcompares?$",
]


def _mostly_numbers(text: str) -> bool:
    tokens = text.split()
    numeric = sum(bool(re.fullmatch(r"[\d.,()%^*]+|\(?\d[\d.,]*\s?(?:k?cal|kj|mg|mcg|g|ml)\)?[*^]?|(?:k?cal|kj|mg|mcg|g|ml)\)?", t, re.I)) for t in tokens)
    return len(tokens) >= 3 and numeric / len(tokens) >= 0.6

NET_QUANTITY = re.compile(r"\s*\b\d+(?:\.\d+)? ?(?:ml|mL|l|L|g|kg)\b\s*$")
UNIT_WORDS = {"cal", "kcal", "kj", "mg", "mcg", "ml", "dfe", "iu"}
KEEP_SINGLE = {"natural", "organic", "vegan", "gluten-free", "caffeine-free", "keto", "sugar-free"}


def _is_noise(text: str, patterns: list[str]) -> bool:
    low = fold(text).lower()
    return any(re.search(p, low) for p in patterns)


def _header_rows(blocks: list[Block]) -> set[int]:
    """Indices of short OCR blocks in a row with at least two other short, same-height blocks of the same
    image (comparison-chart headers and table cells)."""
    short = [i for i, b in enumerate(blocks) if b.box and len(b.text.split()) <= 3]
    drop = set()
    for i in short:
        bi = blocks[i].box
        hi = bi[3] - bi[1]
        row = [j for j in short if j != i and blocks[j].provenance.file == blocks[i].provenance.file
               and abs(blocks[j].box[1] - bi[1]) <= 0.6 * hi
               and 0.6 <= (blocks[j].box[3] - blocks[j].box[1]) / hi <= 1.6]
        if len(row) >= 2:
            drop.add(i)
    return drop


def _split_headline(text: str) -> list[str]:
    """An ALL-CAPS headline run into a sentence-case statement ("... MANUKA HONEY Contains 10% UMF ...")."""
    words = text.split(" ")
    caps = lambda w: len(w) >= 2 and w.isalpha() and w.isupper()
    for i in range(2, len(words) - 1):
        if caps(words[i - 2]) and caps(words[i - 1]) and re.fullmatch(r"[A-Z][a-z]{2,}", words[i]):
            return [" ".join(words[:i]), *_split_headline(" ".join(words[i:]))]
    return [text]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", text)
    out = [s for p in parts for s in _split_headline(p)]
    return [squash(p) for p in out if squash(p)]


def whole_block(block: Block) -> bool:
    """Clean, already block-structured sources (OpenAI OCR, Word paragraphs) are checked as whole blocks so the
    claim keeps its context; noisy line-level sources (vision OCR, PDF, pasted text) are split into sentences."""
    m = block.provenance.method
    return m.startswith("ocr-openai") or m == "docx"


def _units(block: Block) -> list[tuple[str, str | None]]:
    """(claim text, drop reason) pairs for one block."""
    sentences = _sentences(block.text)
    if not whole_block(block):
        return [(s, None) for s in sentences]
    kept, out = [], []
    for s in sentences:
        if _is_noise(s, SENTENCE_NOISE) or _is_noise(s, BLOCK_NOISE):
            out.append((s, "direction / warning / dose statement"))
        else:
            kept.append(s)
    if kept:
        out.insert(0, (" ".join(kept), None))
    return out


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9%$]+", "", fold(text).lower())


@dataclass
class Extraction:
    claims: list[Claim]
    dropped: list[dict] = field(default_factory=list)  # what was filtered and why, for the audit trail


def segment(blocks: list[Block], product: Product, min_confidence: float = 0.65) -> Extraction:
    claims: dict[str, Claim] = {}
    order: list[str] = []
    dropped: list[dict] = []
    headers = _header_rows(blocks)

    def drop(text, prov, why):
        dropped.append({"text": text, "file": prov.file, "location": prov.location, "reason": why})

    for i, block in enumerate(blocks):
        if i in headers:
            drop(block.text, block.provenance, "chart / table header")
            continue
        if block.confidence is not None and block.confidence < min_confidence:
            drop(block.text, block.provenance, f"OCR confidence {block.confidence:.2f} below {min_confidence}")
            continue
        if _is_noise(block.text, BLOCK_NOISE) or _mostly_numbers(block.text):
            drop(block.text, block.provenance, "panel / print spec / legal or contact text")
            continue
        for sentence, reason in _units(block):
            if reason:
                drop(sentence, block.provenance, reason)
                continue
            text = NET_QUANTITY.sub("", sentence).strip(" ,;:-–*")
            if not text:
                continue
            if _is_noise(text, SENTENCE_NOISE) or _is_noise(text, BLOCK_NOISE):
                drop(text, block.provenance, "direction / warning / dose statement")
                continue
            masked = re.sub(r"\[name\]", " ", matching_text(text, product))
            words = [w for w in re.findall(r"[a-z][a-z'-]+", masked) if w not in UNIT_WORDS]
            if not words:
                drop(text, block.provenance, "product / brand name only")
                continue
            if len(words) < 2 and words[0] not in KEEP_SINGLE and not re.search(r"\d", masked):
                drop(text, block.provenance, "single word")
                continue
            if sum(len(w) >= 3 for w in words) == 0:
                drop(text, block.provenance, "no words")
                continue
            k = _key(text)
            if k in claims:
                claims[k].provenance.append(block.provenance)
            else:
                claims[k] = Claim("", text, [block.provenance])
                order.append(k)

    out = []
    for n, k in enumerate(order, start=1):
        c = claims[k]
        c.id = f"{product.id}-{n:03d}"
        out.append(c)
    return Extraction(out, dropped)
