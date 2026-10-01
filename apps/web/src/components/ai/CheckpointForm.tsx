import { useState } from 'react';
import type { PendingAction } from '../../api/types';

type Verdict = 'duplicate' | 'related' | 'distinct';
const VERDICTS: { value: Verdict; label: string }[] = [
  { value: 'duplicate', label: 'Duplicate' },
  { value: 'related', label: 'Related' },
  { value: 'distinct', label: 'Different' },
];

interface Props {
  action: PendingAction;
  busy: boolean;
  onAnswer: (decision: Record<string, unknown>) => void;
}

/** The question a paused run is waiting on. The AI was not sure enough to decide this itself. */
export function CheckpointForm({ action, busy, onAnswer }: Props) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3" role="group" aria-label="The AI needs your decision">
      <p className="text-sm font-medium text-amber-900">The AI was not sure enough to decide this, so it is asking you.</p>
      {action.error && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {action.error}
        </p>
      )}
      {action.kind === 'confirm_actionable' ? (
        <ActionableQuestion action={action} busy={busy} onAnswer={onAnswer} />
      ) : (
        <DuplicateQuestion action={action} busy={busy} onAnswer={onAnswer} />
      )}
    </div>
  );
}

function ActionableQuestion({ action, busy, onAnswer }: Props) {
  const analysis = action.payload.analysis ?? {};
  return (
    <div className="mt-2 space-y-2 text-sm">
      <p>
        <span className="font-medium">Underlying need:</span> {analysis.underlying_need}
      </p>
      <p className="text-xs text-slate-600">
        Confidence {Math.round((analysis.confidence ?? 0) * 100)}%. Is this request clear enough to triage?
      </p>
      <div className="flex gap-2">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onAnswer({ actionable: true })}>
          Yes, it is clear enough
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => onAnswer({ actionable: false })}>
          No, ask for more detail
        </button>
      </div>
    </div>
  );
}

function DuplicateQuestion({ action, busy, onAnswer }: Props) {
  const candidates: { card_id: number; title: string; similarity: number; confidence: number; rationale: string }[] =
    action.payload.candidates ?? [];
  const [verdicts, setVerdicts] = useState<Record<number, Verdict>>({});
  const complete = candidates.every((c) => verdicts[c.card_id]);
  return (
    <form
      className="mt-2 space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (complete) onAnswer({ decisions: candidates.map((c) => ({ card_id: c.card_id, verdict: verdicts[c.card_id] })) });
      }}
    >
      {candidates.map((c) => (
        <fieldset key={c.card_id} className="rounded border border-amber-200 bg-white p-2">
          <legend className="px-1 text-sm font-medium">
            #{c.card_id} {c.title}
          </legend>
          <p className="text-xs text-slate-600">
            The AI thinks this is a duplicate ({Math.round(c.confidence * 100)}% sure): {c.rationale}
          </p>
          <div className="mt-1.5 flex gap-3">
            {VERDICTS.map((v) => (
              <label key={v.value} className="inline-flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name={`verdict-${c.card_id}`}
                  value={v.value}
                  checked={verdicts[c.card_id] === v.value}
                  onChange={() => setVerdicts((current) => ({ ...current, [c.card_id]: v.value }))}
                />
                {v.label}
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <button type="submit" className="btn btn-primary" disabled={busy || !complete}>
        Send answer
      </button>
    </form>
  );
}
