"use client";

import { useMemo, useState } from "react";
import type { CheckResult, Citation, Claim, Verdict } from "@/lib/api";
import { StatusBadge, VerdictBadge } from "./ui";

const FILTERS: ("ALL" | Verdict | "REVIEW")[] = ["ALL", "RED", "AMBER", "GREEN", "REVIEW"];

export default function Results({ result }: { result: CheckResult }) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("ALL");
  const [query, setQuery] = useState("");
  const { summary } = result;

  const claims = useMemo(() => {
    const q = query.trim().toLowerCase();
    return result.claims.filter((c) => {
      if (filter === "REVIEW" ? c.status !== "NEEDS_REVIEW" : filter !== "ALL" && c.verdict !== filter) return false;
      return !q || c.text.toLowerCase().includes(q) || c.rules.some((r) => r.rule_id.toLowerCase().includes(q));
    });
  }, [result.claims, filter, query]);

  const count = (f: (typeof FILTERS)[number]) =>
    f === "ALL" ? summary.claims : f === "REVIEW" ? summary.needs_review : summary[f];

  return (
    <section className="space-y-4">
      <div className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{result.product.name}</h2>
            <p className="text-sm text-slate-500">{result.product.regime_label}</p>
          </div>
          <p className="text-sm text-slate-500">
            {summary.claims} claims assessed · AI judge {summary.ai ? "on" : "off"}
          </p>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Red" value={summary.RED} className="border-red-200 bg-red-50 text-red-700" />
          <Stat label="Amber" value={summary.AMBER} className="border-amber-200 bg-amber-50 text-amber-800" />
          <Stat label="Green" value={summary.GREEN} className="border-emerald-200 bg-emerald-50 text-emerald-700" />
          <Stat label="Needs review" value={summary.needs_review} className="border-slate-200 bg-slate-50 text-slate-700" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`rounded-full px-3 py-1 text-sm font-medium transition ${
              filter === f ? "bg-slate-900 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {f === "ALL" ? "All" : f === "REVIEW" ? "Needs review" : f[0] + f.slice(1).toLowerCase()} ({count(f)})
          </button>
        ))}
        <input
          className="input ml-auto max-w-xs"
          placeholder="Search claims or rule ids"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="space-y-3">
        {claims.map((c) => (
          <ClaimCard key={c.id} claim={c} />
        ))}
        {claims.length === 0 && (
          <p className="card p-6 text-center text-sm text-slate-500">No claims match this filter.</p>
        )}
      </div>

      {result.not_assessed_total > 0 && (
        <details className="card p-5">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            Text not assessed as a claim ({result.not_assessed_total})
          </summary>
          <ul className="mt-3 divide-y divide-slate-100 text-sm">
            {result.not_assessed.map((d, i) => (
              <li key={i} className="flex flex-wrap justify-between gap-2 py-2">
                <span className="text-slate-800">{d.text}</span>
                <span className="text-slate-500">
                  {d.reason}
                  {d.file ? ` · ${d.file}` : ""}
                </span>
              </li>
            ))}
          </ul>
          {result.not_assessed_total > result.not_assessed.length && (
            <p className="mt-2 text-xs text-slate-500">
              Showing the first {result.not_assessed.length} of {result.not_assessed_total}.
            </p>
          )}
        </details>
      )}
    </section>
  );
}

function Stat({ label, value, className }: { label: string; value: number; className: string }) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${className}`}>
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs font-medium uppercase tracking-wide opacity-80">{label}</div>
    </div>
  );
}

const BORDER: Record<Verdict, string> = {
  RED: "border-l-red-500",
  AMBER: "border-l-amber-500",
  GREEN: "border-l-emerald-500",
};

function ClaimCard({ claim }: { claim: Claim }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={`card border-l-4 ${BORDER[claim.verdict]}`}>
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 p-4 text-left">
        <div className="flex shrink-0 flex-col gap-1 pt-0.5">
          <VerdictBadge verdict={claim.verdict} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-900">&ldquo;{claim.text}&rdquo;</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <StatusBadge status={claim.status} />
            {claim.decided_by && <SourceBadge source={claim.decided_by} />}
            <span>{claim.claim_type.replaceAll("_", " ")}</span>
            {[...new Set(claim.rules.map((r) => r.rule_id))].map((id) => (
              <code key={id} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                {id}
              </code>
            ))}
          </div>
        </div>
        <span className={`mt-1 text-slate-400 transition ${open ? "rotate-180" : ""}`} aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div className="space-y-4 border-t border-slate-100 px-4 pb-4 pt-3 text-sm">
          <p className="leading-relaxed text-slate-700">{claim.justification}</p>
          {claim.rules.map((r, i) => (
            <div key={`${r.rule_id}-${i}`} className="rounded-lg bg-slate-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <VerdictBadge verdict={r.verdict} />
                <SourceBadge source={r.source ?? "rules"} />
                <code className="text-xs font-semibold text-slate-700">{r.rule_id}</code>
                <span className="font-medium text-slate-800">{r.title}</span>
              </div>
              {r.matched.length > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  Matched: {r.matched.map((m) => `“${m}”`).join(", ")}
                </p>
              )}
              {r.conditions && r.conditions.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {r.conditions.map((c, i) => (
                    <li key={i} className="flex gap-2">
                      <ConditionIcon status={c.status} />
                      <div>
                        <p className="text-slate-800">{c.text}</p>
                        <p className="text-xs text-slate-500">{c.evidence}</p>
                        {c.citation && <CitationView c={c.citation} compact />}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {r.citations.length > 0 && (
                <div className="mt-3 space-y-2">
                  {r.citations.map((c, i) => (
                    <CitationView key={i} c={c} />
                  ))}
                </div>
              )}
            </div>
          ))}
          {claim.found_in.length > 0 && (
            <div className="text-xs text-slate-500">
              <p className="font-medium text-slate-600">Found in</p>
              <ul className="mt-1 space-y-0.5">
                {claim.found_in.map((f, i) => (
                  <li key={i}>
                    <span className="text-slate-700">{f.file}</span> · {f.location} · {f.method}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function SourceBadge({ source }: { source: "rules" | "ai" | "both" }) {
  const style =
    source === "ai"
      ? "bg-violet-100 text-violet-700"
      : source === "both"
        ? "bg-indigo-100 text-indigo-700"
        : "bg-slate-200 text-slate-700";
  const label = source === "ai" ? "AI" : source === "both" ? "AI + Rules" : "Rules";
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${style}`}>{label}</span>;
}

function ConditionIcon({ status }: { status: string }) {
  const met = status === "met";
  const notMet = status === "not_met";
  return (
    <span
      title={status.replaceAll("_", " ")}
      className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-xs font-bold ${
        met ? "bg-emerald-100 text-emerald-700" : notMet ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-700"
      }`}
    >
      {met ? "✓" : notMet ? "✕" : "?"}
    </span>
  );
}

export function CitationView({ c, compact = false }: { c: Citation; compact?: boolean }) {
  return (
    <blockquote className={`border-l-2 border-indigo-200 pl-3 ${compact ? "mt-1" : ""}`}>
      <p className="text-xs text-slate-600">
        {c.url ? (
          <a href={c.url} target="_blank" rel="noopener noreferrer" className="font-medium text-indigo-700 hover:underline">
            {c.document}
          </a>
        ) : (
          <span className="font-medium text-slate-700">{c.document}</span>
        )}
        {c.locator && <span> · {c.locator}</span>}
      </p>
      {c.excerpt && <p className="mt-0.5 text-xs italic text-slate-500">&ldquo;{c.excerpt}&rdquo;</p>}
    </blockquote>
  );
}
