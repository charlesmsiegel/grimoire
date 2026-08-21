import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { highlight, matches, usePalette, type PaletteItem } from "./palette";

/** The order groups appear in, whatever order the sources registered in. A
 *  name that exists in three places should read as three different offers,
 *  and "which three" is the campaign you are in, then its scenes, then the
 *  rest of the library. Anything a source names that is not on this list
 *  follows, in first-seen order. */
const GROUP_ORDER = ["IN THIS CAMPAIGN", "SCENES", "ELSEWHERE"];

function groupRank(group: string): number {
  const at = GROUP_ORDER.indexOf(group);
  return at < 0 ? GROUP_ORDER.length : at;
}

function Row(
  { item, query, selected, onRun, onHover }: {
    item: PaletteItem; query: string; selected: boolean;
    onRun: () => void; onHover: () => void;
  },
) {
  const [before, hit, after] = highlight(item.label, query);
  return (
    <button
      type="button"
      role="option"
      id={`palette-${item.id}`}
      aria-selected={selected}
      className={"palette-row" + (selected ? " selected" : "")}
      // Pointer, not focus: moving the mouse must not take focus off the input,
      // or typing stops working half way through a search.
      onMouseMove={onHover}
      onClick={onRun}
    >
      {item.badge && <span className="palette-badge" aria-hidden>{item.badge}</span>}
      <span className="palette-label">
        {before}<b>{hit}</b>{after}
      </span>
      {item.meta && <span className="palette-meta">{item.meta}</span>}
      <span className="palette-tail" aria-hidden>
        {item.action ? "ACTION" : selected ? "⏎" : ""}
      </span>
    </button>
  );
}

/** ⌘K. The app's only navigation surface — there is no nav sidebar to fall
 *  back to — so it has to cover scenes, campaigns, worlds, library sections,
 *  records and actions. It covers them by asking whoever is mounted: the shell
 *  registers the app-wide list, and a page registers what only it knows.
 *
 *  Closed by default and unmounted while closed, which is what keeps it from
 *  being persistent nav wearing a keyboard shortcut. */
export default function CommandPalette() {
  const { open, setOpen, sources, rev } = usePalette();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Open, this is what is on top of the screen, and Escape belongs to it —
  // said here rather than only on the input so that a palette opened over a
  // dossier drawer does not close the drawer underneath it as well. ⌘K is
  // exempt (it is `global`), which is how the palette stays reachable from
  // inside itself. The input keeps its own Escape: arrow keys and ⏎ have to
  // live there anyway, and one of the two paths is always the one a test of
  // this component reaches for.
  useHotkeys(
    [{ keys: "escape", enabled: open, whileTyping: true, run: () => setOpen(false) }],
    { modal: open },
  );

  // Fresh every time it opens. A palette that remembers last night's query is
  // a palette you have to clear before you can use it.
  useEffect(() => {
    if (open) { setQuery(""); setCursor(0); inputRef.current?.focus(); }
  }, [open]);

  const items = useMemo(() => {
    if (!open) return [];
    const out: PaletteItem[] = [];
    const seen = new Set<string>();
    for (const source of sources) {
      for (const item of source(query.trim())) {
        // Two sources naming the same record is normal — a campaign's cast and
        // the world's roster both hold Sister Aud. First registration wins,
        // and group order has already decided which that is.
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        if (matches(item, query.trim())) out.push(item);
      }
    }
    return out.sort((a, b) => groupRank(a.group) - groupRank(b.group));
  // `rev` rather than `sources`: the set is mutated in place, so its identity
  // never changes and depending on it alone would memoize the first result
  // list forever.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sources, rev, query]);

  // A shrinking result list must not leave the cursor past the end, where ⏎
  // would open nothing and the highlight would be invisible.
  useEffect(() => { setCursor((c) => Math.min(c, Math.max(0, items.length - 1))); }, [items.length]);

  function run(item: PaletteItem | undefined) {
    if (!item) return;
    setOpen(false);
    item.run?.();
    if (item.to) navigate(item.to);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => (items.length ? (c + 1) % items.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => (items.length ? (c - 1 + items.length) % items.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(items[cursor]);
    }
  }

  // Keep the selected row in view under arrow-key travel. `block: "nearest"`
  // rather than "center" so a two-row move does not swing the whole list.
  useEffect(() => {
    const el = listRef.current?.querySelector(".palette-row.selected") as HTMLElement | null;
    // jsdom has no layout and so no scrollIntoView; feature-detected rather
    // than shimmed in test setup, because the Android WebView is the same
    // shape of risk and a throw here takes the whole palette down.
    el?.scrollIntoView?.({ block: "nearest" });
  }, [cursor, items.length]);

  if (!open) return null;

  let lastGroup = "";
  return (
    // Click-through-to-close on the scrim; the panel stops the bubble itself.
    <div className="palette-scrim" onMouseDown={() => setOpen(false)}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Go anywhere"
           onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <span className="palette-key" aria-hidden>⌘K</span>
          <input
            ref={inputRef}
            value={query}
            placeholder="go anywhere"
            aria-label="Search"
            role="combobox"
            aria-expanded
            aria-controls="palette-results"
            aria-activedescendant={items[cursor] ? `palette-${items[cursor].id}` : undefined}
            onChange={(e) => { setQuery(e.target.value); setCursor(0); }}
            onKeyDown={onKeyDown}
          />
          <span className="palette-hint" aria-hidden>↑↓ MOVE · ⏎ OPEN · ESC CLOSE</span>
        </div>
        <div className="palette-results" id="palette-results" role="listbox" ref={listRef}>
          {items.length === 0 && (
            <p className="palette-empty">
              {query.trim() ? `Nothing matches “${query.trim()}”` : "Start typing"}
            </p>
          )}
          {items.map((item, i) => {
            const head = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.id}>
                {head && <div className="palette-group section-label">{head}</div>}
                <Row item={item} query={query.trim()} selected={i === cursor}
                     onRun={() => run(item)} onHover={() => setCursor(i)} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** ⌘K / Ctrl-K anywhere. Mounted once by the shell.
 *
 *  `whileTyping`, deliberately: the composer is where you are when you realise
 *  you need to look someone up, and the whole point of the palette is that it
 *  does not cost you the draft you are holding. `global` for the same reason
 *  one step further out — an overlay must not be able to strand you, and
 *  "everything is one keystroke away" is the promise the app's only navigation
 *  surface makes. */
export function usePaletteHotkey(): void {
  const { setOpen } = usePalette();
  useHotkeys([{
    keys: "mod+k", label: "Go anywhere", group: "ANYWHERE",
    whileTyping: true, global: true, run: () => setOpen(true),
  }]);
}
