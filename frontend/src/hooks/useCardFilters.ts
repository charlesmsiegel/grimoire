import { useMemo, useState } from "react";

export interface CardFilterAccessors<T> {
  /** Strings to match the search query against. Nulls are ignored. */
  text: (item: T) => Array<string | null | undefined>;
  /** Tags exposed in the chip row and required for tag-based filtering. */
  tags: (item: T) => string[];
}

export interface UseCardFiltersResult<T> {
  filtered: T[];
  search: string;
  setSearch: (s: string) => void;
  selectedTags: string[];
  toggleTag: (tag: string) => void;
  clearTags: () => void;
  /** Distinct tags across the input list, sorted. */
  availableTags: string[];
}

export function useCardFilters<T>(
  items: T[],
  accessors: CardFilterAccessors<T>,
): UseCardFiltersResult<T> {
  const [search, setSearch] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);

  const availableTags = useMemo(() => {
    const set = new Set<string>();
    for (const item of items) {
      for (const tag of accessors.tags(item)) set.add(tag);
    }
    return [...set].sort((a, b) => a.localeCompare(b));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q && selectedTags.length === 0) return items;
    return items.filter((item) => {
      if (selectedTags.length > 0) {
        const tagSet = new Set(accessors.tags(item));
        for (const t of selectedTags) {
          if (!tagSet.has(t)) return false;
        }
      }
      if (q) {
        const haystack = accessors
          .text(item)
          .filter((s): s is string => typeof s === "string" && s.length > 0)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, search, selectedTags]);

  function toggleTag(tag: string) {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }

  function clearTags() {
    setSelectedTags([]);
  }

  return {
    filtered,
    search,
    setSearch,
    selectedTags,
    toggleTag,
    clearTags,
    availableTags,
  };
}
