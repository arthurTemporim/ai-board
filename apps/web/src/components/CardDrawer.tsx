import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ApiError } from '../api/client';
import {
  errorText,
  useAddComment,
  useAddFeedback,
  useCard,
  useRemoveFeedback,
  useSaveCard,
  useToggleVote,
  useVotes,
} from '../api/hooks';
import type { BoardDetail, CardDetail, CardUpdateInput, Tag } from '../api/types';
import { formatDate, formatDateTime } from '../lib/format';
import { hrefFor } from '../state/urlState';
import { useCurrentUser } from '../state/user';
import { useToast } from '../state/toast';
import { AiPanel } from './AiPanel';
import { TagPicker } from './TagPicker';
import { useDialog } from './useDialog';

interface Props {
  cardId: number;
  board: BoardDetail;
  tags: Tag[];
  onClose: () => void;
  onOpenCard: (id: number) => void;
}

/** Edited fields that differ from the server copy; undefined means "untouched". */
interface Draft {
  title?: string;
  description?: string;
  progress?: number;
  tag_ids?: number[];
  column_id?: number;
}

const sameIds = (a: number[], b: number[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join();

function Section({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="border-t border-slate-200 px-5 py-4">
      <h3 id={id} className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function CardDrawer({ cardId, board, tags, onClose, onOpenCard }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dirtyRef = useRef(false);
  const { data: card, isLoading, error } = useCard(cardId);

  const requestClose = () => {
    if (dirtyRef.current && !window.confirm('Discard your unsaved changes?')) return;
    onClose();
  };
  const cardIdRef = useRef(cardId);
  cardIdRef.current = cardId;
  useDialog(ref, requestClose, closeRef, () => `[data-card-id="${cardIdRef.current}"]`);
  // Following a link to another card swaps the content and drops the focused link; keep focus inside.
  useEffect(() => closeRef.current?.focus(), [cardId]);

  return createPortal(
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onMouseDown={(e) => e.target === e.currentTarget && requestClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={card ? `Card: ${card.title}` : 'Card details'}
        className="flex h-full w-full max-w-xl flex-col overflow-y-auto bg-white shadow-2xl"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <span className="text-xs font-medium text-slate-500">{card ? `${board.name} · #${card.id}` : 'Loading…'}</span>
          <button ref={closeRef} type="button" className="btn" onClick={requestClose}>
            Close <span aria-hidden>✕</span>
          </button>
        </div>
        {isLoading && <p className="p-5 text-sm text-slate-500">Loading card…</p>}
        {error && (
          <p role="alert" className="p-5 text-sm text-red-600">
            {error instanceof ApiError && error.status === 404 ? 'This card no longer exists.' : errorText(error)}
          </p>
        )}
        {card && (
          <DrawerBody key={card.id} card={card} board={board} tags={tags} dirtyRef={dirtyRef} onOpenCard={onOpenCard} />
        )}
      </div>
    </div>,
    document.body,
  );
}

function DrawerBody({
  card,
  board,
  tags,
  dirtyRef,
  onOpenCard,
}: {
  card: CardDetail;
  board: BoardDetail;
  tags: Tag[];
  dirtyRef: { current: boolean };
  onOpenCard: (id: number) => void;
}) {
  const save = useSaveCard();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>({});
  // Version the edits are based on, captured at the first edit so later server changes are detected.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [conflict, setConflict] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const serverTagIds = card.tags.map((t) => t.id);
  const value = {
    title: draft.title ?? card.title,
    description: draft.description ?? card.description,
    progress: draft.progress ?? card.progress,
    tag_ids: draft.tag_ids ?? serverTagIds,
    column_id: draft.column_id ?? card.column_id,
  };
  const dirty = Object.keys(draft).length > 0;
  dirtyRef.current = dirty;

  const edit = <K extends keyof Draft>(field: K, next: NonNullable<Draft[K]>) => {
    setBaseVersion((v) => v ?? card.version);
    setDraft((current) => {
      const server = { title: card.title, description: card.description, progress: card.progress, tag_ids: serverTagIds, column_id: card.column_id }[field];
      const unchanged = Array.isArray(next) ? sameIds(next, server as number[]) : next === server;
      const { [field]: _removed, ...rest } = current;
      void _removed;
      const result: Draft = unchanged ? rest : { ...rest, [field]: next };
      if (Object.keys(result).length === 0) setBaseVersion(null);
      return result;
    });
  };

  const discard = () => {
    setDraft({});
    setBaseVersion(null);
    setConflict(false);
    setFormError(null);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!value.title.trim()) {
      setFormError('The title cannot be empty.');
      return;
    }
    const changes: CardUpdateInput = {};
    if (draft.title !== undefined) changes.title = draft.title.trim();
    if (draft.description !== undefined) changes.description = draft.description;
    if (draft.progress !== undefined) changes.progress = draft.progress;
    if (draft.tag_ids !== undefined) changes.tag_ids = draft.tag_ids;
    setFormError(null);
    save.mutate(
      { card, version: baseVersion ?? card.version, changes, columnId: draft.column_id },
      {
        onSuccess: () => {
          discard();
          toast.info('Changes saved');
        },
        onError: (e) => {
          if (e instanceof ApiError && e.isConflict) setConflict(true);
          else setFormError(errorText(e));
        },
      },
    );
  };

  return (
    <>
      <form onSubmit={submit} className="px-5 py-4" noValidate>
        <label htmlFor="card-title" className="sr-only">
          Title
        </label>
        <input
          id="card-title"
          className="w-full rounded-md border border-transparent px-2 py-1 text-xl font-semibold hover:border-slate-300 focus:border-slate-300"
          value={value.title}
          maxLength={200}
          onChange={(e) => edit('title', e.target.value)}
          aria-invalid={formError !== null}
        />
        <p className="mt-1 px-2 text-xs text-slate-500">
          Requested by <span className="font-medium">{card.requester_type}</span> · created {formatDate(card.created_at)} · updated{' '}
          {formatDate(card.updated_at)}
          {card.priority_score !== null && <> · priority {card.priority_score}</>}
        </p>

        <div className="mt-4 space-y-4">
          <div>
            <label htmlFor="card-description" className="label">
              Description
            </label>
            <textarea id="card-description" className="input min-h-28" value={value.description} onChange={(e) => edit('description', e.target.value)} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="card-column" className="label">
                Status
              </label>
              <select id="card-column" className="input" value={value.column_id} onChange={(e) => edit('column_id', Number(e.target.value))}>
                {board.columns.map((column) => (
                  <option key={column.id} value={column.id}>
                    {column.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="card-progress" className="label">
                Progress: {value.progress}%
              </label>
              <input
                id="card-progress"
                type="range"
                min={0}
                max={100}
                step={5}
                className="w-full accent-blue-600"
                value={value.progress}
                onChange={(e) => edit('progress', Number(e.target.value))}
              />
            </div>
          </div>

          <div>
            <span className="label">Tags</span>
            <TagPicker tags={tags} selected={value.tag_ids} onChange={(ids) => edit('tag_ids', ids)} />
          </div>
        </div>

        {conflict && (
          <div role="alert" className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            Someone else changed this card after you started editing, so your changes were not saved.{' '}
            <button type="button" className="font-semibold underline" onClick={discard}>
              Discard my edits and show the latest
            </button>
          </div>
        )}
        {formError && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {formError}
          </p>
        )}
        <div className="mt-4 flex items-center justify-end gap-2">
          {dirty && <span className="mr-auto text-xs text-slate-500">Unsaved changes</span>}
          <button type="button" className="btn" disabled={!dirty || save.isPending} onClick={discard}>
            Discard
          </button>
          <button type="submit" className="btn btn-primary" disabled={!dirty || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>

      <AiPanel card={card} onOpenCard={onOpenCard} />
      <VotesSection card={card} />
      <GroupingSection card={card} board={board} onOpenCard={onOpenCard} />
      <CommentsSection card={card} />
      <FeedbackSection card={card} />
    </>
  );
}

function VotesSection({ card }: { card: CardDetail }) {
  const { current } = useCurrentUser();
  const votes = useVotes(card.id, current?.id ?? null);
  const toggle = useToggleVote(card, current?.id ?? null);
  const toast = useToast();
  const voted = votes.data?.voted ?? false;
  return (
    <Section title="Popularity" id="votes-heading">
      <div className="flex items-center gap-3">
        <button
          type="button"
          className={`btn ${voted ? 'btn-primary' : ''}`}
          aria-pressed={voted}
          disabled={!current || votes.isLoading || toggle.isPending}
          onClick={() => toggle.mutate(voted, { onError: (e) => toast.error(errorText(e)) })}
        >
          {voted ? 'Voted ✓' : 'Vote'}
        </button>
        <span className="text-sm text-slate-600" aria-live="polite">
          {card.vote_count} {card.vote_count === 1 ? 'vote' : 'votes'}
        </span>
        {!current && <span className="text-xs text-slate-500">Choose who you are in the header to vote.</span>}
      </div>
    </Section>
  );
}

function GroupingSection({ card, board, onOpenCard }: { card: CardDetail; board: BoardDetail; onOpenCard: (id: number) => void }) {
  const duplicates = card.links.filter((l) => l.kind === 'duplicate' && l.status !== 'rejected');
  const related = card.links.filter((l) => l.kind === 'related' && l.status !== 'rejected');
  const list = (items: CardDetail['links']) => (
    <ul className="space-y-1">
      {items.map((link) => (
        <li key={link.card_id} className="flex items-baseline gap-2 text-sm">
          <a
            href={hrefFor(board.id, link.card_id)}
            className="text-blue-700 underline-offset-2 hover:underline"
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey) return;
              e.preventDefault();
              onOpenCard(link.card_id);
            }}
          >
            {link.title}
          </a>
          <span className="text-xs text-slate-500">{link.status}</span>
        </li>
      ))}
    </ul>
  );
  const empty = <p className="text-sm text-slate-400">None</p>;
  return (
    <Section title="Grouping" id="grouping-heading">
      <dl className="space-y-3">
        <div>
          <dt className="label">Underlying problem</dt>
          <dd>
            {card.problems.length === 0 ? (
              <p className="text-sm text-slate-400">Not grouped yet</p>
            ) : (
              <ul className="space-y-1">
                {card.problems.map((p) => (
                  <li key={p.id} className="text-sm">
                    {p.title} <span className="text-xs text-slate-500">{p.status}</span>
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
        <div>
          <dt className="label">Duplicates</dt>
          <dd>{duplicates.length ? list(duplicates) : empty}</dd>
        </div>
        <div>
          <dt className="label">Related requests</dt>
          <dd>{related.length ? list(related) : empty}</dd>
        </div>
      </dl>
    </Section>
  );
}

function CommentsSection({ card }: { card: CardDetail }) {
  const { current } = useCurrentUser();
  const add = useAddComment(card);
  const toast = useToast();
  const [body, setBody] = useState('');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!body.trim()) return;
    add.mutate(body.trim(), { onSuccess: () => setBody(''), onError: (e) => toast.error(errorText(e)) });
  };
  return (
    <Section title={`Comments (${card.comments.length})`} id="comments-heading">
      <ul className="space-y-3">
        {card.comments.map((comment) => (
          <li key={comment.id} className="rounded-md bg-slate-50 p-3">
            <p className="text-xs text-slate-500">
              <span className="font-medium text-slate-700">{comment.author.name}</span> · {formatDateTime(comment.created_at)}
            </p>
            <p className="mt-1 whitespace-pre-wrap text-sm">{comment.body}</p>
          </li>
        ))}
        {card.comments.length === 0 && <li className="text-sm text-slate-400">No comments yet.</li>}
      </ul>
      <form onSubmit={submit} className="mt-3 space-y-2">
        <label htmlFor="new-comment" className="sr-only">
          Add a comment
        </label>
        <textarea
          id="new-comment"
          className="input min-h-16"
          placeholder={current ? `Comment as ${current.name}…` : 'Choose who you are to comment'}
          value={body}
          disabled={!current}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e);
          }}
        />
        <div className="flex justify-end">
          <button type="submit" className="btn btn-primary" disabled={!current || !body.trim() || add.isPending}>
            {add.isPending ? 'Posting…' : 'Add comment'}
          </button>
        </div>
      </form>
    </Section>
  );
}

function FeedbackSection({ card }: { card: CardDetail }) {
  const add = useAddFeedback(card);
  const remove = useRemoveFeedback(card);
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState('');
  const [quote, setQuote] = useState('');
  const [customer, setCustomer] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!source.trim() || !quote.trim()) return;
    add.mutate(
      { source: source.trim(), quote: quote.trim(), customer_ref: customer.trim() || null },
      {
        onSuccess: () => {
          setSource('');
          setQuote('');
          setCustomer('');
          setOpen(false);
        },
        onError: (e) => toast.error(errorText(e)),
      },
    );
  };

  return (
    <Section title={`Customer feedback (${card.feedback.length})`} id="feedback-heading">
      <ul className="space-y-3">
        {card.feedback.map((entry) => (
          <li key={entry.id} className="border-l-2 border-slate-300 pl-3">
            <blockquote className="text-sm italic text-slate-700">“{entry.quote}”</blockquote>
            <p className="mt-1 flex items-center gap-2 text-xs text-slate-500">
              <span>
                {entry.source}
                {entry.customer_ref ? ` · ${entry.customer_ref}` : ''} · {formatDate(entry.created_at)}
              </span>
              <button
                type="button"
                className="ml-auto text-red-700 hover:underline"
                aria-label={`Delete feedback from ${entry.source}`}
                onClick={() => remove.mutate(entry.id, { onError: (e) => toast.error(errorText(e)) })}
              >
                Delete
              </button>
            </p>
          </li>
        ))}
        {card.feedback.length === 0 && <li className="text-sm text-slate-400">No feedback recorded.</li>}
      </ul>
      {open ? (
        <form onSubmit={submit} className="mt-3 space-y-2 rounded-md bg-slate-50 p-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="fb-source" className="label">
                Source
              </label>
              <input id="fb-source" className="input" placeholder="Support ticket, call…" value={source} onChange={(e) => setSource(e.target.value)} />
            </div>
            <div>
              <label htmlFor="fb-customer" className="label">
                Customer (optional)
              </label>
              <input id="fb-customer" className="input" value={customer} onChange={(e) => setCustomer(e.target.value)} />
            </div>
          </div>
          <div>
            <label htmlFor="fb-quote" className="label">
              Quote
            </label>
            <textarea id="fb-quote" className="input min-h-16" value={quote} onChange={(e) => setQuote(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!source.trim() || !quote.trim() || add.isPending}>
              Add feedback
            </button>
          </div>
        </form>
      ) : (
        <button type="button" className="btn mt-3" onClick={() => setOpen(true)}>
          Add feedback
        </button>
      )}
    </Section>
  );
}
