import { useState, type FormEvent } from 'react';
import { errorText, useTagMutations } from '../api/hooks';
import type { Tag } from '../api/types';
import { useToast } from '../state/toast';
import { Modal } from './Modal';

const PRESETS = ['#DC2626', '#F59E0B', '#059669', '#0891B2', '#2563EB', '#7C3AED', '#DB2777', '#6B7280'];

function ColorPicker({ value, onChange, label }: { value: string; onChange: (color: string) => void; label: string }) {
  return (
    <div className="flex items-center gap-1.5" role="group" aria-label={`${label} color`}>
      {PRESETS.map((preset) => (
        <button
          key={preset}
          type="button"
          aria-label={preset}
          aria-pressed={value.toLowerCase() === preset.toLowerCase()}
          onClick={() => onChange(preset)}
          className="h-5 w-5 rounded-full border border-white ring-1 ring-slate-300 aria-pressed:ring-2 aria-pressed:ring-slate-900"
          style={{ backgroundColor: preset }}
        />
      ))}
      <input
        type="color"
        aria-label={`Custom ${label} color`}
        value={value.toLowerCase()}
        onChange={(e) => onChange(e.target.value)}
        className="h-6 w-8 cursor-pointer rounded border border-slate-300 bg-white p-0.5"
      />
    </div>
  );
}

function TagRow({ tag, boardId }: { tag: Tag; boardId: number }) {
  const { update, remove } = useTagMutations(boardId);
  const toast = useToast();
  const [name, setName] = useState(tag.name);
  const [color, setColor] = useState(tag.color);
  const dirty = name.trim() !== tag.name || color.toLowerCase() !== tag.color.toLowerCase();

  return (
    <li className="space-y-2 border-b border-slate-100 py-3 last:border-0">
      <div className="flex items-center gap-2">
        <input className="input" aria-label={`Name of tag ${tag.name}`} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        <button
          type="button"
          className="btn btn-primary"
          disabled={!dirty || !name.trim() || update.isPending}
          onClick={() =>
            update.mutate({ id: tag.id, name: name.trim(), color }, { onError: (e) => toast.error(errorText(e)) })
          }
        >
          Save
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Delete tag "${tag.name}"? It will be removed from all cards.`))
              remove.mutate(tag.id, { onError: (e) => toast.error(errorText(e)) });
          }}
        >
          Delete
        </button>
      </div>
      <ColorPicker value={color} onChange={setColor} label={tag.name} />
    </li>
  );
}

export function TagManager({ boardId, tags, onClose }: { boardId: number; tags: Tag[]; onClose: () => void }) {
  const { create } = useTagMutations(boardId);
  const [name, setName] = useState('');
  const [color, setColor] = useState(PRESETS[4] as string);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setError('Enter a tag name.');
      return;
    }
    create.mutate(
      { name: name.trim(), color },
      {
        onSuccess: () => {
          setName('');
          setError(null);
        },
        onError: (e) => setError(errorText(e)),
      },
    );
  };

  return (
    <Modal title="Manage tags" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-2 rounded-md bg-slate-50 p-3" noValidate>
        <div className="flex items-center gap-2">
          <label htmlFor="new-tag-name" className="sr-only">
            New tag name
          </label>
          <input id="new-tag-name" className="input" placeholder="New tag name" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            Add tag
          </button>
        </div>
        <ColorPicker value={color} onChange={setColor} label="new tag" />
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
      </form>
      <ul className="mt-2 max-h-[50vh] overflow-y-auto" aria-label="Existing tags">
        {tags.map((tag) => (
          <TagRow key={`${tag.id}:${tag.name}:${tag.color}`} tag={tag} boardId={boardId} />
        ))}
        {tags.length === 0 && <li className="py-4 text-sm text-slate-500">No tags yet.</li>}
      </ul>
    </Modal>
  );
}
