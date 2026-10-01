import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { makeCard } from '../test/fixtures';
import { CardTile, cardSummary } from './CardTile';

const tags = [
  { id: 1, board_id: 1, name: 'security', color: '#DC2626' },
  { id: 2, board_id: 1, name: 'enterprise', color: '#059669' },
];

describe('CardTile', () => {
  it('shows title, tag chips, vote and comment counts, and the created date', () => {
    render(<CardTile card={makeCard({ tags, vote_count: 7, comment_count: 2 })} />);

    expect(screen.getByRole('heading', { name: 'Support SAML single sign-on' })).toBeInTheDocument();
    const chips = within(screen.getByRole('list', { name: 'Tags' })).getAllByRole('listitem');
    expect(chips.map((c) => c.textContent)).toEqual(['security', 'enterprise']);
    expect(chips[0]?.firstElementChild).toHaveStyle({ backgroundColor: '#DC2626' });
    expect(screen.getByLabelText('7 votes')).toHaveTextContent('7');
    expect(screen.getByLabelText('2 comments')).toHaveTextContent('2');
    expect(screen.getByText('Jul 2, 2026')).toHaveAttribute('datetime', '2026-07-02T21:07:53Z');
  });

  it('uses singular labels for one vote or comment', () => {
    render(<CardTile card={makeCard({ vote_count: 1, comment_count: 1 })} />);
    expect(screen.getByLabelText('1 vote')).toBeInTheDocument();
    expect(screen.getByLabelText('1 comment')).toBeInTheDocument();
  });

  it('shows a progress bar only when there is progress', () => {
    const { rerender } = render(<CardTile card={makeCard({ progress: 0 })} />);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();

    rerender(<CardTile card={makeCard({ progress: 60 })} />);
    expect(screen.getByRole('progressbar', { name: 'Progress' })).toHaveAttribute('aria-valuenow', '60');
    expect(screen.getByText('60%')).toBeInTheDocument();
  });

  it('shows no indicators for a plain card', () => {
    render(<CardTile card={makeCard()} />);
    expect(screen.queryByRole('list', { name: /indicators/i })).not.toBeInTheDocument();
  });

  it('flags duplicates, related requests and pending AI suggestions', () => {
    render(<CardTile card={makeCard({ duplicate_count: 2, related_count: 1, pending_suggestion_count: 3, needs_review: true })} />);
    const indicators = within(screen.getByRole('list', { name: /indicators/i }));
    expect(indicators.getByText('2 possible duplicates')).toBeInTheDocument();
    expect(indicators.getByText('1 related')).toBeInTheDocument();
    expect(indicators.getByText('3 AI suggestions pending')).toBeInTheDocument();
  });

  it('uses singular wording for a single duplicate or suggestion', () => {
    render(<CardTile card={makeCard({ duplicate_count: 1, pending_suggestion_count: 1, needs_review: true })} />);
    expect(screen.getByText('1 possible duplicate')).toBeInTheDocument();
    expect(screen.getByText('1 AI suggestion pending')).toBeInTheDocument();
  });
});

describe('cardSummary', () => {
  it('describes the card for screen readers', () => {
    const text = cardSummary(makeCard({ tags, vote_count: 3, progress: 40, duplicate_count: 1, pending_suggestion_count: 2, needs_review: true }));
    expect(text).toBe(
      'Support SAML single sign-on, tags: security, enterprise, 3 votes, 0 comments, 40% complete, 1 possible duplicate, 2 AI suggestions pending',
    );
  });
});
