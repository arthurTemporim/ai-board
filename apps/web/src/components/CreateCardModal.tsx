import { useState, type FormEvent } from 'react';
import { errorText, useCreateAndTriage, useCreateCard } from '../api/hooks';
import { REQUESTER_TYPES, type BoardDetail, type RequesterType, type Tag } from '../api/types';
import { useToast } from '../state/toast';
import { Modal } from './Modal';
import { TagPicker } from './TagPicker';

interface Props {
  board: BoardDetail;
  tags: Tag[];
  onClose: () => void;
  onCreated: (cardId: number) => void;
}

export function CreateCardModal({ board, tags, onClose, onCreated }: Props) {
  const create = useCreateCard();
  const createAndTriage = useCreateAndTriage();
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [requester, setRequester] = useState<RequesterType>('customer');
  const [columnId, setColumnId] = useState(board.columns[0]?.id ?? 0);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  const triage = () => {
    if (!title.trim()) {
      setError('Enter a title.');
      return;
    }
    createAndTriage.mutate(
      { board_id: board.id, column_id: columnId, title: title.trim(), description, requester_type: requester, tag_ids: tagIds },
      {
        onSuccess: (accepted) => {
          toast.info('Card created. AI triage is running');
          onCreated(accepted.card_id);
        },
        onError: (e) => setError(errorText(e)),
      },
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) {
      setError('Enter a title.');
      return;
    }
    create.mutate(
      { board_id: board.id, column_id: columnId, title: title.trim(), description, requester_type: requester, tag_ids: tagIds },
      {
        onSuccess: (card) => {
          toast.info('Card created');
          onCreated(card.id);
        },
        onError: (e) => setError(errorText(e)),
      },
    );
  };

  return (
    <Modal title="New card" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="new-title" className="label">
            Title
          </label>
          <input
            id="new-title"
            className="input"
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={error !== null && !title.trim()}
            aria-describedby={error ? 'new-card-error' : undefined}
          />
        </div>
        <div>
          <label htmlFor="new-description" className="label">
            Description
          </label>
          <textarea id="new-description" className="input min-h-24" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="new-requester" className="label">
              Requester
            </label>
            <select id="new-requester" className="input" value={requester} onChange={(e) => setRequester(e.target.value as RequesterType)}>
              {REQUESTER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="new-column" className="label">
              Column
            </label>
            <select id="new-column" className="input" value={columnId} onChange={(e) => setColumnId(Number(e.target.value))}>
              {board.columns.map((column) => (
                <option key={column.id} value={column.id}>
                  {column.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <span className="label">Tags</span>
          <TagPicker tags={tags} selected={tagIds} onChange={setTagIds} />
        </div>
        {error && (
          <p id="new-card-error" role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn" disabled={create.isPending || createAndTriage.isPending} onClick={triage}>
            {createAndTriage.isPending ? 'Starting…' : 'Create and run AI triage'}
          </button>
          <button type="submit" className="btn btn-primary" disabled={create.isPending || createAndTriage.isPending}>
            {create.isPending ? 'Creating…' : 'Create card'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
