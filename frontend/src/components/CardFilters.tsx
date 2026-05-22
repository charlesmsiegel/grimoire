export interface CardFiltersProps {
  search: string;
  onSearch: (q: string) => void;
  availableTags: string[];
  selectedTags: string[];
  onToggleTag: (tag: string) => void;
  onClearTags: () => void;
  /** Placeholder for the search input, e.g. "Search items…". */
  searchPlaceholder?: string;
  /** Accessible label for the search input. */
  searchLabel?: string;
  /** Count summary, e.g. "12 of 348", rendered next to the input. */
  resultSummary?: string;
}

export function CardFilters({
  search,
  onSearch,
  availableTags,
  selectedTags,
  onToggleTag,
  onClearTags,
  searchPlaceholder = "Search…",
  searchLabel = "Search",
  resultSummary,
}: CardFiltersProps) {
  const hasTags = availableTags.length > 0;
  return (
    <div className="card-filters" role="search">
      <div className="card-filters-row">
        <label className="card-filters-search">
          <span className="visually-hidden">{searchLabel}</span>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchLabel}
          />
        </label>
        {resultSummary && <span className="card-filters-summary">{resultSummary}</span>}
      </div>
      {hasTags && (
        <div className="card-filters-tags" aria-label="Filter by tag">
          {availableTags.map((tag) => {
            const active = selectedTags.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                className={active ? "card-filter-tag active" : "card-filter-tag"}
                aria-pressed={active}
                onClick={() => onToggleTag(tag)}
              >
                {tag}
              </button>
            );
          })}
          {selectedTags.length > 0 && (
            <button type="button" className="card-filter-tag-clear" onClick={onClearTags}>
              Clear tags
            </button>
          )}
        </div>
      )}
    </div>
  );
}
