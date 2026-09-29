// Default: same host the page was opened on, port 8000 (works for localhost and LAN access).
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:8000` : "http://127.0.0.1:8000")
).replace(/\/$/, "");

export type Verdict = "RED" | "AMBER" | "GREEN";
export type Status = "FAIL" | "PASS" | "CONDITIONS" | "NEEDS_REVIEW";

export interface Regime {
  id: string;
  label: string;
}

export interface ProductSummary {
  id: string;
  name: string;
  regime: string;
  regime_label: string;
}

export interface RecordEntry {
  per_serving?: [number, string];
  per_100?: [number, string];
  rdi_pct?: number;
}

export interface Product {
  id: string;
  name: string;
  market?: string;
  regime: string;
  regime_basis?: { reason: string; cite: unknown };
  inputs?: string[];
  mask: string[];
  business?: string | null;
  notified_source?: string | null;
  record_source?: string;
  serving?: unknown;
  liquid: boolean;
  record: Record<string, RecordEntry>;
  properties: Record<string, string[]>;
  npsc: boolean | null;
  review_notes?: unknown[];
}

export interface Citation {
  source: string;
  document: string;
  locator: string;
  excerpt: string;
  snapshot?: string;
  snapshot_line?: number;
  url?: string;
}

export interface Condition {
  text: string;
  status: string;
  evidence: string;
  citation?: Citation | null;
}

export interface RuleHit {
  rule_id: string;
  verdict: Verdict;
  title: string;
  why: string;
  matched: string[];
  citations: Citation[];
  conditions?: Condition[];
  source?: "rules" | "ai";
}

export interface Claim {
  id: string;
  text: string;
  claim_type: string;
  verdict: Verdict;
  status: Status;
  justification: string;
  rules: RuleHit[];
  decided_by?: "rules" | "ai" | "both";
  found_in: { file: string; location: string; method: string }[];
}

export interface CheckResult {
  product: { id: string; name: string; regime: string; regime_label: string };
  summary: { RED: number; AMBER: number; GREEN: number; claims: number; needs_review: number; ai?: boolean | null };
  claims: Claim[];
  not_assessed: { text: string; file: string; location: string; reason: string }[];
  not_assessed_total: number;
}

export interface Rule {
  id: string;
  verdict: Verdict;
  title: string;
  why: string;
  citations: Citation[];
}

export interface Source {
  id: string;
  title: string;
  register_id?: string;
  compilation?: string;
  in_force_from?: string;
  retrieved?: string;
  url?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new Error(`Cannot reach the API at ${API_URL}. Is the backend running (cd backend; uvicorn api:app --host 0.0.0.0)?`);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const msg = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText;
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return body as T;
}

const json = (method: string, data: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const api = {
  regimes: () => request<{ regimes: Regime[] }>("/api/regimes").then((r) => r.regimes),
  products: () => request<{ products: ProductSummary[] }>("/api/products").then((r) => r.products),
  product: (id: string) => request<Product>(`/api/products/${encodeURIComponent(id)}`),
  createProduct: (p: Product) => request<Product>("/api/products", json("POST", p)),
  updateProduct: (id: string, p: Product) => request<Product>(`/api/products/${encodeURIComponent(id)}`, json("PUT", p)),
  deleteProduct: (id: string) => request<void>(`/api/products/${encodeURIComponent(id)}`, { method: "DELETE" }),
  rules: () => request<{ rules: Rule[] }>("/api/rules").then((r) => r.rules),
  sources: () => request<{ sources: Source[] }>("/api/sources").then((r) => r.sources),
  check: (form: FormData) => request<CheckResult>("/api/check", { method: "POST", body: form }),
};
