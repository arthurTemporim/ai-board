import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { PendingAction, Run, Suggestion } from '../../api/types';
import { CheckpointForm } from './CheckpointForm';
import { RunProgress } from './RunProgress';
import { SuggestionItem } from './SuggestionItem';

const base = { status: 'suggested', created_at: '2026-07-02T21:07:53Z', run_id: 1, editable: [] as string[], rationale: null, confidence: 0.82 };

const duplicate: Suggestion = {
  ...base,
  id: 'link:5',
  type: 'duplicate',
  rationale: 'Both want login through the company identity provider.',
  data: { other_card_id: 1, other_title: 'Support SAML single sign-on', merge: { primary_card_id: 1, duplicate_card_id: 9, votes_on_duplicate: 3 } },
};
const score: Suggestion = {
  ...base,
  id: 'draft:7',
  type: 'score',
  status: 'pending',
  confidence: 0.7,
  data: {
    total: 59.38,
    rubric: 'default v1',
    lines: [
      { key: 'customer_impact', rating: 4, rationale: 'Finance teams need it every month.' },
      { key: 'effort_fit', rating: 5, rationale: 'A table export is small.' },
    ],
  },
};
const tag: Suggestion = { ...base, id: 'draft:3', type: 'tag', status: 'pending', editable: ['name'], rationale: 'About sign-on.', data: { name: 'sso', is_existing: false } };

function setup(suggestion: Suggestion, busy = false) {
  const handlers = { onAccept: vi.fn(), onReject: vi.fn(), onOpenCard: vi.fn() };
  render(<ul><SuggestionItem suggestion={suggestion} busy={busy} {...handlers} /></ul>);
  return handlers;
}

describe('SuggestionItem', () => {
  it('shows a duplicate with confidence, reasoning and what accepting will do', async () => {
    const h = setup(duplicate);
    expect(screen.getByText('82% sure')).toBeInTheDocument();
    expect(screen.getByText(/Both want login through the company identity provider/)).toBeInTheDocument();
    expect(screen.getByText(/moves 3 vote\(s\) from #9 to #1 and keeps the two cards linked/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '#1 Support SAML single sign-on' }));
    expect(h.onOpenCard).toHaveBeenCalledWith(1);
    await userEvent.click(screen.getByRole('button', { name: /^Merge duplicate:/ }));
    expect(h.onAccept).toHaveBeenCalledWith({});
    await userEvent.click(screen.getByRole('button', { name: /^Reject:/ }));
    expect(h.onReject).toHaveBeenCalledTimes(1);
  });

  it('shows the score per criterion with its rationale', () => {
    setup(score);
    const table = screen.getByRole('table', { name: 'Rating per criterion' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      'customer impact4/5Finance teams need it every month.',
      'effort fit5/5A table export is small.',
    ]);
    expect(screen.getByText('59.38')).toBeInTheDocument();
  });

  it('lets you edit before accepting and only sends what changed', async () => {
    const h = setup(tag);
    await userEvent.click(screen.getByRole('button', { name: /^Edit:/ }));
    const input = screen.getByLabelText('Tag name');
    expect(input).toHaveValue('sso');
    await userEvent.clear(input);
    await userEvent.type(input, 'single-sign-on');
    await userEvent.click(screen.getByRole('button', { name: 'Save and accept' }));
    expect(h.onAccept).toHaveBeenCalledWith({ edits: { name: 'single-sign-on' } });
  });

  it('accepts an unchanged edit form as a plain accept', async () => {
    const h = setup(tag);
    await userEvent.click(screen.getByRole('button', { name: /^Edit:/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Save and accept' }));
    expect(h.onAccept).toHaveBeenCalledWith({ edits: undefined });
  });

  it('requires a reason and a changed rating to override a score', async () => {
    const h = setup(score);
    await userEvent.click(screen.getByRole('button', { name: /^Override ratings:/ }));
    const save = screen.getByRole('button', { name: 'Save override' });
    expect(save).toBeDisabled(); // nothing changed yet

    await userEvent.selectOptions(screen.getByLabelText('effort fit'), '2');
    expect(save).toBeDisabled(); // changed, but no reason
    await userEvent.type(screen.getByLabelText(/Reason for the override/), 'Needs a migration');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(h.onAccept).toHaveBeenCalledWith({ override: { ratings: { effort_fit: 2 }, reason: 'Needs a migration' } });
  });

  it('only offers Edit where the server allows it and Override only for scores', () => {
    setup(duplicate);
    expect(screen.queryByRole('button', { name: /^Edit:/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Override/ })).not.toBeInTheDocument();
  });

  it('disables the buttons while a decision is being saved', () => {
    setup(duplicate, true);
    for (const button of screen.getAllByRole('button').filter((b) => /Merge|Reject/.test(b.textContent ?? ''))) expect(button).toBeDisabled();
  });

  it('warns that a stale field edit cannot be applied', () => {
    setup({ ...base, id: 'draft:9', type: 'field_edit', status: 'pending', data: { fields: { title: 'New' }, stale: true } });
    expect(screen.getByRole('alert')).toHaveTextContent(/card changed/i);
    expect(screen.getByRole('button', { name: /^Apply edit/ })).toBeDisabled();
  });
});

const candidate = { card_id: 4, title: 'Azure sign-in', similarity: 0.49, confidence: 0.65, rationale: 'Same login need.' };
const duplicateQuestion: PendingAction = { kind: 'confirm_duplicate', run_id: 2, card_id: 2, payload: { candidates: [candidate, { ...candidate, card_id: 6, title: 'Okta login' }] } };

describe('CheckpointForm', () => {
  it('asks for a verdict on every candidate before it can be sent', async () => {
    const onAnswer = vi.fn();
    render(<CheckpointForm action={duplicateQuestion} busy={false} onAnswer={onAnswer} />);
    expect(screen.getByText(/AI was not sure enough/)).toBeInTheDocument();
    expect(screen.getAllByText(/65% sure\): Same login need/)).toHaveLength(2); // one per candidate
    const send = screen.getByRole('button', { name: 'Send answer' });
    expect(send).toBeDisabled();

    const [first, second] = screen.getAllByRole('group').slice(1);
    await userEvent.click(within(first as HTMLElement).getByLabelText('Duplicate'));
    expect(send).toBeDisabled();
    await userEvent.click(within(second as HTMLElement).getByLabelText('Different'));
    await userEvent.click(send);
    expect(onAnswer).toHaveBeenCalledWith({ decisions: [{ card_id: 4, verdict: 'duplicate' }, { card_id: 6, verdict: 'distinct' }] });
  });

  it('asks whether a request is clear enough', async () => {
    const onAnswer = vi.fn();
    const action: PendingAction = { kind: 'confirm_actionable', run_id: 1, card_id: 1, payload: { analysis: { underlying_need: 'Offline access', confidence: 0.3 } } };
    render(<CheckpointForm action={action} busy={false} onAnswer={onAnswer} />);
    expect(screen.getByText(/Offline access/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /No, ask for more detail/ }));
    expect(onAnswer).toHaveBeenCalledWith({ actionable: false });
    await userEvent.click(screen.getByRole('button', { name: /Yes, it is clear enough/ }));
    expect(onAnswer).toHaveBeenLastCalledWith({ actionable: true });
  });

  it('shows why a previous answer was not accepted', () => {
    render(<CheckpointForm action={{ ...duplicateQuestion, error: 'no decision for card(s) [6]' }} busy={false} onAnswer={() => {}} />);
    expect(screen.getByRole('alert')).toHaveTextContent('no decision for card(s) [6]');
  });
});

const step = (name: string, status: string, extra: Partial<Run['steps'][number]> = {}) => ({
  name, status, requested: true, started_at: null, finished_at: null, latency_ms: null, result: null, error: null, reason: null, ...extra,
});
const run = (overrides: Partial<Run>): Run => ({
  run_id: 1, card_id: 1, status: 'running', outcome: null, steps: [], progress: { done: 0, total: 0 }, pending_action: null,
  human_decisions: [], errors: [], created_at: '2026-07-02T21:07:53Z', finished_at: null, ...overrides,
});

describe('RunProgress', () => {
  it('shows progress while running', () => {
    render(
      <RunProgress
        run={run({ progress: { done: 2, total: 8 }, steps: [step('validate_intake', 'succeeded'), step('analyze_need', 'succeeded'), step('find_related', 'running'), step('score', 'pending'), step('suggest_tags', 'not_requested', { requested: false })] })}
      />,
    );
    expect(screen.getByText('Running: 2 of 8 steps')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Triage steps completed' })).toHaveAttribute('aria-valuenow', '2');
    const chips = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(chips).toEqual(['✓Validate intake: done', '✓Analyze need: done', '◐Find related: running', '○Score: waiting']);
  });

  it('explains a failed step and a skipped dependency', () => {
    render(
      <RunProgress
        run={run({
          status: 'succeeded',
          progress: { done: 3, total: 3 },
          steps: [step('score', 'failed', { error: 'LLMCallError: model unavailable' }), step('decision_brief', 'skipped', { reason: 'dependency failed: score' }), step('draft_comms', 'skipped', { reason: 'not requested' })],
        })}
      />,
    );
    expect(screen.getByText('Finished: 3 of 3 steps')).toBeInTheDocument();
    expect(screen.getByText(/LLMCallError: model unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/dependency failed: score/)).toBeInTheDocument();
    expect(screen.queryByText(/not requested/)).not.toBeInTheDocument();
  });

  it('says what a vague request or a duplicate outcome means', () => {
    const { rerender } = render(<RunProgress run={run({ status: 'succeeded', outcome: 'needs_info' })} />);
    expect(screen.getByText(/too vague to triage/)).toBeInTheDocument();
    rerender(<RunProgress run={run({ status: 'succeeded', outcome: 'duplicate_proposed' })} />);
    expect(screen.getByText(/looks like a duplicate/)).toBeInTheDocument();
    rerender(<RunProgress run={run({ status: 'awaiting_human' })} />);
    expect(screen.getByText('Paused: waiting for your answer')).toBeInTheDocument();
  });
});
