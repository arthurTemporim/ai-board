import type { Tag } from '../api/types';
import { readableTextColor } from '../lib/format';

export function TagChip({ tag, small = false }: { tag: Pick<Tag, 'name' | 'color'>; small?: boolean }) {
  return (
    <span
      className={`inline-block max-w-full truncate rounded-full font-medium ${small ? 'px-2 py-0 text-[11px] leading-5' : 'px-2.5 py-0.5 text-xs'}`}
      style={{ backgroundColor: tag.color, color: readableTextColor(tag.color) }}
    >
      {tag.name}
    </span>
  );
}
