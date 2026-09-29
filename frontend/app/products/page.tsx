"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, type ProductSummary } from "@/lib/api";

export default function ProductsPage() {
  const [products, setProducts] = useState<ProductSummary[] | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    api
      .products()
      .then(setProducts)
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  async function remove(p: ProductSummary) {
    if (!confirm(`Delete product "${p.name}"? This removes products/${p.id}.yaml.`)) return;
    try {
      await api.deleteProduct(p.id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div>
      <PageHeader title="Products" subtitle="Product profiles set the regime, brand names to ignore, nutrient record and properties.">
        <Link href="/products/new" className="btn-primary">New product</Link>
      </PageHeader>
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {!products && !error && (
        <p className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</p>
      )}
      {products && products.length === 0 && (
        <div className="card p-10 text-center">
          <p className="text-slate-600">No products yet.</p>
          <Link href="/products/new" className="btn-primary mt-4">Create your first product</Link>
        </div>
      )}
      {products && products.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Product</th>
                <th className="px-4 py-3 font-medium">Regime</th>
                <th className="px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {products.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">{p.name}</div>
                    <code className="text-xs text-slate-500">{p.id}</code>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{p.regime_label}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Link href={`/?product=${encodeURIComponent(p.id)}`} className="btn-secondary py-1.5">Check</Link>
                      <Link href={`/products/${encodeURIComponent(p.id)}`} className="btn-secondary py-1.5">Edit</Link>
                      <button type="button" onClick={() => remove(p)} className="btn-danger py-1.5">Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
