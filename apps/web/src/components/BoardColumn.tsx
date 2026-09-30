import { useDroppable } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import type { BoardColumn as BoardColumnData, Card } from '../api/types';
import { cardDndId, columnDndId } from '../board/dnd';
import { SortableCard } from './SortableCard';

interface Props {
  column: BoardColumnData;
  visibleCards: Card[];
  dragDisabled: boolean;
  onOpenCard: (card: Card) => void;
}

export function BoardColumn({ column, visibleCards, dragDisabled, onOpenCard }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: columnDndId(column.id), disabled: dragDisabled });
  const overLimit = column.wip_limit !== null && column.cards.length > column.wip_limit;
  const headingId = `column-${column.id}-title`;

  return (
    <section
      aria-labelledby={headingId}
      className={`flex max-h-full w-72 shrink-0 flex-col rounded-lg bg-slate-100 ${isOver ? 'ring-2 ring-blue-300' : ''}`}
    >
      <header className="flex items-center justify-between px-3 py-2.5">
        <h2 id={headingId} className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          {column.name}
        </h2>
        <span
          className={`rounded-full px-2 text-xs font-medium ${overLimit ? 'bg-red-100 text-red-700' : 'bg-slate-200 text-slate-600'}`}
          title={column.wip_limit !== null ? `Limit ${column.wip_limit}` : undefined}
        >
          {visibleCards.length === column.cards.length
            ? column.cards.length
            : `${visibleCards.length} of ${column.cards.length}`}
          {column.wip_limit !== null ? ` / ${column.wip_limit}` : ''}
        </span>
      </header>
      <div ref={setNodeRef} className="flex min-h-16 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-2">
        <SortableContext items={visibleCards.map((c) => cardDndId(c.id))} strategy={verticalListSortingStrategy}>
          {visibleCards.map((card) => (
            <SortableCard key={card.id} card={card} dragDisabled={dragDisabled} onOpen={onOpenCard} />
          ))}
        </SortableContext>
        {visibleCards.length === 0 && (
          <p className="rounded-md border border-dashed border-slate-300 px-3 py-4 text-center text-xs text-slate-400">
            {column.cards.length === 0 ? 'No cards' : 'No matching cards'}
          </p>
        )}
      </div>
    </section>
  );
}
