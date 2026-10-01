import { useEffect, useRef, useState } from 'react';
import {
  errorText,
  useAcceptSuggestion,
  useAudit,
  useLatestRun,
  useRefreshAfterRun,
  useRejectSuggestion,
  useResumeRun,
  useRun,
  useStartTriage,
  useSuggestions,
} from '../api/hooks';
import { STEP_LABELS, TRIAGE_STEPS, type CardDetail, type Suggestion, type SuggestionType } from '../api/types';
import { formatDateTime } from '../lib/format';
import { useToast } from '../state/toast';
import { CheckpointForm } from './ai/CheckpointForm';
import { RunProgress } from './ai/RunProgress';
import { SuggestionItem, type AcceptArgs } from './ai/SuggestionItem';

const GROUPS: { title: string; types: SuggestionType[] }[] = [
  { title: 'Duplicates and related requests', types: ['duplicate', 'related'] },
  { title: 'Problem grouping', types: ['problem', 'new_problem'] },
  { title: 'Tags', types: ['tag'] },
  { title: 'Priority score', types: ['score'] },
  { title: 'Decision brief', types: ['brief'] },
  { title: 'Messages and other', types: ['comms', 'comment', 'field_edit', 'analysis'] },
];

interface Props {
  card: CardDetail;
  onOpenCard: (id: number) => void;
}

/** Run AI triage on the card, watch it, and decide what to do with what it found. */
export function AiPanel({ card, onOpenCard }: Props) {
  const toast = useToast();
  const latest = useLatestRun(card.id);
  const [startedRunId, setStartedRunId] = useState<number | null>(null);
  const [answering, setAnswering] = useState(false);
  const runId = startedRunId ?? latest.data?.run_id ?? null;
  const run = useRun(runId, answering);
  const refresh = useRefreshAfterRun(card);
  const suggestions = useSuggestions(card.id);
  const start = useStartTriage(card);
  const accept = useAcceptSuggestion(card);
  const reject = useRejectSuggestion(card);
  const resume = useResumeRun(card, runId ?? 0);
  const [showAudit, setShowAudit] = useState(false);
  const audit = useAudit(card.id, showAudit);
  const [busyId, setBusyId] = useState<string | null>(null);

  const status = run.data?.status;
  const active = status === 'running' || status === 'awaiting_human' || start.isPending;

  // New suggestions appear as steps finish; when the run ends or pauses, refresh everything.
  const done = run.data?.progress.done;
  const lastDone = useRef<number | undefined>(undefined);
  const lastStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (done !== lastDone.current || (status && status !== lastStatus.current)) void refresh();
    lastDone.current = done;
    lastStatus.current = status;
    if (status && status !== 'awaiting_human') setAnswering(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, status]);

  const begin = (steps?: string[]) =>
    start.mutate(steps, {
      onSuccess: (accepted) => setStartedRunId(accepted.run_id),
      onError: (e) => toast.error(`Could not start triage: ${errorText(e)}`),
    });

  const decide = (s: Suggestion, args: AcceptArgs | 'reject') => {
    setBusyId(s.id);
    const done = { onSettled: () => setBusyId(null), onError: (e: unknown) => toast.error(errorText(e)) };
    if (args === 'reject') reject.mutate({ sid: s.id }, { ...done, onSuccess: () => toast.info('Suggestion rejected') });
    else accept.mutate({ sid: s.id, ...args }, { ...done, onSuccess: (d) => toast.info(d.message) });
  };

  const pending = suggestions.data ?? [];
  return (
    <section aria-labelledby="ai-heading" className="border-t border-slate-200 px-5 py-4">
      <h3 id="ai-heading" className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        AI triage
      </h3>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary" disabled={active} onClick={() => begin()}>
          {start.isPending ? 'Starting…' : 'Run AI triage'}
        </button>
        <details className="relative">
          <summary className="btn cursor-pointer list-none" aria-label="Run a single step">
            Run one step ▾
          </summary>
          <ul className="absolute left-0 z-10 mt-1 w-48 rounded-md border border-slate-200 bg-white p-1 shadow-lg" aria-label="Steps">
            {TRIAGE_STEPS.map((step) => (
              <li key={step}>
                <button type="button" className="w-full rounded px-2 py-1 text-left text-sm hover:bg-slate-100 disabled:opacity-50" disabled={active} onClick={() => begin([step])}>
                  {STEP_LABELS[step]}
                </button>
              </li>
            ))}
          </ul>
        </details>
        <p className="text-xs text-slate-500">Nothing is applied until you accept it.</p>
      </div>

      {run.isError && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          Could not read the run: {errorText(run.error)}
        </p>
      )}
      {run.data && (
        <div className="mt-3 space-y-3">
          <RunProgress run={run.data} />
          {run.data.status === 'awaiting_human' && run.data.pending_action && !answering && (
            <CheckpointForm
              action={run.data.pending_action}
              busy={resume.isPending}
              onAnswer={(decision) =>
                resume.mutate(decision, {
                  onSuccess: () => setAnswering(true),
                  onError: (e) => toast.error(errorText(e)),
                })
              }
            />
          )}
        </div>
      )}

      <div className="mt-4">
        <h4 className="text-sm font-semibold">AI Suggestions {pending.length > 0 && <span className="text-slate-500">({pending.length})</span>}</h4>
        {suggestions.isLoading && <p className="mt-1 text-sm text-slate-500">Loading…</p>}
        {suggestions.isSuccess && pending.length === 0 && (
          <p className="mt-1 text-sm text-slate-400">No suggestions waiting. Run AI triage to get some.</p>
        )}
        {GROUPS.map((group) => {
          const items = pending.filter((s) => group.types.includes(s.type));
          if (!items.length) return null;
          return (
            <div key={group.title} className="mt-3">
              <h5 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{group.title}</h5>
              <ul className="space-y-2">
                {items.map((s) => (
                  <SuggestionItem
                    key={s.id}
                    suggestion={s}
                    busy={busyId === s.id}
                    onAccept={(args) => decide(s, args)}
                    onReject={() => decide(s, 'reject')}
                    onOpenCard={onOpenCard}
                  />
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      <div className="mt-4">
        <button type="button" className="text-xs text-slate-600 underline-offset-2 hover:underline" aria-expanded={showAudit} onClick={() => setShowAudit((v) => !v)}>
          {showAudit ? 'Hide' : 'Show'} audit trail
        </button>
        {showAudit && (
          <ul className="mt-2 space-y-1 text-xs text-slate-600" aria-label="Audit trail">
            {audit.data?.map((e) => (
              <li key={e.id}>
                <span className="font-medium">{e.action.replace(/_/g, ' ')}</span>
                {e.detail.type ? ` (${e.detail.type})` : ''}
                {e.detail.reason ? `: ${e.detail.reason}` : ''} · {formatDateTime(e.created_at)}
              </li>
            ))}
            {audit.data?.length === 0 && <li>No decisions yet.</li>}
          </ul>
        )}
      </div>
    </section>
  );
}
