import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { KeyboardEvent } from 'react';
import type { Card } from '../api/types';
import { cardDndId } from '../board/dnd';
import { CardTile, cardSummary } from './CardTile';

interface Props {
  card: Card;
  dragDisabled: boolean;
  onOpen: (card: Card) => void;
}

/**
 * One focusable element per card. Mouse: click opens the drawer, dragging (after 5px) moves it.
 * Keyboard: Enter opens the drawer; Space picks the card up, arrows move it, Space drops it.
 */
export function SortableCard({ card, dragDisabled, onOpen }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: cardDndId(card.id),
    disabled: dragDisabled,
  });

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && !isDragging) {
      event.preventDefault();
      onOpen(card);
      return;
    }
    listeners?.onKeyDown?.(event);
  };

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      data-card-id={card.id}
      onKeyDown={onKeyDown}
      onClick={() => onOpen(card)}
      aria-label={cardSummary(card)}
      aria-roledescription={dragDisabled ? 'card' : 'draggable card'}
      className="cursor-grab touch-manipulation rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 active:cursor-grabbing"
    >
      <CardTile card={card} isDragging={isDragging} />
    </div>
  );
}
