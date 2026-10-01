import { describe, expect, it } from 'vitest';
import { makeCard } from '../test/fixtures';
import { isFiltering, matchesFilter } from './filter';
import { formatDate, readableTextColor } from './format';

const tag = { id: 5, board_id: 1, name: 'Reporting', color: '#2563EB' };

describe('matchesFilter', () => {
  const card = makeCard({ title: 'Export to CSV', description: 'For my Excel review', tags: [tag] });

  it('matches everything when empty', () => {
    expect(matchesFilter(card, { tagId: null, query: '  ', needsReview: false })).toBe(true);
    expect(isFiltering({ tagId: null, query: '  ', needsReview: false })).toBe(false);
  });
  it('searches title, description and tag names, ignoring case', () => {
    expect(matchesFilter(card, { tagId: null, query: 'csv', needsReview: false })).toBe(true);
    expect(matchesFilter(card, { tagId: null, query: 'EXCEL', needsReview: false })).toBe(true);
    expect(matchesFilter(card, { tagId: null, query: 'reporting', needsReview: false })).toBe(true);
    expect(matchesFilter(card, { tagId: null, query: 'okta', needsReview: false })).toBe(false);
  });
  it('can show only cards that need review', () => {
    const waiting = makeCard({ needs_review: true });
    expect(matchesFilter(waiting, { tagId: null, query: '', needsReview: true })).toBe(true);
    expect(matchesFilter(card, { tagId: null, query: '', needsReview: true })).toBe(false);
    expect(matchesFilter(card, { tagId: null, query: '', needsReview: false })).toBe(true);
    expect(isFiltering({ tagId: null, query: '', needsReview: true })).toBe(true);
    expect(matchesFilter(waiting, { tagId: 5, query: '', needsReview: true })).toBe(false); // filters combine
  });

  it('filters by tag and combines with the search', () => {
    expect(matchesFilter(card, { tagId: 5, query: '', needsReview: false })).toBe(true);
    expect(matchesFilter(card, { tagId: 6, query: '', needsReview: false })).toBe(false);
    expect(matchesFilter(card, { tagId: 5, query: 'okta', needsReview: false })).toBe(false);
  });
});

describe('format', () => {
  it('formats dates in UTC so the output is stable', () => {
    expect(formatDate('2026-07-02T23:59:00Z')).toBe('Jul 2, 2026');
  });
  it('picks readable text colors', () => {
    expect(readableTextColor('#ffffff')).toBe('#111827');
    expect(readableTextColor('#F59E0B')).toBe('#111827');
    expect(readableTextColor('#1e3a8a')).toBe('#ffffff');
    expect(readableTextColor('#DC2626')).toBe('#ffffff');
    expect(readableTextColor('not-a-color')).toBe('#111827');
  });
});
