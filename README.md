# Claim compliance checker for Australia (TGA / FSANZ)

Reads product copy (raw text, PDF artwork, images, web pages), extracts the claims, and checks each one against
a bank of Australian rules: the TGA Permissible Indications Determination and Advertising Code for listed
medicines, and Food Standards Code Standard 1.2.7 / Schedule 4 for foods. For every claim it gives:

- a RED / AMBER / GREEN verdict;
- the rules behind it, each with a verbatim excerpt, section or item locator, and the line in a committed snapshot of the source;
- a plain-English justification.

The verdict engine is deterministic Python: the same claim, profile and rule bank always give the same
red / amber / green result. The rule engine itself does not call the network (except fetching URLs you submit).
Every citation is checked verbatim against the source snapshot when it is emitted. Wording the rule bank can't map
is flagged AMBER *needs review* rather than guessed.

An optional second opinion (`backend/judge.py`) runs only when `OPENAI_API_KEY` is set and `CLAIMCHECK_JUDGE`
is not `off`. It may only quote passages retrieved from the same snapshots; a quote that is not verbatim is
dropped, and a red or amber answer with no surviving quote becomes amber `AI-UNGROUNDED`. The sample-bank
results in `results/` were produced with the judge off. Image OCR still needs a key.

- **Backend:** FastAPI (`backend/`)
- **Frontend:** Next.js (`frontend/`)

## How to run

No API keys, accounts or external services are needed for PDF and text checks. Image OCR
requires `OPENAI_API_KEY`. Python 3.11+ and Node 20+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Both servers listen on `0.0.0.0`, so they are reachable from other machines on the network
(allow ports 8000 and 3000 through the firewall).

Backend (port 8000, OpenAPI docs at `/docs`):

```bash
cd backend
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Frontend (port 3000; Next uses `-H`, not `--host`, and it is already set in the scripts):

```bash
cd frontend
npm install
npm run dev                        # add "-- -p 3001" to use another port
npm run build; npm start           # production build
```

By default the frontend calls the API on the same host the page was opened from, at port 8000
(e.g. `http://192.168.1.20:3000` calls `http://192.168.1.20:8000`).

To point at another API, copy `frontend/.env.local.example` to `frontend/.env.local`. The dev and build scripts
use webpack (`--webpack`) because Turbopack's on-disk cache can hit file-lock errors on Windows.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CLAIMCHECK_CORS_ORIGINS` | localhost / 127.0.0.1 on ports 3000 and 3001 | Allowed frontend origins (comma-separated) |
| `CLAIMCHECK_CORS_ORIGIN_REGEX` | localhost and private LAN IPs (10.x, 172.16-31.x, 192.168.x), any port | Extra allowed origins (regex; empty to disable) |
| `CLAIMCHECK_PRODUCTS_DIR` | `products/` | Where product profiles (`<id>.yaml`) are stored |
| `OPENAI_API_KEY` | unset | Required for image OCR (OpenAI vision) |
| `OPENAI_OCR_MODEL` | `gpt-4o` | Model used for OpenAI OCR |
| `NEXT_PUBLIC_API_URL` | `<page host>:8000` | Backend URL used by the frontend |

### API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | health check |
| GET | `/api/regimes` | available regimes |
| GET, POST | `/api/products` | list / create product profiles |
| GET, PUT, DELETE | `/api/products/{id}` | read / update / delete a profile |
| GET | `/api/rules` | rule table with citations |
| GET | `/api/sources` | regulation snapshots (register IDs, compilations) |
| POST | `/api/check` | multipart: `product` **or** `regime`, plus any of `text`, `urls`, `files` |

URL checks only fetch public hosts (internal addresses are rejected). Uploads are limited to 20 files and
20 MB in total, and to PDF, Word (`.docx`), Excel (`.xlsx`), image, HTML and text types. Word files contribute
paragraphs and table cells; Excel files contribute every text cell (located as `sheet 'X', cell A1`).

`product` uses a saved profile (regime, product record, brand names). `regime` checks ad-hoc input with an
empty profile. Without a nutrition record, any claim that depends on amounts comes back AMBER
"cannot verify", never GREEN.

What needs what:

- **OCR** uses OpenAI vision (`gpt-4o` by default, temperature 0): copy `backend/.env.example` to
  `backend/.env`, fill in `OPENAI_API_KEY` and restart uvicorn. Results are cached under `extracted/`,
  keyed by image SHA-256, model and prompt, so repeat checks are identical and do not call the API again.
- **Network** is used only for submitted URLs and by `scripts/snapshot_sources.py`, which rebuilds the
  regulation snapshots from the Federal Register of Legislation API and the FSANZ register.

## How it works

```
input ─► ingest ─► blocks ─► segment ─► claims ─► engine ─► rule hits + citations ─► verdict
         (PDF text layer, OCR+cache,    (noise filters,      (product profile: regime, record, brand masks)
          HTML, text; each block keeps   sentence split,
          file + page/box provenance)    de-dup, IDs)
```

1. **Ingest** (`ingest.py`, `ocr.py`). The PDF text layer is read with pdfminer. Images go through OpenAI
   vision OCR; words that run together are re-split with a word-frequency model, and brand and ingredient
   names are protected from splitting. Each block records its file and location (PDF page and text box, or
   image text-block index).
2. **Segment** (`segment.py`). Nutrition panels, ingredient lists, print specs, directions, warnings,
   addresses, boilerplate and unreadable OCR are dropped; every dropped text is returned under
   `not_assessed` with its reason. The rest is split into sentences and de-duplicated across files.
3. **Regime.** Each product profile (`products/*.yaml`) declares whether the product is assessed as a TGA listed
   medicine or a food, with a cited reason. The profile also carries the Nutrition Information Panel values and
   the brand/product names to mask, so a product name isn't read as a claim.
4. **Engine** (`engine.py`). Claim text is matched against *concepts* (`rulebank/au/concepts.yaml`, e.g.
   energy, stress, cognition, immunity), each linked to Permissible Indications items, Schedule 4 health-claim
   rows and notified relationships.
   - **Listed medicines.** Exact permitted wording is GREEN. A reworded indication is AMBER *needs review* and
     names the closest items. A therapeutic claim with no indication is RED, as is a traditional-evidence-only
     indication without a traditional qualifier. Advertising Code checks cover disease, prevention, safety,
     guarantee and continued-use statements.
   - **Foods.** Nutrition content claims are checked against Schedule 4 S4—3 conditions and the product record
     (e.g. ≥10% RDI, ≥25% for "good source"). Health claims need a Schedule 4 or notified basis plus the
     Standard 1.2.7 conditions. Therapeutic claims are RED; comparative descriptors, NZ-only categories and
     stated amounts that contradict the label are AMBER.
5. **Verdict.** The most severe rule hit wins (RED > AMBER > GREEN). AMBER is either CONDITIONS (fine if the
   listed conditions are met) or NEEDS_REVIEW (a wording or basis question for a person). The rule table is
   `rulebank/au/rules.yaml`.

### Traceability

Citations point at plain-text snapshots in `rulebank/au/snapshots/`; `manifest.json` records the register ID,
compilation number and in-force date of each source. `RuleBank.verify` checks every citation as it is emitted,
and a citation whose excerpt isn't in the snapshot raises `CitationError`.

## Limitations

- **Recall depends on the lexicon.** Claims are recognised through concept patterns and lexicons. A health
  claim in unknown wording, without a benefit verb plus health word, comes out GREEN "no claim"; the result
  says which rule classed it that way. The optional AI judge does not run unless a key is set.
- **Classification is not automated.** Food vs medicine, the nutrition record and brand names are declared per
  product profile.
- **Not checked:** ingredient permissibility, mandatory label elements, NPSC calculation (reported as
  unverified), evidence dossiers, AUST L(A), registered medicines, the NZ market, scanned PDFs without a text
  layer, JavaScript-rendered pages.
- **OCR** can miss small or low-contrast print. Dropped text is listed in `not_assessed`.
- **Regulation drift.** Snapshots are dated; re-run `scripts/snapshot_sources.py` after amendments.

## Repository layout

```
backend/          FastAPI app (api.py) + ingest, ocr, segment, engine, rulebank, pipeline, models, judge
frontend/         Next.js app
rulebank/au/      rules.yaml, concepts.yaml, lexicon.yaml, snapshots/ (+ manifest.json), source_docs/
products/         product profiles (comvita, seed, arepa)
Source/Material/  case-study artwork, images and formulation sheets the profiles point at
results/          one JSON per product: two consecutive runs
scripts/          snapshot_sources.py; run_sample_bank.py (rewrites results/)
tests/            test_citations.py, test_ocr.py
```

## Sample bank

`python scripts/run_sample_bank.py` checks Comvita, Seed and Arepa twice with the judge off and writes
`results/<id>.json`. Add `--judge on` to run the same inputs with the AI judge (needs `OPENAI_API_KEY`);
that writes `results/<id>.judge-on.json` and leaves the default files alone. Image OCR uses OpenAI vision
either way. `CLAIMS.md` is one line per claim from the judge-off run. `WRITEUP.md` answers the
submission questions (stability, a new market, production, what comes next).

Citation check: `python tests/test_citations.py`.
