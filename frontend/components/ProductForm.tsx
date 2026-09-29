"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Product, type RecordEntry, type Regime } from "@/lib/api";
import { ErrorBox, Spinner } from "./ui";

interface RecordRow {
  nutrient: string;
  servingQty: string;
  servingUnit: string;
  per100Qty: string;
  per100Unit: string;
  rdi: string;
}

interface PropertyRow {
  name: string;
  patterns: string;
}

const EMPTY: Product = {
  id: "",
  name: "",
  regime: "",
  mask: [],
  liquid: false,
  record: {},
  properties: {},
  npsc: null,
};

const toRows = (record: Record<string, RecordEntry>): RecordRow[] =>
  Object.entries(record).map(([nutrient, r]) => ({
    nutrient,
    servingQty: r.per_serving ? String(r.per_serving[0]) : "",
    servingUnit: r.per_serving?.[1] ?? "",
    per100Qty: r.per_100 ? String(r.per_100[0]) : "",
    per100Unit: r.per_100?.[1] ?? "",
    rdi: r.rdi_pct != null ? String(r.rdi_pct) : "",
  }));

function slug(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

export default function ProductForm({ initial }: { initial?: Product }) {
  const router = useRouter();
  const editing = Boolean(initial);
  const base = initial ?? EMPTY;
  const [regimes, setRegimes] = useState<Regime[]>([]);
  const [id, setId] = useState(base.id);
  const [idTouched, setIdTouched] = useState(editing);
  const [name, setName] = useState(base.name);
  const [regime, setRegime] = useState(base.regime);
  const [reason, setReason] = useState(base.regime_basis?.reason ?? "");
  const [mask, setMask] = useState(base.mask.join("\n"));
  const [business, setBusiness] = useState(base.business ?? "");
  const [liquid, setLiquid] = useState(base.liquid);
  const [npsc, setNpsc] = useState<string>(base.npsc == null ? "" : String(base.npsc));
  const [rows, setRows] = useState<RecordRow[]>(toRows(base.record));
  const [props, setProps] = useState<PropertyRow[]>(
    Object.entries(base.properties).map(([k, v]) => ({ name: k, patterns: v.join("\n") })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .regimes()
      .then((r) => {
        setRegimes(r);
        setRegime((cur) => cur || r[0]?.id || "");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  function buildRecord(): Record<string, RecordEntry> {
    const out: Record<string, RecordEntry> = {};
    for (const r of rows) {
      const key = r.nutrient.trim().toLowerCase();
      if (!key) continue;
      const entry: RecordEntry = {};
      const amount = (qty: string, unit: string, label: string): [number, string] | undefined => {
        if (!qty.trim()) return undefined;
        const n = Number(qty);
        if (!Number.isFinite(n) || !unit.trim()) throw new Error(`${key}: ${label} needs a number and a unit`);
        return [n, unit.trim()];
      };
      entry.per_serving = amount(r.servingQty, r.servingUnit, "per serving");
      entry.per_100 = amount(r.per100Qty, r.per100Unit, "per 100");
      if (r.rdi.trim()) {
        const n = Number(r.rdi);
        if (!Number.isFinite(n)) throw new Error(`${key}: %RDI must be a number`);
        entry.rdi_pct = n;
      }
      out[key] = Object.fromEntries(Object.entries(entry).filter(([, v]) => v !== undefined));
    }
    return out;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    let payload: Product;
    try {
      payload = {
        ...base,
        id: id.trim(),
        name: name.trim(),
        regime,
        regime_basis: { reason: reason.trim(), cite: base.regime_basis?.cite ?? null },
        mask: mask.split("\n").map((s) => s.trim()).filter(Boolean),
        business: business.trim() || null,
        liquid,
        npsc: npsc === "" ? null : npsc === "true",
        record: buildRecord(),
        properties: Object.fromEntries(
          props
            .filter((p) => p.name.trim())
            .map((p) => [p.name.trim().toLowerCase(), p.patterns.split("\n").map((s) => s.trim()).filter(Boolean)]),
        ),
      };
    } catch (err) {
      return setError((err as Error).message);
    }
    setBusy(true);
    try {
      if (editing) await api.updateProduct(base.id, payload);
      else await api.createProduct(payload);
      router.push("/products");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const updateRow = (i: number, patch: Partial<RecordRow>) =>
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const updateProp = (i: number, patch: Partial<PropertyRow>) =>
    setProps(props.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  return (
    <form onSubmit={submit} className="space-y-6">
      <section className="card grid gap-4 p-6 sm:grid-cols-2">
        <h2 className="text-base font-semibold text-slate-900 sm:col-span-2">Basics</h2>
        <div>
          <label htmlFor="name" className="label">Product name</label>
          <input
            id="name"
            className="input"
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!idTouched) setId(slug(e.target.value));
            }}
          />
        </div>
        <div>
          <label htmlFor="id" className="label">Id</label>
          <input
            id="id"
            className="input font-mono disabled:bg-slate-50 disabled:text-slate-500"
            required
            pattern="[a-z0-9][a-z0-9_\-]{0,63}"
            title="Lower-case letters, digits, '-' and '_'"
            disabled={editing}
            value={id}
            onChange={(e) => {
              setIdTouched(true);
              setId(e.target.value);
            }}
          />
        </div>
        <div>
          <label htmlFor="regime" className="label">Regime</label>
          <select id="regime" className="input" value={regime} onChange={(e) => setRegime(e.target.value)}>
            {regimes.map((r) => (
              <option key={r.id} value={r.id}>{r.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="business" className="label">Business (optional)</label>
          <input id="business" className="input" value={business} onChange={(e) => setBusiness(e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor="reason" className="label">Why this regime applies</label>
          <textarea
            id="reason"
            className="input min-h-20"
            placeholder="e.g. Capsule dosage form presented for a therapeutic use, so assessed as a listed medicine."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap gap-6 sm:col-span-2">
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={liquid} onChange={(e) => setLiquid(e.target.checked)} className="h-4 w-4" />
            Liquid (amounts per 100 mL)
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            Meets the nutrient profiling scoring criterion
            <select className="input w-auto py-1" value={npsc} onChange={(e) => setNpsc(e.target.value)}>
              <option value="">Unknown</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </label>
        </div>
      </section>

      <section className="card space-y-2 p-6">
        <h2 className="text-base font-semibold text-slate-900">Brand and product names</h2>
        <p className="text-sm text-slate-500">
          One per line. These are masked before matching, so names like &ldquo;Energy + Focus&rdquo; are not read as claims.
        </p>
        <textarea className="input min-h-28 font-mono" value={mask} onChange={(e) => setMask(e.target.value)} />
      </section>

      <section className="card space-y-3 p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Nutrient record</h2>
            <p className="text-sm text-slate-500">Used to verify content claims such as &ldquo;high in protein&rdquo;.</p>
          </div>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setRows([...rows, { nutrient: "", servingQty: "", servingUnit: "", per100Qty: "", per100Unit: "", rdi: "" }])}
          >
            Add nutrient
          </button>
        </div>
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-2 font-medium">Nutrient</th>
                  <th className="py-2 pr-2 font-medium">Per serving</th>
                  <th className="py-2 pr-2 font-medium">Per 100 g / mL</th>
                  <th className="py-2 pr-2 font-medium">% RDI</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="py-1 pr-2">
                      <input className="input" placeholder="protein" value={r.nutrient} onChange={(e) => updateRow(i, { nutrient: e.target.value })} />
                    </td>
                    <td className="py-1 pr-2">
                      <div className="flex gap-1">
                        <input className="input w-24" inputMode="decimal" value={r.servingQty} onChange={(e) => updateRow(i, { servingQty: e.target.value })} />
                        <input className="input w-20" placeholder="g" value={r.servingUnit} onChange={(e) => updateRow(i, { servingUnit: e.target.value })} />
                      </div>
                    </td>
                    <td className="py-1 pr-2">
                      <div className="flex gap-1">
                        <input className="input w-24" inputMode="decimal" value={r.per100Qty} onChange={(e) => updateRow(i, { per100Qty: e.target.value })} />
                        <input className="input w-20" placeholder="g" value={r.per100Unit} onChange={(e) => updateRow(i, { per100Unit: e.target.value })} />
                      </div>
                    </td>
                    <td className="py-1 pr-2">
                      <input className="input w-20" inputMode="decimal" value={r.rdi} onChange={(e) => updateRow(i, { rdi: e.target.value })} />
                    </td>
                    <td className="py-1 text-right">
                      <button type="button" aria-label="Remove nutrient" className="text-slate-400 hover:text-red-600" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card space-y-3 p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Properties</h2>
            <p className="text-sm text-slate-500">
              Named properties (e.g. an ingredient) with regular expressions that find them in a claim, one per line.
            </p>
          </div>
          <button type="button" className="btn-secondary" onClick={() => setProps([...props, { name: "", patterns: "" }])}>
            Add property
          </button>
        </div>
        {props.map((p, i) => (
          <div key={i} className="grid gap-2 rounded-lg border border-slate-200 p-3 sm:grid-cols-[14rem_1fr_auto]">
            <input className="input" placeholder="l-theanine" value={p.name} onChange={(e) => updateProp(i, { name: e.target.value })} />
            <textarea className="input min-h-10 font-mono" placeholder={"theanine\nl-theanine"} value={p.patterns} onChange={(e) => updateProp(i, { patterns: e.target.value })} />
            <button type="button" aria-label="Remove property" className="self-start px-2 py-2 text-slate-400 hover:text-red-600" onClick={() => setProps(props.filter((_, j) => j !== i))}>
              ✕
            </button>
          </div>
        ))}
      </section>

      {error && <ErrorBox message={error} />}

      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={() => router.push("/products")}>Cancel</button>
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy && <Spinner />} {editing ? "Save changes" : "Create product"}
        </button>
      </div>
    </form>
  );
}
