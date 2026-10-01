import type { Ref } from 'react';
import type { Card } from '../api/types';
import { formatDate } from '../lib/format';
import { CommentIcon, CopyIcon, LinkIcon, SparkleIcon, ThumbIcon } from './icons';
import { ProgressBar } from './ProgressBar';
import { TagChip } from './TagChip';

interface Props {
  card: Card;
  /** Used by the sortable wrapper; plain callers can ignore it. */
  innerRef?: Ref<HTMLElement>;
  isDragging?: boolean;
  isOverlay?: boolean;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Spoken summary for the card's focusable wrapper (its children are presentational to screen readers). */
export function cardSummary(card: Card): string {
  const parts = [card.title];
  if (card.tags.length) parts.push(`tags: ${card.tags.map((t) => t.name).join(', ')}`);
  parts.push(plural(card.vote_count, 'vote'), plural(card.comment_count, 'comment'));
  if (card.progress > 0) parts.push(`${card.progress}% complete`);
  if (card.duplicate_count) parts.push(plural(card.duplicate_count, 'possible duplicate'));
  if (card.related_count) parts.push(`${card.related_count} related`);
  if (card.needs_review && !card.pending_suggestion_count) parts.push('AI is waiting for your answer');
  if (card.pending_suggestion_count) parts.push(`${plural(card.pending_suggestion_count, 'AI suggestion')} pending`);
  return parts.join(', ');
}

/** Purely presentational card; drag handling and click behaviour live in SortableCard. */
export function CardTile({ card, isDragging = false, isOverlay = false }: Props) {
  const indicators: { key: string; label: string; icon: JSX.Element; tone: string }[] = [];
  if (card.duplicate_count > 0)
    indicators.push({ key: 'dup', label: plural(card.duplicate_count, 'possible duplicate'), icon: <CopyIcon />, tone: 'bg-amber-50 text-amber-800' });
  if (card.related_count > 0)
    indicators.push({ key: 'rel', label: `${card.related_count} related`, icon: <LinkIcon />, tone: 'bg-sky-50 text-sky-800' });
  if (card.needs_review && card.pending_suggestion_count === 0)
    indicators.push({ key: 'wait', label: 'AI is waiting for your answer', icon: <SparkleIcon />, tone: 'bg-amber-50 text-amber-800' });
  if (card.pending_suggestion_count > 0)
    indicators.push({ key: 'ai', label: `${card.pending_suggestion_count} AI suggestion${card.pending_suggestion_count === 1 ? '' : 's'} pending`, icon: <SparkleIcon />, tone: 'bg-violet-50 text-violet-800' });

  return (
    <article
      className={`rounded-md border border-slate-200 bg-white p-3 text-left shadow-sm ${
        isDragging ? 'opacity-40' : ''
      } ${isOverlay ? 'rotate-1 shadow-lg ring-2 ring-blue-400' : 'hover:border-slate-300 hover:shadow'}`}
    >
      <h3 className="text-sm font-medium leading-snug text-slate-900">{card.title}</h3>

      {card.tags.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1" aria-label="Tags">
          {card.tags.map((tag) => (
            <li key={tag.id} className="max-w-full">
              <TagChip tag={tag} small />
            </li>
          ))}
        </ul>
      )}

      {card.progress > 0 && (
        <div className="mt-2 flex items-center gap-2">
          <ProgressBar value={card.progress} />
          <span className="w-9 shrink-0 text-right text-[11px] text-slate-500">{card.progress}%</span>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1" title={plural(card.vote_count, 'vote')}>
          <ThumbIcon />
          <span aria-label={plural(card.vote_count, 'vote')}>{card.vote_count}</span>
        </span>
        <span className="inline-flex items-center gap-1" title={plural(card.comment_count, 'comment')}>
          <CommentIcon />
          <span aria-label={plural(card.comment_count, 'comment')}>{card.comment_count}</span>
        </span>
        <time dateTime={card.created_at} className="ml-auto">
          {formatDate(card.created_at)}
        </time>
      </div>

      {indicators.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1" aria-label="Grouping and AI indicators">
          {indicators.map((item) => (
            <li key={item.key} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${item.tone}`}>
              {item.icon}
              {item.label}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
