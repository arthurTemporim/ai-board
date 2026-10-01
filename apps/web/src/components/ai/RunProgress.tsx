import { STEP_LABELS, type Run } from '../../api/types';

const SYMBOL: Record<string, { mark: string; tone: string; label: string }> = {
  pending: { mark: '○', tone: 'text-slate-400', label: 'waiting' },
  running: { mark: '◐', tone: 'text-blue-600', label: 'running' },
  succeeded: { mark: '✓', tone: 'text-emerald-600', label: 'done' },
  failed: { mark: '✗', tone: 'text-red-600', label: 'failed' },
  skipped: { mark: '–', tone: 'text-slate-400', label: 'skipped' },
  awaiting_human: { mark: '⏸', tone: 'text-amber-600', label: 'waiting for you' },
};

const OUTCOME: Record<string, string> = {
  needs_info: 'This request is too vague to triage. Questions for the requester were drafted below.',
  duplicate_proposed: 'This looks like a duplicate. Review the merge suggestion below.',
  failed: 'The run failed.',
};

/** Where the run is: a progress bar, one chip per step, and anything that went wrong. */
export function RunProgress({ run }: { run: Run }) {
  const { done, total } = run.progress;
  const visible = run.steps.filter((s) => s.status !== 'not_requested' && !(s.route && s.status === 'skipped'));
  const notes = run.steps.filter(
    (s) => (s.status === 'failed' && s.error) || (s.status === 'skipped' && s.reason?.match(/dependency|missing/)),
  );
  const heading =
    run.status === 'running'
      ? `Running: ${done} of ${total} steps`
      : run.status === 'awaiting_human'
        ? 'Paused: waiting for your answer'
        : run.status === 'failed'
          ? 'Run failed'
          : `Finished: ${done} of ${total} steps`;

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3" role="status" aria-live="polite" aria-label="AI triage progress">
      <div className="flex items-center justify-between text-xs font-medium text-slate-600">
        <span>{heading}</span>
        <span>
          {done}/{total}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Triage steps completed"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200"
      >
        <div
          className={`h-full rounded-full transition-all ${run.status === 'failed' ? 'bg-red-500' : 'bg-blue-500'}`}
          style={{ width: `${total ? (done / total) * 100 : 0}%` }}
        />
      </div>
      <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Steps">
        {visible.map((step) => {
          const symbol = SYMBOL[step.status] ?? SYMBOL.pending!;
          return (
            <li
              key={`${step.name}-${step.status}`}
              className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-xs ring-1 ring-slate-200"
              title={step.error ?? step.reason ?? undefined}
            >
              <span aria-hidden className={symbol.tone}>
                {symbol.mark}
              </span>
              <span>{STEP_LABELS[step.name] ?? step.name}</span>
              <span className="sr-only">: {symbol.label}</span>
            </li>
          );
        })}
      </ul>
      {run.outcome && OUTCOME[run.outcome] && <p className="mt-2 text-xs text-slate-600">{OUTCOME[run.outcome]}</p>}
      {notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-red-700">
          {notes.map((s) => (
            <li key={s.name}>
              <span className="font-medium">{STEP_LABELS[s.name] ?? s.name}:</span> {s.error ?? s.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
