import type { Card } from '../api/types';

export interface CardFilter {
  tagId: number | null;
  query: string;
  /** Only cards with AI suggestions waiting or a run paused for an answer. */
  needsReview: boolean;
}

export const NO_FILTER: CardFilter = { tagId: null, query: '', needsReview: false };

export const isFiltering = (filter: CardFilter): boolean =>
  filter.tagId !== null || filter.query.trim() !== '' || filter.needsReview;

export function matchesFilter(card: Card, filter: CardFilter): boolean {
  if (filter.needsReview && !card.needs_review) return false;
  if (filter.tagId !== null && !card.tags.some((tag) => tag.id === filter.tagId)) return false;
  const query = filter.query.trim().toLowerCase();
  if (!query) return true;
  const haystack = [card.title, card.description, ...card.tags.map((tag) => tag.name)].join('\n').toLowerCase();
  return haystack.includes(query);
}
