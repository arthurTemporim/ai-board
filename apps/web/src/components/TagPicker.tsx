import type { Tag } from '../api/types';
import { readableTextColor } from '../lib/format';

interface Props {
  tags: Tag[];
  selected: number[];
  onChange: (ids: number[]) => void;
  legend?: string;
}

/** Toggle chips; a checkbox under the hood so it works with keyboard and screen readers. */
export function TagPicker({ tags, selected, onChange, legend = 'Tags' }: Props) {
  if (tags.length === 0) return <p className="text-sm text-slate-500">No tags on this board yet.</p>;
  const toggle = (id: number) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  return (
    <fieldset>
      <legend className="sr-only">{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => {
          const on = selected.includes(tag.id);
          return (
            <label key={tag.id} className="cursor-pointer">
              <input type="checkbox" className="peer sr-only" checked={on} onChange={() => toggle(tag.id)} />
              <span
                className="inline-block rounded-full border-2 px-2.5 py-0.5 text-xs font-medium peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-blue-600"
                style={
                  on
                    ? { backgroundColor: tag.color, color: readableTextColor(tag.color), borderColor: tag.color }
                    : { borderColor: tag.color, color: '#334155', backgroundColor: 'white' }
                }
              >
                {on && <span aria-hidden>✓ </span>}
                {tag.name}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
