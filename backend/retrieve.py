"""Vector search over the regulation snapshots (the RAG half of the hybrid check).

The snapshots are cut into passages of a few paragraphs, each tagged with its source and line range, embedded with
an OpenAI embedding model and stored under rulebank/au/index/. The index is rebuilt automatically when a snapshot
or the embedding model changes (checked by SHA-256), so retrieval always reflects the cited text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass

import numpy as np

from rulebank import RULEBANK_DIR, RuleBank

log = logging.getLogger("claimcheck.retrieve")
INDEX_DIR = RULEBANK_DIR / "index"
TARGET_CHARS = 900
CHUNK_VERSION = 1

# Which snapshots are relevant to which regime.
REGIME_SOURCES = {
    "tga_listed": ["tga_permissible_indications", "tga_advertising_code", "sources_and_logic_aus"],
    "fsanz_food": ["fsc_std_1_2_7", "fsc_schedule_4", "fsanz_notified_arepa", "sources_and_logic_aus"],
}


@dataclass(frozen=True)
class Passage:
    id: str
    source: str
    start_line: int
    end_line: int
    text: str

    @property
    def locator(self) -> str:
        return f"lines {self.start_line}-{self.end_line}" if self.end_line > self.start_line else f"line {self.start_line}"


def embed_model() -> str:
    return os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small").split("#")[0].strip()


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """(first line, last line, text) for each blank-line separated paragraph; 1-based line numbers."""
    out, buf, start = [], [], 0
    for n, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not buf:
                start = n
            buf.append(line.rstrip())
        elif buf:
            out.append((start, n - 1, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, start + len(buf) - 1, "\n".join(buf)))
    return out


def chunk(source: str, text: str) -> list[Passage]:
    passages: list[Passage] = []
    cur: list[tuple[int, int, str]] = []

    def flush():
        if cur:
            passages.append(Passage(f"{source}#{len(passages) + 1}", source, cur[0][0], cur[-1][1],
                                    "\n".join(p[2] for p in cur)))
            cur.clear()

    for para in _paragraphs(text):
        if cur and sum(len(p[2]) for p in cur) + len(para[2]) > TARGET_CHARS:
            flush()
        cur.append(para)
    flush()
    return passages


def _embed(texts: list[str]) -> np.ndarray:
    from openai import OpenAI

    client, vecs = OpenAI(timeout=120), []
    for i in range(0, len(texts), 100):
        resp = client.embeddings.create(model=embed_model(), input=texts[i:i + 100])
        vecs += [d.embedding for d in resp.data]
    arr = np.asarray(vecs, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


class Index:
    def __init__(self, rb: RuleBank):
        self.rb = rb
        self._lock = threading.Lock()
        self._passages: list[Passage] | None = None
        self._vectors: np.ndarray | None = None

    def _fingerprint(self) -> dict:
        texts = self.rb._snapshot_text
        return {
            "model": embed_model(),
            "chunk_version": CHUNK_VERSION,
            "target_chars": TARGET_CHARS,
            "snapshots": {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in sorted(texts.items())},
        }

    def _load(self) -> None:
        with self._lock:
            if self._passages is not None:
                return
            meta_path, vec_path = INDEX_DIR / "passages.json", INDEX_DIR / "vectors.npy"
            fp = self._fingerprint()
            if meta_path.exists() and vec_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("fingerprint") == fp:
                    self._passages = [Passage(**p) for p in meta["passages"]]
                    self._vectors = np.load(vec_path)
                    log.info("RAG index loaded: %d passages (%s)", len(self._passages), fp["model"])
                    return
                log.info("RAG index stale (snapshot or model changed); rebuilding")
            passages = [p for k, t in sorted(self.rb._snapshot_text.items()) for p in chunk(k, t)]
            log.info("RAG index: embedding %d passages with %s", len(passages), fp["model"])
            vectors = _embed([p.text for p in passages])
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            np.save(vec_path, vectors)
            meta_path.write_text(json.dumps({"fingerprint": fp, "passages": [asdict(p) for p in passages]},
                                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            self._passages, self._vectors = passages, vectors
            log.info("RAG index built and saved to %s", INDEX_DIR)

    def search(self, query: str, regime: str, k: int = 8) -> list[Passage]:
        self._load()
        allowed = set(REGIME_SOURCES.get(regime, []))
        q = _embed([query])[0]
        scores = self._vectors @ q
        order = np.argsort(-scores)
        return [self._passages[i] for i in order if self._passages[i].source in allowed][:k]
