import type { BoardColumn, BoardDetail, Card } from '../api/types';

export function makeCard(overrides: Partial<Card> = {}): Card {
  return {
    id: 1,
    board_id: 1,
    column_id: 10,
    position: 0,
    title: 'Support SAML single sign-on',
    description: '',
    requester_type: 'customer',
    progress: 0,
    priority_score: null,
    version: 1,
    created_by_id: null,
    created_at: '2026-07-02T21:07:53Z',
    updated_at: '2026-07-02T21:07:53Z',
    tags: [],
    vote_count: 0,
    comment_count: 0,
    duplicate_count: 0,
    related_count: 0,
    pending_draft_count: 0,
    pending_suggestion_count: 0,
    needs_review: false,
    ...overrides,
  };
}

export function makeColumn(id: number, name: string, cards: Card[] = []): BoardColumn {
  return {
    id,
    board_id: 1,
    name,
    position: id,
    wip_limit: null,
    cards: cards.map((card, position) => ({ ...card, column_id: id, position })),
  };
}

/** Inbox [A, B, C], Planned [D], Shipped []. */
export function makeBoard(): BoardDetail {
  return {
    id: 1,
    name: 'Core Platform',
    description: '',
    created_at: '2026-07-01T00:00:00Z',
    columns: [
      makeColumn(10, 'Inbox', [
        makeCard({ id: 1, title: 'A' }),
        makeCard({ id: 2, title: 'B' }),
        makeCard({ id: 3, title: 'C' }),
      ]),
      makeColumn(20, 'Planned', [makeCard({ id: 4, title: 'D' })]),
      makeColumn(30, 'Shipped'),
    ],
  };
}
