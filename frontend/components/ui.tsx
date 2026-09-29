import type { Status, Verdict } from "@/lib/api";

const VERDICT_STYLE: Record<Verdict, string> = {
  RED: "bg-red-100 text-red-700 ring-red-600/20",
  AMBER: "bg-amber-100 text-amber-800 ring-amber-600/20",
  GREEN: "bg-emerald-100 text-emerald-700 ring-emerald-600/20",
};

const STATUS_LABEL: Record<Status, string> = {
  FAIL: "Fail",
  PASS: "Pass",
  CONDITIONS: "Conditions",
  NEEDS_REVIEW: "Needs review",
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${VERDICT_STYLE[verdict]}`}>
      {verdict}
    </span>
  );
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent ${className}`}
    />
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}
