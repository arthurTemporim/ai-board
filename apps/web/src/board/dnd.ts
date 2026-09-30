import type { BoardColumn, BoardDetail } from '../api/types';

/** Where a card should end up; `position` is a zero-based index in the target column. */
export interface Move {
  cardId: number;
  columnId: number;
  position: number;
}

type Id = string | number;
export interface DragEndLike {
  active: { id: Id };
  over: { id: Id } | null;
}

export const cardDndId = (id: number): string => `card-${id}`;
export const columnDndId = (id: number): string => `column-${id}`;

export type DropTarget = { type: 'card' | 'column'; id: number };

export function parseDndId(id: Id): DropTarget | null {
  const match = /^(card|column)-(\d+)$/.exec(String(id));
  if (!match) return null;
  return { type: match[1] as 'card' | 'column', id: Number(match[2]) };
}

/**
 * Turns a dnd-kit drag-end event into a Move, or null when nothing should change
 * (dropped outside, on itself, or back in the same place).
 *
 * Dropping on a card puts the dragged card at that card's index (same-column drops behave
 * like array-move); dropping on a column, e.g. an empty one, appends it.
 */
export function moveFromDragEnd(columns: BoardColumn[], event: DragEndLike): Move | null {
  const active = parseDndId(event.active.id);
  const over = event.over ? parseDndId(event.over.id) : null;
  if (!active || active.type !== 'card' || !over) return null;
  if (over.type === 'card' && over.id === active.id) return null;

  const source = columns.find((c) => c.cards.some((card) => card.id === active.id));
  if (!source) return null;
  const fromIndex = source.cards.findIndex((card) => card.id === active.id);

  let target: BoardColumn | undefined;
  let position: number;
  if (over.type === 'card') {
    target = columns.find((c) => c.cards.some((card) => card.id === over.id));
    if (!target) return null;
    position = target.cards.findIndex((card) => card.id === over.id);
  } else {
    target = columns.find((c) => c.id === over.id);
    if (!target) return null;
    // Appending: the end of the column, or of the remaining cards when staying in place.
    position = target.id === source.id ? target.cards.length - 1 : target.cards.length;
  }

  if (target.id === source.id && position === fromIndex) return null;
  return { cardId: active.id, columnId: target.id, position };
}

/** Applies a move to cached board data (optimistic update); mirrors the server's renumbering. */
export function applyMove(board: BoardDetail, move: Move): BoardDetail {
  const moving = board.columns.flatMap((c) => c.cards).find((card) => card.id === move.cardId);
  if (!moving) return board;

  const columns = board.columns.map((column) => {
    const rest = column.cards.filter((card) => card.id !== move.cardId);
    if (column.id !== move.columnId) {
      const changed = rest.length !== column.cards.length;
      return changed ? { ...column, cards: renumber(rest) } : column;
    }
    const index = Math.max(0, Math.min(move.position, rest.length));
    const placed = { ...moving, column_id: column.id, version: moving.version + 1 };
    return { ...column, cards: renumber([...rest.slice(0, index), placed, ...rest.slice(index)]) };
  });
  return { ...board, columns };
}

const renumber = <T extends { position: number }>(cards: T[]): T[] =>
  cards.map((card, position) => (card.position === position ? card : { ...card, position }));
