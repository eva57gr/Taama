"""REST API for the claim checker (used by the Next.js frontend). Run: `uvicorn api:app --host 0.0.0.0 --port 8000 --reload`."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")  # OPENAI_API_KEY etc.; before modules read the env

import logging

logging.basicConfig(level=os.environ.get("CLAIMCHECK_LOG", "INFO").split("#")[0].strip().upper() or "INFO",
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("claimcheck.api")

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from engine import (
    REGIMES,
    Engine,
    Product,
    delete_product,
    list_products,
    load_product,
    product_from_dict,
    product_path,
    product_to_dict,
    save_product,
)
from ingest import IMAGE_SUFFIXES, OFFICE_SUFFIXES
from ocr import OCRError
from pipeline import check

MAX_UPLOAD = 20_000_000
MAX_FILES = 20
MAX_URLS = 10
MAX_TEXT = 200_000
ALLOWED = IMAGE_SUFFIXES | OFFICE_SUFFIXES | {".pdf", ".html", ".htm", ".txt", ".md"}
ORIGINS = [o.strip() for o in os.environ.get("CLAIMCHECK_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001").split(",") if o.strip()]
# Frontend opened from a private-network address (e.g. http://192.168.1.20:3001).
ORIGIN_REGEX = os.environ.get(
    "CLAIMCHECK_CORS_ORIGIN_REGEX",
    r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?",
) or None

app = FastAPI(title="Claim Checker API", version="1.0.0",
              description="Deterministic claim compliance checks against Australian TGA / FSANZ rules.")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_origin_regex=ORIGIN_REGEX,
                   allow_methods=["*"], allow_headers=["*"])

_ENGINE: Engine | None = None
_LOCK = threading.Lock()


def engine() -> Engine:
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = Engine()
        return _ENGINE


@app.exception_handler(FileNotFoundError)
async def _not_found(_: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(OCRError)
async def _ocr_failed(_: Request, exc: OCRError):
    log.error("OCR error -> 502: %s", exc)
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_request(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ------------------------------------------------------------------------------------------ helpers


def _summary(p: Product) -> dict:
    return {"id": p.id, "name": p.name, "regime": p.regime, "regime_label": REGIMES[p.regime]}


def _adhoc(regime: str) -> Product:
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    return Product(id="adhoc", name="(ad-hoc input)", regime=regime,
                   regime_basis={"reason": "regime chosen by the user", "cite": None},
                   inputs=[], mask=[], record={}, record_source="")


def _check_url(url: str) -> str:
    """Only public http(s) hosts: the server fetches these, so block internal addresses (SSRF)."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"not an http(s) URL: {url}")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"cannot resolve {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"URL host {u.hostname} is not a public address")
    return url.strip()


def _result(run: dict, product: dict, names: dict[str, str] | None = None) -> dict:
    names = names or {}

    def rename(f):
        return names.get(f, names.get(str(Path(f)), f)) if isinstance(f, str) else f

    claims = []
    for c in run["claims"]:
        c = dict(c)
        c["found_in"] = [{**p, "file": rename(p.get("file"))} for p in c.get("found_in", [])]
        claims.append(c)
    dropped = [{**d, "file": rename(d.get("file"))} for d in run.get("not_assessed") or []]
    return {"product": product, "summary": run["summary"], "claims": claims,
            "not_assessed": dropped[:200], "not_assessed_total": len(dropped)}


# ------------------------------------------------------------------------------------------- routes


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/regimes")
def regimes() -> dict:
    return {"regimes": [{"id": k, "label": v} for k, v in REGIMES.items()]}


@app.get("/api/products")
def get_products() -> dict:
    return {"products": [_summary(p) for p in list_products()]}


@app.get("/api/products/{product_id}")
def get_product(product_id: str) -> dict:
    return product_to_dict(load_product(product_id))


@app.post("/api/products", status_code=201)
def create_product(body: dict) -> dict:
    p = product_from_dict(body)
    if product_path(p.id).is_file():
        raise HTTPException(409, f"product {p.id!r} already exists")
    save_product(p)
    return product_to_dict(p)


@app.put("/api/products/{product_id}")
def update_product(product_id: str, body: dict) -> dict:
    if not product_path(product_id).is_file():
        raise FileNotFoundError(f"unknown product {product_id!r}")
    p = product_from_dict({**body, "id": product_id})
    save_product(p)
    return product_to_dict(p)


@app.delete("/api/products/{product_id}", status_code=204)
def remove_product(product_id: str) -> None:
    delete_product(product_id)


@app.get("/api/rules")
def rules() -> dict:
    rb = engine().rb
    return {"rules": [
        {"id": r.id, "verdict": r.verdict.value, "title": r.title, "why": r.why,
         "citations": [rb.describe(c) for c in r.citations]}
        for r in rb.rules.values()
    ]}


@app.get("/api/sources")
def sources() -> dict:
    keep = ("title", "register_id", "compilation", "in_force_from", "retrieved", "url")
    return {"sources": [{"id": k, **{x: v[x] for x in keep if x in v}} for k, v in engine().rb.manifest.items()]}


@app.post("/api/check")
async def run_check(
    product: str = Form(""),
    regime: str = Form(""),
    text: str = Form(""),
    urls: list[str] = Form(default=[]),
    files: list[UploadFile] = File(default=[]),
    ai: str = Form("auto"),
) -> dict:
    if bool(product) == bool(regime):
        raise ValueError("choose either a product profile or a regime")
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ValueError("text is too long")
    urls = [u for raw in urls for u in raw.split() if u]
    if len(urls) > MAX_URLS or len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files and {MAX_URLS} URLs per check")
    files = [f for f in files if f.filename]
    if not (text or files or urls):
        raise ValueError("paste some text, upload a file or add a URL")

    prod = load_product(product) if product else _adhoc(regime)
    checked_urls = [_check_url(u) for u in urls]

    paths, names, total = [], {}, 0
    tmpdir = tempfile.mkdtemp(prefix="claimcheck-")
    try:
        for i, f in enumerate(files):
            suffix = Path(f.filename).suffix.lower()
            if suffix not in ALLOWED:
                raise ValueError(f"unsupported file type: {suffix or f.filename}")
            data = await f.read(MAX_UPLOAD + 1)
            total += len(data)
            if total > MAX_UPLOAD:
                raise ValueError("uploads are larger than 20 MB in total")
            path = Path(tmpdir) / f"upload{i}{suffix}"
            path.write_bytes(data)
            paths.append(str(path))
            names[str(path)] = Path(f.filename).name
        use_ai = {"on": True, "off": False}.get(ai.strip().lower())
        log.info("check: product=%s regime=%s files=%s urls=%d text=%d chars ai=%s", prod.id, prod.regime,
                 list(names.values()), len(checked_urls), len(text), ai)
        run = await run_in_threadpool(check, prod, sources=paths + checked_urls, text=text or None, engine=engine(),
                                      use_ai=use_ai)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return _result(run, {"id": prod.id, "name": prod.name, "regime": prod.regime,
                         "regime_label": REGIMES[prod.regime]}, names)

