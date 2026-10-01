import { errorText, useMetrics } from '../api/hooks';
import type { Metrics } from '../api/types';

const pct = (v: number | null) => (v === null ? 'no data yet' : `${Math.round(v * 1000) / 10}%`);
const mins = (v: number | null) => (v === null ? 'no data yet' : v < 1 ? 'under a minute' : `${v} min`);

function Tile({ title, value, detail, definition }: { title: string; value: string; detail: string; definition: string }) {
  return (
    <section aria-label={title} className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-sm text-slate-600">{detail}</p>
      <p className="mt-2 text-xs text-slate-500">{definition}</p>
    </section>
  );
}

/** What the AI triage workflow has actually done, computed from recorded human decisions. */
export function MetricsView({ metrics: m }: { metrics: Metrics }) {
  const t = m.time_to_first_decision;
  const byType = Object.entries(m.acceptance.by_type);
  return (
    <div className="mx-auto max-w-5xl space-y-5 p-5">
      <header>
        <h1 className="text-xl font-semibold">Triage metrics</h1>
        <p className="text-sm text-slate-600">Computed from {m.total_decisions} recorded decision(s) on AI suggestions. Nothing here is estimated except the baseline below.</p>
      </header>

      {m.demo_entries > 0 && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          Includes {m.demo_entries} demo decision(s) created by <code>make demo</code> so this page has something to show. They are not real usage.
        </p>
      )}
      {m.total_decisions === 0 && (
        <p role="note" className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
          No decisions have been recorded yet. Run AI triage on a card and accept or reject its suggestions, or run <code>make demo</code> for sample data.
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Tile
          title="Time to first decision"
          value={mins(t.median_minutes)}
          detail={t.cards ? `median over ${t.cards} card(s); mean ${mins(t.mean_minutes)}; slowest ${mins(t.slowest_minutes)}` : 'No card has a decision yet'}
          definition={m.definitions.time_to_first_decision!}
        />
        <Tile
          title="Suggestion acceptance"
          value={pct(m.acceptance.rate)}
          detail={`${m.acceptance.accepted} accepted, ${m.acceptance.rejected} rejected; ${pct(m.acceptance.accepted_without_changes_rate)} accepted without changes`}
          definition={m.definitions.acceptance!}
        />
        <Tile
          title="Duplicates consolidated"
          value={String(m.duplicates.merged)}
          detail={`${m.duplicates.votes_moved} vote(s) moved to primary cards; ${m.duplicates.duplicates_rejected} duplicate suggestion(s) rejected`}
          definition={m.definitions.duplicates!}
        />
        <Tile
          title="Score override rate"
          value={pct(m.score_overrides.rate)}
          detail={`${m.score_overrides.overridden} of ${m.score_overrides.kept} accepted score(s) were changed; ${m.score_overrides.scores_rejected} rejected outright`}
          definition={m.definitions.score_overrides!}
        />
      </div>

      {byType.length > 0 && (
        <section aria-label="Acceptance by type" className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold">Acceptance by suggestion type</h2>
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th className="py-1 font-medium">Type</th>
                <th className="py-1 font-medium">Accepted</th>
                <th className="py-1 font-medium">Rejected</th>
                <th className="py-1 font-medium">Rate</th>
              </tr>
            </thead>
            <tbody>
              {byType.map(([type, row]) => (
                <tr key={type} className="border-t border-slate-100">
                  <td className="py-1">{type.replace(/_/g, ' ')}</td>
                  <td className="py-1 tabular-nums">{row.accepted}</td>
                  <td className="py-1 tabular-nums">{row.rejected}</td>
                  <td className="py-1 tabular-nums">{pct(row.rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section aria-label="Assumptions" className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4">
        <h2 className="text-sm font-semibold">Assumed manual baseline (not measured)</h2>
        <p className="mt-1 text-sm text-slate-700">{m.baseline.description}</p>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-slate-500">Manual triage, assumed</dt>
          <dd className="font-medium">{m.baseline.manual_triage_minutes} min per card</dd>
          <dt className="text-slate-500">With AI triage, measured</dt>
          <dd className="font-medium">
            {mins(t.median_minutes)} median from creation to first decision <span className="font-normal text-slate-500">(elapsed time, includes waiting)</span>
          </dd>
        </dl>
        <p className="mt-2 text-xs text-slate-500">The two lines are not like for like and are shown side by side only for orientation. Do not read the gap as time saved.</p>
      </section>
    </div>
  );
}

export function MetricsPage() {
  const metrics = useMetrics();
  if (metrics.isLoading) return <p className="m-5 text-sm text-slate-500">Loading metrics…</p>;
  if (metrics.isError) {
    return (
      <p role="alert" className="m-5 rounded-md bg-red-50 p-3 text-sm text-red-700">
        Could not load metrics: {errorText(metrics.error)}
      </p>
    );
  }
  return <MetricsView metrics={metrics.data as Metrics} />;
}
