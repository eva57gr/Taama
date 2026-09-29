"use client";

import { useEffect, useMemo, useState } from "react";
import { CitationView } from "@/components/Results";
import { ErrorBox, PageHeader, Spinner, VerdictBadge } from "@/components/ui";
import { api, type Rule, type Source } from "@/lib/api";

export default function RulesPage() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.rules(), api.sources()])
      .then(([r, s]) => {
        setRules(r);
        setSources(s);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (rules ?? []).filter(
      (r) => !q || r.id.toLowerCase().includes(q) || r.title.toLowerCase().includes(q) || r.why.toLowerCase().includes(q),
    );
  }, [rules, query]);

  return (
    <div className="space-y-8">
      <PageHeader title="Rules & sources" subtitle="Every verdict traces back to one of these rules and a snapshot of the official text.">
        <input className="input w-72" placeholder="Search rules" value={query} onChange={(e) => setQuery(e.target.value)} />
      </PageHeader>
      {error && <ErrorBox message={error} />}
      {!rules && !error && (
        <p className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</p>
      )}

      {sources.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Sources</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {sources.map((s) => (
              <div key={s.id} className="card p-4 text-sm">
                <p className="font-medium text-slate-900">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-700 hover:underline">{s.title}</a>
                  ) : (
                    s.title
                  )}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {[s.register_id, s.compilation && `compilation ${s.compilation}`, s.in_force_from && `in force from ${s.in_force_from}`, s.retrieved && `retrieved ${s.retrieved}`]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {rules && (
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Rules ({shown.length})</h2>
          <div className="space-y-3">
            {shown.map((r) => (
              <div key={r.id} className="card p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <VerdictBadge verdict={r.verdict} />
                  <code className="text-xs font-semibold text-slate-700">{r.id}</code>
                  <span className="font-medium text-slate-900">{r.title}</span>
                </div>
                <p className="mt-2 text-slate-600">{r.why}</p>
                {r.citations.length > 0 && (
                  <div className="mt-3 space-y-2">
                    {r.citations.map((c, i) => (
                      <CitationView key={i} c={c} />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
