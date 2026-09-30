import type { Tag } from '../api/types';
import { isFiltering, type CardFilter } from '../lib/filter';

interface Props {
  tags: Tag[];
  filter: CardFilter;
  onFilterChange: (filter: CardFilter) => void;
  onNewCard: () => void;
  onManageTags: () => void;
}

export function Toolbar({ tags, filter, onFilterChange, onNewCard, onManageTags }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-3" role="search" aria-label="Filter cards">
      <div>
        <label htmlFor="card-search" className="sr-only">
          Search cards
        </label>
        <input
          id="card-search"
          type="search"
          className="input w-64"
          placeholder="Search title, description, tags…"
          value={filter.query}
          onChange={(e) => onFilterChange({ ...filter, query: e.target.value })}
        />
      </div>
      <div>
        <label htmlFor="tag-filter" className="sr-only">
          Filter by tag
        </label>
        <select
          id="tag-filter"
          className="input !w-auto"
          value={filter.tagId ?? ''}
          onChange={(e) => onFilterChange({ ...filter, tagId: e.target.value === '' ? null : Number(e.target.value) })}
        >
          <option value="">All tags</option>
          {tags.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {tag.name}
            </option>
          ))}
        </select>
      </div>
      {isFiltering(filter) && (
        <button type="button" className="btn" onClick={() => onFilterChange({ tagId: null, query: '' })}>
          Clear filters
        </button>
      )}
      <div className="ml-auto flex gap-2">
        <button type="button" className="btn" onClick={onManageTags}>
          Manage tags
        </button>
        <button type="button" className="btn btn-primary" onClick={onNewCard}>
          New card
        </button>
      </div>
    </div>
  );
}
