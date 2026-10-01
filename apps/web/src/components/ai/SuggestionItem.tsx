import { useState, type ReactNode } from 'react';
import type { Suggestion, SuggestionType } from '../../api/types';
import { TagChip } from '../TagChip';

const ACCEPT_LABEL: Record<SuggestionType, string> = {
  duplicate: 'Merge duplicate',
  related: 'Confirm related',
  problem: 'Add to problem',
  new_problem: 'Create problem',
  tag: 'Add tag',
  score: 'Accept score',
  brief: 'Post as comment',
  comms: 'Approve draft',
  comment: 'Post comment',
  field_edit: 'Apply edit',
  analysis: 'Acknowledge',
};

/** Fields a person can change before accepting, with how to read them out of the suggestion's data. */
const EDIT_FIELDS: Partial<Record<SuggestionType, { key: string; label: string; multiline?: boolean }[]>> = {
  tag: [{ key: 'name', label: 'Tag name' }],
  brief: [{ key: 'summary', label: 'Summary', multiline: true }],
  comment: [{ key: 'body', label: 'Comment', multiline: true }],
  comms: [
    { key: 'subject', label: 'Subject' },
    { key: 'body', label: 'Message', multiline: true },
  ],
  new_problem: [
    { key: 'title', label: 'Problem title' },
    { key: 'statement', label: 'Statement', multiline: true },
  ],
};

export interface AcceptArgs {
  edits?: Record<string, unknown>;
  override?: { ratings: Record<string, number>; reason: string };
}

interface Props {
  suggestion: Suggestion;
  busy: boolean;
  onAccept: (args: AcceptArgs) => void;
  onReject: () => void;
  onOpenCard: (cardId: number) => void;
}

const percent = (value: number | null) => (value === null ? null : `${Math.round(value * 100)}%`);

export function SuggestionItem({ suggestion, busy, onAccept, onReject, onOpenCard }: Props) {
  const [mode, setMode] = useState<'view' | 'edit' | 'override'>('view');
  const fields = EDIT_FIELDS[suggestion.type] ?? [];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, String(suggestion.data[f.key] ?? '')])),
  );
  const label = describeTitle(suggestion);
  const stale = suggestion.type === 'field_edit' && suggestion.data.stale === true;

  const finishEdit = () => {
    const changed = Object.fromEntries(fields.filter((f) => values[f.key] !== String(suggestion.data[f.key] ?? '')).map((f) => [f.key, values[f.key]]));
    onAccept({ edits: Object.keys(changed).length ? changed : undefined });
    setMode('view');
  };

  return (
    <li className="rounded-md border border-slate-200 bg-white p-3" aria-label={label}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 text-sm">{body(suggestion, onOpenCard)}</div>
        {suggestion.confidence !== null && (
          <span
            className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600"
            title="How sure the AI is"
          >
            {percent(suggestion.confidence)} sure
          </span>
        )}
      </div>
      {suggestion.rationale && <p className="mt-1.5 text-xs text-slate-600">Why: {suggestion.rationale}</p>}
      {stale && (
        <p role="alert" className="mt-1.5 text-xs text-amber-700">
          The card changed after this edit was suggested, so it can no longer be applied. Reject it and run triage again.
        </p>
      )}

      {mode === 'edit' && (
        <form
          className="mt-2 space-y-2 rounded bg-slate-50 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            finishEdit();
          }}
        >
          {fields.map((f) => (
            <div key={f.key}>
              <label className="label" htmlFor={`${suggestion.id}-${f.key}`}>
                {f.label}
              </label>
              {f.multiline ? (
                <textarea id={`${suggestion.id}-${f.key}`} className="input min-h-20" value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
              ) : (
                <input id={`${suggestion.id}-${f.key}`} className="input" value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
              )}
            </div>
          ))}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setMode('view')}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || fields.some((f) => !values[f.key]?.trim() && f.key !== 'statement')}>
              Save and accept
            </button>
          </div>
        </form>
      )}

      {mode === 'override' && <ScoreOverride suggestion={suggestion} busy={busy} onCancel={() => setMode('view')} onSubmit={(o) => { onAccept({ override: o }); setMode('view'); }} />}

      {mode === 'view' && (
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={busy || stale} aria-label={`${ACCEPT_LABEL[suggestion.type]}: ${label}`} onClick={() => onAccept({})}>
            {ACCEPT_LABEL[suggestion.type]}
          </button>
          {fields.length > 0 && (
            <button type="button" className="btn" disabled={busy} aria-label={`Edit: ${label}`} onClick={() => setMode('edit')}>
              Edit
            </button>
          )}
          {suggestion.type === 'score' && (
            <button type="button" className="btn" disabled={busy} aria-label={`Override ratings: ${label}`} onClick={() => setMode('override')}>
              Override…
            </button>
          )}
          <button type="button" className="btn" disabled={busy} aria-label={`Reject: ${label}`} onClick={onReject}>
            Reject
          </button>
        </div>
      )}
    </li>
  );
}

function describeTitle(s: Suggestion): string {
  switch (s.type) {
    case 'duplicate':
      return `Possible duplicate of #${s.data.other_card_id} ${s.data.other_title}`;
    case 'related':
      return `Related to #${s.data.other_card_id} ${s.data.other_title}`;
    case 'problem':
      return `Problem: ${s.data.problem_title}`;
    case 'new_problem':
      return `New problem: ${s.data.title}`;
    case 'tag':
      return `Tag: ${s.data.name}`;
    case 'score':
      return `Priority score ${s.data.total}`;
    case 'brief':
      return 'Decision brief';
    case 'comms':
      return `Message: ${s.data.subject}`;
    case 'comment':
      return 'Comment';
    case 'field_edit':
      return 'Edit card fields';
    default:
      return 'Analysis';
  }
}

function CardLink({ id, title, onOpen }: { id: number; title: string; onOpen: (id: number) => void }) {
  return (
    <button type="button" className="text-left font-medium text-blue-700 underline-offset-2 hover:underline" onClick={() => onOpen(id)}>
      #{id} {title}
    </button>
  );
}

function body(s: Suggestion, onOpenCard: (id: number) => void): ReactNode {
  const d = s.data;
  switch (s.type) {
    case 'duplicate':
      return (
        <>
          <p>
            Possible duplicate of <CardLink id={d.other_card_id} title={d.other_title} onOpen={onOpenCard} />
          </p>
          {d.merge && (
            <p className="mt-1 text-xs text-slate-600">
              Accepting moves {d.merge.votes_on_duplicate} vote(s) from #{d.merge.duplicate_card_id} to #{d.merge.primary_card_id} and keeps the
              two cards linked.
            </p>
          )}
        </>
      );
    case 'related':
      return (
        <p>
          Related to <CardLink id={d.other_card_id} title={d.other_title} onOpen={onOpenCard} />
        </p>
      );
    case 'problem':
      return (
        <p>
          Belongs to the problem <span className="font-medium">{d.problem_title}</span>
        </p>
      );
    case 'new_problem':
      return (
        <>
          <p>
            New problem: <span className="font-medium">{d.title}</span>
          </p>
          {d.statement && <p className="mt-0.5 text-xs text-slate-600">{d.statement}</p>}
        </>
      );
    case 'tag':
      return (
        <p className="flex items-center gap-2">
          <TagChip tag={{ name: d.name, color: '#6B7280' }} />
          {d.is_existing === false && <span className="text-xs text-slate-500">(new tag)</span>}
        </p>
      );
    case 'score':
      return (
        <>
          <p>
            Priority <span className="font-semibold">{d.total}</span>/100 <span className="text-xs text-slate-500">({d.rubric})</span>
          </p>
          <table className="mt-1.5 w-full text-xs">
            <caption className="sr-only">Rating per criterion</caption>
            <thead>
              <tr className="text-left text-slate-500">
                <th className="pr-2 font-medium">Criterion</th>
                <th className="pr-2 font-medium">Rating</th>
                <th className="font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {(d.lines ?? []).map((line: any) => (
                <tr key={line.key} className="align-top">
                  <td className="pr-2 py-0.5">{line.key.replace(/_/g, ' ')}</td>
                  <td className="pr-2 py-0.5 font-medium">{line.rating}/5</td>
                  <td className="py-0.5 text-slate-600">{line.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      );
    case 'brief':
      return (
        <>
          <p>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-semibold uppercase">{d.recommendation}</span> {d.summary}
          </p>
          {d.risks?.length > 0 && <p className="mt-1 text-xs text-slate-600">Risks: {d.risks.join('; ')}</p>}
          {d.open_questions?.length > 0 && <p className="mt-0.5 text-xs text-slate-600">Open questions: {d.open_questions.join('; ')}</p>}
        </>
      );
    case 'comms':
      return (
        <>
          <p className="text-xs text-slate-500">To: {d.audience}</p>
          <p className="font-medium">{d.subject}</p>
          <p className="mt-0.5 whitespace-pre-wrap text-slate-700">{d.body}</p>
        </>
      );
    case 'comment':
      return <p className="whitespace-pre-wrap">{d.body}</p>;
    case 'field_edit':
      return (
        <ul className="text-xs">
          {Object.entries(d.fields ?? {}).map(([k, v]) => (
            <li key={k}>
              <span className="font-medium">{k}</span> → {String(v)}
            </li>
          ))}
        </ul>
      );
    default:
      return (
        <>
          <p>{d.underlying_need}</p>
          {d.clarifying_questions?.length > 0 && <p className="mt-0.5 text-xs text-slate-600">Questions: {d.clarifying_questions.join(' ')}</p>}
        </>
      );
  }
}

/** Change ratings and say why. The reason is required: the audit trail keeps it. */
function ScoreOverride({
  suggestion,
  busy,
  onCancel,
  onSubmit,
}: {
  suggestion: Suggestion;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (override: { ratings: Record<string, number>; reason: string }) => void;
}) {
  const lines: { key: string; rating: number }[] = suggestion.data.lines ?? [];
  const [ratings, setRatings] = useState<Record<string, number>>(() => Object.fromEntries(lines.map((l) => [l.key, l.rating])));
  const [reason, setReason] = useState('');
  const changed = Object.fromEntries(lines.filter((l) => ratings[l.key] !== l.rating).map((l) => [l.key, ratings[l.key] as number]));
  const valid = Object.keys(changed).length > 0 && reason.trim().length >= 3;
  return (
    <form
      className="mt-2 space-y-2 rounded bg-slate-50 p-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSubmit({ ratings: changed, reason: reason.trim() });
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        {lines.map((l) => (
          <div key={l.key}>
            <label className="label" htmlFor={`${suggestion.id}-${l.key}`}>
              {l.key.replace(/_/g, ' ')}
            </label>
            <select id={`${suggestion.id}-${l.key}`} className="input" value={ratings[l.key]} onChange={(e) => setRatings({ ...ratings, [l.key]: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                  {n === l.rating ? ' (AI)' : ''}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
      <div>
        <label className="label" htmlFor={`${suggestion.id}-reason`}>
          Reason for the override (required)
        </label>
        <textarea id={`${suggestion.id}-reason`} className="input min-h-16" value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy || !valid}>
          Save override
        </button>
      </div>
    </form>
  );
}
