"""Build the plain-text source snapshots that every rule citation is checked against.

Run once (needs internet for the official texts):

    python scripts/snapshot_sources.py

Outputs go to rulebank/au/snapshots/. Each snapshot is committed, so the checker
and the citation tests never touch the network. Re-running this script is how
the rule bank is refreshed when a regulation is amended.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "rulebank" / "au" / "snapshots"
CONSULTANT_DOCX = ROOT / "rulebank" / "au" / "source_docs" / "sources_and_logic_aus.docx"

API = "https://api.prod.legislation.gov.au/v1"
SITE = "https://www.legislation.gov.au"

# Federal Register of Legislation title IDs for the official instruments we cite.
LEGISLATION = {
    "fsc_std_1_2_7": "F2015L00394",  # FSC Standard 1.2.7 Nutrition, health and related claims
    "fsc_schedule_4": "F2015L00474",  # FSC Schedule 4 Nutrition, health and related claims
    "tga_permissible_indications": "F2025L00450",  # TG (Permissible Indications) Determination (No. 1) 2025
    "tga_advertising_code": "F2021L01661",  # TG (Therapeutic Goods Advertising Code) Instrument 2021
}

FSANZ_NOTIFIED = "https://www.foodstandards.gov.au/fhr?combine={query}"
# Businesses whose notified food-health relationships we snapshot (one per sample brand that relies on them).
FSANZ_NOTIFIED_QUERIES = {"fsanz_notified_arepa": "arepa"}

BLOCK_TAGS = {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "table"}
CELL_TAGS = {"td", "th"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in CELL_TAGS:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |") for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip() + "\n"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (claimcheck snapshot)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def snapshot_legislation(key: str, title_id: str) -> dict:
    version = json.loads(_get(f"{API}/versions/find(titleId='{title_id}',asAtSpecification='Latest')"))
    start = version["start"][:10]
    url = f"{SITE}/{title_id}/{start}/{start}/text/original/epub/OEBPS/document_1/document_1.html"
    text = html_to_text(_get(url).decode("utf-8"))
    (SNAP_DIR / f"{key}.txt").write_text(text, encoding="utf-8")
    return {
        "title": version["name"],
        "title_id": title_id,
        "register_id": version["registerId"],
        "compilation": version["compilationNumber"],
        "in_force_from": start,
        "status": version["status"],
        "url": f"{SITE}/{title_id}/latest/text",
        "snapshot_url": url,
    }


def snapshot_fsanz_notified(key: str, query: str) -> dict:
    url = FSANZ_NOTIFIED.format(query=query)
    text = html_to_text(_get(url).decode("utf-8"))
    start = text.find("Standard 1.2.7 requires a person who is self-substantiating")
    end = text.find("Food Standards Australia New Zealand acknowledges the Traditional Owners")
    if start < 0 or end < 0:
        raise RuntimeError(f"FSANZ notified-relationships page layout changed: {url}")
    (SNAP_DIR / f"{key}.txt").write_text(text[start:end].strip() + "\n", encoding="utf-8")
    return {
        "title": f"FSANZ — Notified food-health relationships to make a health claim (search: '{query}')",
        "url": url,
        "retrieved": date.today().isoformat(),
    }


def snapshot_consultant_doc() -> dict:
    d = docx.Document(str(CONSULTANT_DOCX))
    out = []
    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[1]
        if tag == "p":
            t = Paragraph(child, d).text.strip()
            if t:
                out.append(t)
        elif tag == "tbl":
            out.append("")
            for row in Table(child, d).rows:
                cells, prev = [], None
                for c in row.cells:
                    if c._tc is prev:  # merged cells repeat; keep one copy
                        continue
                    prev = c._tc
                    cells.append(" / ".join(x.strip() for x in c.text.splitlines() if x.strip()))
                out.append(" | ".join(cells))
            out.append("")
    (SNAP_DIR / "sources_and_logic_aus.txt").write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    return {
        "title": "Taama — Australia Product Classification & Regulatory Assessment (consultant working document)",
        "author": "Dione Simmons, Health Product Development Co (reviewed 23.07.26, signed 8.08.26)",
        "file": "rulebank/au/source_docs/sources_and_logic_aus.docx",
    }


def main() -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"generated": date.today().isoformat(), "sources": {}}
    manifest["sources"]["sources_and_logic_aus"] = snapshot_consultant_doc()
    for key, title_id in LEGISLATION.items():
        manifest["sources"][key] = snapshot_legislation(key, title_id)
        print(f"{key}: {manifest['sources'][key]['register_id']}")
    for key, query in FSANZ_NOTIFIED_QUERIES.items():
        manifest["sources"][key] = snapshot_fsanz_notified(key, query)
        print(f"{key}: {manifest['sources'][key]['url']}")
    (SNAP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
