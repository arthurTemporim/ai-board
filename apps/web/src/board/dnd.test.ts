import { describe, expect, it } from 'vitest';
import { makeBoard } from '../test/fixtures';
import { applyMove, cardDndId, columnDndId, moveFromDragEnd } from './dnd';

const board = makeBoard();
const end = (activeCard: number, over: string | null) => ({
  active: { id: cardDndId(activeCard) },
  over: over === null ? null : { id: over },
});

describe('moveFromDragEnd', () => {
  it('reorders within a column: dropping A on C puts A at C’s index', () => {
    expect(moveFromDragEnd(board.columns, end(1, cardDndId(3)))).toEqual({ cardId: 1, columnId: 10, position: 2 });
  });

  it('reorders upward: dropping C on A puts C first', () => {
    expect(moveFromDragEnd(board.columns, end(3, cardDndId(1)))).toEqual({ cardId: 3, columnId: 10, position: 0 });
  });

  it('moves across columns, inserting at the card dropped on', () => {
    expect(moveFromDragEnd(board.columns, end(2, cardDndId(4)))).toEqual({ cardId: 2, columnId: 20, position: 0 });
  });

  it('appends when dropped on an empty column', () => {
    expect(moveFromDragEnd(board.columns, end(2, columnDndId(30)))).toEqual({ cardId: 2, columnId: 30, position: 0 });
  });

  it('appends after the last card when dropped on another column’s body', () => {
    expect(moveFromDragEnd(board.columns, end(2, columnDndId(20)))).toEqual({ cardId: 2, columnId: 20, position: 1 });
  });

  it('dropping on its own column moves it to the end, and is a no-op if already last', () => {
    expect(moveFromDragEnd(board.columns, end(1, columnDndId(10)))).toEqual({ cardId: 1, columnId: 10, position: 2 });
    expect(moveFromDragEnd(board.columns, end(3, columnDndId(10)))).toBeNull();
  });

  it('does nothing when dropped outside, on itself, or on unknown ids', () => {
    expect(moveFromDragEnd(board.columns, end(1, null))).toBeNull();
    expect(moveFromDragEnd(board.columns, end(1, cardDndId(1)))).toBeNull();
    expect(moveFromDragEnd(board.columns, end(1, 'nonsense'))).toBeNull();
    expect(moveFromDragEnd(board.columns, end(99, cardDndId(1)))).toBeNull();
    expect(moveFromDragEnd(board.columns, { active: { id: 'column-10' }, over: { id: cardDndId(1) } })).toBeNull();
  });
});

describe('applyMove', () => {
  const titles = (b: typeof board, columnId: number) => b.columns.find((c) => c.id === columnId)?.cards.map((c) => c.title);
  const positions = (b: typeof board, columnId: number) => b.columns.find((c) => c.id === columnId)?.cards.map((c) => c.position);

  it('reorders within a column and renumbers positions', () => {
    const next = applyMove(board, { cardId: 1, columnId: 10, position: 2 });
    expect(titles(next, 10)).toEqual(['B', 'C', 'A']);
    expect(positions(next, 10)).toEqual([0, 1, 2]);
  });

  it('moves across columns, renumbering both and updating the card', () => {
    const next = applyMove(board, { cardId: 2, columnId: 20, position: 0 });
    expect(titles(next, 10)).toEqual(['A', 'C']);
    expect(positions(next, 10)).toEqual([0, 1]);
    expect(titles(next, 20)).toEqual(['B', 'D']);
    expect(positions(next, 20)).toEqual([0, 1]);
    const moved = next.columns[1]?.cards[0];
    expect(moved?.column_id).toBe(20);
    expect(moved?.version).toBe(2); // the server bumps the version on every move
  });

  it('clamps an out-of-range position like the server does', () => {
    expect(titles(applyMove(board, { cardId: 1, columnId: 20, position: 99 }), 20)).toEqual(['D', 'A']);
  });

  it('does not mutate the input and ignores unknown cards', () => {
    const snapshot = JSON.stringify(board);
    applyMove(board, { cardId: 1, columnId: 30, position: 0 });
    expect(JSON.stringify(board)).toBe(snapshot);
    expect(applyMove(board, { cardId: 99, columnId: 30, position: 0 })).toBe(board);
  });
});
