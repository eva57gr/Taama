"use client";

import { use, useEffect, useState } from "react";
import ProductForm from "@/components/ProductForm";
import { ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, type Product } from "@/lib/api";

export default function EditProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.product(id).then(setProduct).catch((e: Error) => setError(e.message));
  }, [id]);

  return (
    <div>
      <PageHeader title="Edit product" subtitle={product?.name} />
      {error && <ErrorBox message={error} />}
      {!product && !error && (
        <p className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</p>
      )}
      {product && <ProductForm initial={product} />}
    </div>
  );
}
