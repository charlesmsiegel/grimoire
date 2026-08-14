import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react";

/** One offer in the palette. `to` navigates; `run` does something in place.
 *  An item with both is a navigation that also has a side effect, which is
 *  how "Add Aud to this scene" differs from "Sister Aud". */
export type PaletteItem = {
  /** Unique within the whole result list — the palette keys and selects on it. */
  id: string;
  /** Which heading it files under. Free text so a page can name its own; the
   *  play view uses IN THIS CAMPAIGN / SCENES / ELSEWHERE. */
  group: string;
  /** The text the query matches against and the row shows in prose. */
  label: string;
  /** Dim trailing detail — `character · in scene`, `fact · scene iv`. */
  meta?: string;
  /** Two-letter monogram square, for rows that name a person. */
  badge?: string;
  /** Rows that *do* rather than *go* are stamped ACTION on the right. */
  action?: boolean;
  to?: string;
  run?: () => void;
};

/** A page's contribution. Called with the trimmed, lowercased query; returns
 *  what it wants offered. Sources filter themselves rather than handing over
 *  everything they hold, because only the page knows which of a record's
 *  fields are worth matching (a character's name, not their whole dossier). */
export type PaletteSource = (query: string) => PaletteItem[];

type Ctx = {
  open: boolean;
  setOpen: (open: boolean) => void;
  register: (source: PaletteSource) => () => void;
  sources: Set<PaletteSource>;
  /** Bumped whenever a source registers or unregisters. The palette depends on
   *  it so a page that mounts while the palette is open contributes without
   *  waiting for the next keystroke. */
  rev: number;
};

// A no-op default rather than a throw: every route is rendered bare in its own
// test, and contributing to the palette must not require the whole shell.
const PaletteCtx = createContext<Ctx>({
  open: false, setOpen: () => {}, register: () => () => {}, sources: new Set(), rev: 0,
});

export function PaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  // A ref, not state: registering must not re-render every subscriber, and the
  // palette reads the set only while it is open and a keystroke has landed.
  const sources = useRef(new Set<PaletteSource>()).current;
  const [rev, setRev] = useState(0);

  // Stable for the life of the provider, and that stability is the whole
  // contract: `usePaletteSource` has `register` in its effect's dependency
  // list, so a `register` that changed identity when the set did would
  // unregister and re-register on its own bump — a render loop that never
  // settles, since each pass bumps the counter that caused it.
  const register = useCallback((source: PaletteSource) => {
    sources.add(source);
    setRev((n) => n + 1);
    return () => { sources.delete(source); setRev((n) => n + 1); };
  }, [sources]);

  const value = useMemo<Ctx>(
    () => ({ open, setOpen, sources, register, rev }),
    [open, sources, register, rev],
  );

  return <PaletteCtx.Provider value={value}>{children}</PaletteCtx.Provider>;
}

export function usePalette(): Ctx {
  return useContext(PaletteCtx);
}

/** Offer `source`'s items for as long as the calling component is mounted.
 *
 *  The caller is responsible for the identity of `source` — wrap it in
 *  `useCallback` over the data it closes on, or it re-registers every render.
 *  Registration is idempotent (a Set), so a churning identity costs a
 *  subscription bump rather than duplicate rows. */
export function usePaletteSource(source: PaletteSource): void {
  const { register } = usePalette();
  useEffect(() => register(source), [register, source]);
}

/** Split `label` around the first case-insensitive occurrence of `query`, so
 *  the matched substring can be rendered in the accent. Returns three parts;
 *  the middle is "" when the match is on something other than the label (the
 *  meta line, an alias), which is a hit worth showing unhighlighted rather
 *  than not showing. */
export function highlight(label: string, query: string): [string, string, string] {
  if (!query) return [label, "", ""];
  const at = label.toLowerCase().indexOf(query.toLowerCase());
  if (at < 0) return [label, "", ""];
  return [label.slice(0, at), label.slice(at, at + query.length), label.slice(at + query.length)];
}

/** Substring match over label and meta, the two things a row shows. Deliberately
 *  not fuzzy: an exact-substring list of six is easier to trust than a ranked
 *  list of forty, and every id in this app is a slug someone can type. */
export function matches(item: PaletteItem, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return item.label.toLowerCase().includes(q) || (item.meta ?? "").toLowerCase().includes(q);
}
