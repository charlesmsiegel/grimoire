import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { usePalette, usePaletteSource, type PaletteItem } from "../components/palette";
import { activeHotkeys, promoteScope, watchHotkeys, type HotkeyRow } from "./registry";
import { formatChord } from "./keys";
import { useHotkeys } from "./useHotkeys";

/** Most specific first: what the panel in front of you does, then what this
 *  scene does, then what works everywhere. A group nobody lists here follows,
 *  in first-seen order — same bargain the palette makes. */
const GROUP_ORDER = ["THIS PANEL", "IN THIS SCENE", "ANYWHERE"];

function rank(group: string): number {
  const at = GROUP_ORDER.indexOf(group);
  return at < 0 ? GROUP_ORDER.length : at;
}

/** `?`, and the sheet it opens: every binding registered right now, under the
 *  heading whoever registered it chose.
 *
 *  The list is read from the registry rather than written down here, so a
 *  shortcut cannot exist without being documented and cannot be documented
 *  without existing — which is the failure mode a hand-maintained help screen
 *  has (#193). A binding whose control is disabled this moment is listed and
 *  dimmed: "not right now" and "no such key" are different answers.
 *
 *  Mounted once by the shell. Unmounted while closed, like the palette. */
export default function ShortcutsHelp() {
  const [open, setOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  // Where focus was when the sheet took it. A dialog that claims `aria-modal`
  // and never takes focus is a claim nothing can act on — Tab would still walk
  // the page behind it — and one that takes focus without giving it back
  // strands the reader on `<body>` afterwards.
  const cameFrom = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  // Set when the sheet closes because the palette took over, so the focus
  // effect below knows not to put focus back: the palette has just taken it,
  // and restoring would leave a visible palette whose typing goes to whatever
  // was behind the sheet (PR #400 review).
  const yielded = useRef(false);

  const self = useHotkeys([
    // `global`, so the sheet opens over an overlay too: a reader who cannot
    // remember how to close the thing in front of them is exactly who needs
    // it, and the sheet lists that overlay's own keys.
    {
      keys: "?", label: "Keyboard shortcuts", group: "ANYWHERE", global: true,
      run: () => setOpen((v) => !v),
    },
    // Unlabelled: the head says ESC CLOSE, and a row repeating it would be the
    // one row in the sheet that is about the sheet.
    { keys: "escape", enabled: open, whileTyping: true, run: () => setOpen(false) },
  ], { modal: open });

  // What it lists is "right now", so it has to hear about right now changing:
  // a turn finishing un-dims the send row, a panel opening underneath dims the
  // scene's. Subscribed only while open, and deaf to its own scope -- this
  // component announces on its own render like every other binding owner, and
  // acting on that would be a render loop (PR #400 review).
  const [, refresh] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!open) return;
    return watchHotkeys((changed, kind) => {
      if (changed === self) return;
      // An overlay that mounted UNDER this one is not on top of it, whatever
      // registration order says: this sheet draws above every other overlay in
      // the app, so it re-asserts the position its z-index already claims.
      if (kind === "registered") promoteScope(self);
      refresh();
    });
  }, [open, self]);

  // The palette is the other surface that opens from anywhere, and it draws
  // BENEATH this one. Two of them stacked is a palette nobody can see taking
  // the keys, so this yields: ⌘K reaches past the sheet and replaces it.
  const { open: paletteOpen } = usePalette();
  useEffect(() => {
    if (!paletteOpen || !open) return;
    yielded.current = true;
    setOpen(false);
  }, [paletteOpen, open]);

  useEffect(() => {
    if (open) {
      cameFrom.current = document.activeElement as HTMLElement | null;
      sheetRef.current?.focus();
      return;
    }
    const back = cameFrom.current;
    cameFrom.current = null;
    // Nothing to give back when the palette took it: it focused its own search
    // box on the way in, and this would take it straight off again.
    if (yielded.current) { yielded.current = false; return; }
    // Only if it is still on the page: the reader may have opened this from a
    // control that has since unmounted.
    if (back?.isConnected) back.focus();
  }, [open]);

  // Typeable as well, because a key nobody can find is the gap this sheet
  // exists to close, one level up: `?` is itself undiscoverable, and the
  // palette is where this app puts everything that would otherwise need a
  // button on every screen.
  const source = useCallback((): PaletteItem[] => [{
    id: "action:shortcuts", group: "ELSEWHERE", label: "Keyboard shortcuts",
    meta: "what you can press here", action: true, run: () => setOpen(true),
  }], []);
  usePaletteSource(source);

  if (!open) return null;

  // Deduped: two panels of the same kind, or a re-registered scope, would
  // otherwise list one binding twice.
  const seen = new Set<string>();
  const rows: HotkeyRow[] = [];
  for (const row of activeHotkeys(self)) {
    if (!row.key.label) continue;
    const id = `${row.key.group ?? ""}|${row.key.keys}|${row.key.label}`;
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push(row);
  }
  const groups: Array<[string, HotkeyRow[]]> = [];
  for (const row of rows) {
    const group = row.key.group ?? "ANYWHERE";
    const found = groups.find(([g]) => g === group);
    if (found) found[1].push(row);
    else groups.push([group, [row]]);
  }
  groups.sort((a, b) => rank(a[0]) - rank(b[0]));

  return (
    // Dismissed by a press on the scrim ITSELF rather than by the sheet
    // stopping the bubble: one handler instead of two, and the sheet stays a
    // dialog with nothing bound to it.
    //
    // `aria-modal` contains nothing by itself. With one tabbable control inside
    // and Tab held here, focus cannot leave for the page behind the scrim in
    // either direction (PR #400 review).
    <div className="shortcuts-scrim" role="presentation"
         onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
         // Tab is caught out here rather than on the dialog: it covers the
         // sheet and everything in it by bubbling, and the dialog stays an
         // element with nothing bound to it.
         onKeyDown={(e) => {
           if (e.key !== "Tab") return;
           e.preventDefault();
           closeRef.current?.focus();
         }}>
      <div className="shortcuts" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
           ref={sheetRef} tabIndex={-1}>
        <div className="shortcuts-head">
          <span className="palette-key" aria-hidden>?</span>
          <h3>Keyboard shortcuts</h3>
          <span className="palette-hint" aria-hidden>ESC CLOSE</span>
          <button type="button" className="drawer-close" aria-label="Close"
                  ref={closeRef} onClick={() => setOpen(false)}>✕</button>
        </div>
        <div className="shortcuts-body">
          {groups.map(([group, keys]) => (
            <div className="shortcuts-section" key={group}>
              <div className="shortcuts-group section-label">{group}</div>
              {keys.map(({ key, reachable }) => (
                <div key={`${key.keys}|${key.label}`}
                     className={"shortcuts-row" + (reachable ? "" : " off")}>
                  <span className="shortcuts-label">{key.label}</span>
                  <kbd className="shortcuts-keys">{formatChord(key.keys)}</kbd>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
