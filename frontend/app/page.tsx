"use client";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Results from "@/components/Results";
import { ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, type CheckResult, type ProductSummary, type Regime } from "@/lib/api";

const ACCEPT = ".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.html,.htm,.txt,.md";
const MAX_BYTES = 20_000_000;

export default function CheckPage() {
  return (
    <Suspense>
      <CheckForm />
    </Suspense>
  );
}

function CheckForm() {
  const params = useSearchParams();
  const [regimes, setRegimes] = useState<Regime[]>([]);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [mode, setMode] = useState<"product" | "regime">(params.get("product") ? "product" : "regime");
  const [product, setProduct] = useState(params.get("product") ?? "");
  const [regime, setRegime] = useState("");
  const [text, setText] = useState("");
  const [urls, setUrls] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CheckResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    Promise.all([api.regimes(), api.products()])
      .then(([r, p]) => {
        setRegimes(r);
        setProducts(p);
        setRegime((cur) => cur || r[0]?.id || "");
        setProduct((cur) => cur || p[0]?.id || "");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((cur) => {
      const seen = new Set(cur.map((f) => f.name + f.size));
      return [...cur, ...incoming.filter((f) => !seen.has(f.name + f.size))];
    });
  };

  const totalBytes = files.reduce((n, f) => n + f.size, 0);
  const hasInput = Boolean(text.trim() || urls.trim() || files.length);
  const target = mode === "product" ? product : regime;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (totalBytes > MAX_BYTES) return setError("Uploads must be 20 MB or less in total.");
    const form = new FormData();
    form.append(mode, target);
    if (text.trim()) form.append("text", text);
    urls
      .split(/\s+/)
      .filter(Boolean)
      .forEach((u) => form.append("urls", u));
    files.forEach((f) => form.append("files", f, f.name));
    setResult(null);
    setBusy(true);
    try {
      setResult(await api.check(form));
    } catch (err) {
      setResult(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Check marketing claims"
        subtitle="Paste copy, upload labels or artwork, or add web pages. Every claim is checked against cited TGA / FSANZ rules."
      />

      <form onSubmit={submit} className="card grid gap-6 p-6 lg:grid-cols-[18rem_1fr]">
        <div className="space-y-4">
          <div>
            <span className="label">Check against</span>
            <div className="grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm">
              {(["regime", "product"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`rounded-md py-1.5 font-medium transition ${
                    mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {m === "regime" ? "Regime" : "Product"}
                </button>
              ))}
            </div>
          </div>

          {mode === "regime" ? (
            <div>
              <label htmlFor="regime" className="label">Regulatory regime</label>
              <select id="regime" className="input" value={regime} onChange={(e) => setRegime(e.target.value)}>
                {regimes.map((r) => (
                  <option key={r.id} value={r.id}>{r.label}</option>
                ))}
              </select>
              <p className="mt-2 text-xs text-slate-500">
                Quick check with no product profile. Nutrient and property conditions are marked unverified.
              </p>
            </div>
          ) : (
            <div>
              <label htmlFor="product" className="label">Product profile</label>
              <select id="product" className="input" value={product} onChange={(e) => setProduct(e.target.value)}>
                {products.length === 0 && <option value="">No products yet</option>}
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <p className="mt-2 text-xs text-slate-500">
                Uses the profile&apos;s regime, brand-name mask, nutrient record and properties.{" "}
                <Link href="/products/new" className="text-indigo-600 hover:underline">Create a product</Link>
              </p>
            </div>
          )}

          <button type="submit" className="btn-primary w-full" disabled={busy || !hasInput || !target}>
            {busy ? (
              <>
                <Spinner /> Checking…
              </>
            ) : (
              "Run check"
            )}
          </button>
          {busy && (
            <p className="text-xs text-slate-500">Images and PDFs are OCR&apos;d on the server; this can take a minute.</p>
          )}
        </div>

        <div className="space-y-5">
          <div>
            <label htmlFor="text" className="label">Text</label>
            <textarea
              id="text"
              className="input min-h-32 font-mono"
              placeholder={"One claim per line works best, e.g.\nSupports healthy immune function.\nClinically proven to boost focus."}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>

          <div>
            <span className="label">Files</span>
            <div
              role="button"
              tabIndex={0}
              onClick={() => fileInput.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") fileInput.current?.click();
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                addFiles(e.dataTransfer.files);
              }}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm transition ${
                dragging ? "border-indigo-400 bg-indigo-50" : "border-slate-300 hover:border-slate-400"
              }`}
            >
              <p className="font-medium text-slate-700">Drop files here or click to browse</p>
              <p className="mt-1 text-xs text-slate-500">PDF, Word (.docx), Excel (.xlsx), images, HTML or text · up to 20 files, 20 MB total</p>
              <input
                ref={fileInput}
                type="file"
                multiple
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {files.length > 0 && (
              <ul className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm">
                {files.map((f, i) => (
                  <li key={f.name + f.size} className="flex items-center justify-between px-3 py-1.5">
                    <span className="truncate text-slate-700">{f.name}</span>
                    <span className="flex shrink-0 items-center gap-3 text-xs text-slate-500">
                      {(f.size / 1024).toFixed(0)} KB
                      <button
                        type="button"
                        aria-label={`Remove ${f.name}`}
                        className="text-slate-400 hover:text-red-600"
                        onClick={() => setFiles(files.filter((_, j) => j !== i))}
                      >
                        ✕
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <label htmlFor="urls" className="label">Web pages</label>
            <textarea
              id="urls"
              className="input min-h-16 font-mono"
              placeholder="https://example.com/product (one per line, up to 10)"
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
            />
          </div>
        </div>
      </form>

      {error && <ErrorBox message={error} />}
      {result && <Results result={result} />}
    </div>
  );
}
