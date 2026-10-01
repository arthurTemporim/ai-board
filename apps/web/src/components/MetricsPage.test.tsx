import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Metrics } from '../api/types';
import { MetricsView } from './MetricsPage';

const metrics: Metrics = {
  time_to_first_decision: { cards: 3, mean_minutes: 13.3, median_minutes: 10, slowest_minutes: 30 },
  acceptance: {
    accepted: 5, rejected: 3, decided: 8, rate: 0.625, accepted_without_changes_rate: 0.375,
    by_type: { tag: { accepted: 1, rejected: 1, rate: 0.5 }, score: { accepted: 2, rejected: 1, rate: 0.6667 } },
  },
  duplicates: { merged: 2, votes_moved: 5, duplicates_confirmed_as_suggested: 2, duplicates_rejected: 1 },
  score_overrides: { overridden: 1, kept: 2, rate: 0.5, scores_rejected: 1 },
  demo_entries: 4,
  total_decisions: 8,
  definitions: { time_to_first_decision: 'Elapsed minutes ...', acceptance: 'Accepted divided ...', duplicates: 'Duplicate suggestions ...', score_overrides: 'Score suggestions ...' },
  baseline: { is_assumption: true, manual_triage_minutes: 15, description: 'Assumed 15 minutes. This is an assumption, not a measurement.', comparable: false },
};
const empty: Metrics = {
  ...metrics,
  time_to_first_decision: { cards: 0, mean_minutes: null, median_minutes: null, slowest_minutes: null },
  acceptance: { accepted: 0, rejected: 0, decided: 0, rate: null, accepted_without_changes_rate: null, by_type: {} },
  duplicates: { merged: 0, votes_moved: 0, duplicates_confirmed_as_suggested: 0, duplicates_rejected: 0 },
  score_overrides: { overridden: 0, kept: 0, rate: null, scores_rejected: 0 },
  demo_entries: 0,
  total_decisions: 0,
};

describe('MetricsView', () => {
  it('shows the four metrics with their numbers', () => {
    render(<MetricsView metrics={metrics} />);
    const tile = (name: string) => within(screen.getByRole('region', { name }));
    expect(tile('Time to first decision').getByText('10 min')).toBeInTheDocument();
    expect(tile('Time to first decision').getByText(/median over 3 card\(s\); mean 13.3 min; slowest 30 min/)).toBeInTheDocument();
    expect(tile('Suggestion acceptance').getByText('62.5%')).toBeInTheDocument();
    expect(tile('Suggestion acceptance').getByText(/5 accepted, 3 rejected; 37.5% accepted without changes/)).toBeInTheDocument();
    expect(tile('Duplicates consolidated').getByText('2')).toBeInTheDocument();
    expect(tile('Duplicates consolidated').getByText(/5 vote\(s\) moved/)).toBeInTheDocument();
    expect(tile('Score override rate').getByText('50%')).toBeInTheDocument();
    expect(tile('Score override rate').getByText(/1 of 2 accepted score\(s\) were changed/)).toBeInTheDocument();
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual(['tag1150%', 'score2166.7%']);
  });

  it('says plainly that the baseline is an assumption and not like for like', () => {
    render(<MetricsView metrics={metrics} />);
    const box = within(screen.getByRole('region', { name: 'Assumptions' }));
    expect(box.getByRole('heading', { name: /Assumed manual baseline \(not measured\)/ })).toBeInTheDocument();
    expect(box.getByText(/assumption, not a measurement/)).toBeInTheDocument();
    expect(box.getByText('15 min per card')).toBeInTheDocument();
    expect(box.getByText(/not like for like/)).toBeInTheDocument();
  });

  it('labels demo data', () => {
    render(<MetricsView metrics={metrics} />);
    expect(screen.getByRole('note')).toHaveTextContent(/Includes 4 demo decision\(s\)/);
  });

  it('shows "no data yet" instead of zeros when nothing has been decided', () => {
    render(<MetricsView metrics={empty} />);
    expect(screen.getAllByText('no data yet').length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole('note')).toHaveTextContent(/No decisions have been recorded yet/);
    expect(screen.queryByRole('region', { name: 'Acceptance by type' })).not.toBeInTheDocument();
  });
});
