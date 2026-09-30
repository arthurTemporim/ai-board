import {
  closestCorners,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { useState } from 'react';
import type { BoardDetail, Card } from '../api/types';
import { useMoveCard } from '../api/hooks';
import { moveFromDragEnd, parseDndId } from '../board/dnd';
import { isFiltering, matchesFilter, type CardFilter } from '../lib/filter';
import { BoardColumn } from './BoardColumn';
import { CardTile } from './CardTile';

interface Props {
  board: BoardDetail;
  filter: CardFilter;
  onOpenCard: (card: Card) => void;
}

export function BoardView({ board, filter, onOpenCard }: Props) {
  const moveCard = useMoveCard(board.id);
  const [activeCard, setActiveCard] = useState<Card | null>(null);
  // Positions are server-side indexes over all cards, so reordering a filtered subset would be ambiguous.
  const filtering = isFiltering(filter);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
      // Enter is reserved for opening the card.
      keyboardCodes: { start: ['Space'], cancel: ['Escape'], end: ['Space'] },
    }),
  );

  const cardById = (id: number) => board.columns.flatMap((c) => c.cards).find((c) => c.id === id);
  const columnName = (id: number) => board.columns.find((c) => c.id === id)?.name ?? 'column';
  const describe = (id: string | number) => {
    const target = parseDndId(id);
    if (!target) return { title: 'item', where: '' };
    if (target.type === 'card') {
      const card = cardById(target.id);
      return { title: card?.title ?? 'card', where: card ? columnName(card.column_id) : '' };
    }
    return { title: columnName(target.id), where: columnName(target.id) };
  };

  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${describe(active.id).title}.`,
    onDragOver: ({ active, over }) =>
      over ? `${describe(active.id).title} is over ${describe(over.id).title}.` : undefined,
    onDragEnd: ({ active, over }) =>
      over
        ? `Dropped ${describe(active.id).title} in ${describe(over.id).where}.`
        : `Dropped ${describe(active.id).title}; nothing changed.`,
    onDragCancel: ({ active }) => `Cancelled moving ${describe(active.id).title}.`,
  };

  const handleDragStart = (event: DragStartEvent) => {
    const target = parseDndId(event.active.id);
    setActiveCard(target ? (cardById(target.id) ?? null) : null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveCard(null);
    const move = moveFromDragEnd(board.columns, event);
    const card = move ? cardById(move.cardId) : undefined;
    if (move && card) moveCard.mutate({ ...move, version: card.version });
  };

  return (
    <div className="flex h-full flex-col">
      {filtering && (
        <p className="px-4 pb-2 text-xs text-slate-500" role="status">
          Filters are on, so dragging is turned off. Clear the filters to reorder cards.
        </p>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        accessibility={{ announcements }}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => setActiveCard(null)}
      >
        <div className="flex min-h-0 flex-1 items-start gap-3 overflow-x-auto px-4 pb-4">
          {board.columns.map((column) => (
            <BoardColumn
              key={column.id}
              column={column}
              visibleCards={column.cards.filter((card) => matchesFilter(card, filter))}
              dragDisabled={filtering}
              onOpenCard={onOpenCard}
            />
          ))}
        </div>
        <DragOverlay>{activeCard ? <CardTile card={activeCard} isOverlay /> : null}</DragOverlay>
      </DndContext>
    </div>
  );
}
