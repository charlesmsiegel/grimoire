import { useCallback, useState } from "react";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { activeHotkeys, type Hotkey } from "./registry";
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

  useHotkeys([
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
  const rows: Hotkey[] = [];
  for (const key of activeHotkeys()) {
    if (!key.label) continue;
    const id = `${key.group ?? ""}|${key.keys}|${key.label}`;
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push(key);
  }
  const groups: Array<[string, Hotkey[]]> = [];
  for (const key of rows) {
    const group = key.group ?? "ANYWHERE";
    const found = groups.find(([g]) => g === group);
    if (found) found[1].push(key);
    else groups.push([group, [key]]);
  }
  groups.sort((a, b) => rank(a[0]) - rank(b[0]));

  return (
    // Dismissed by a press on the scrim ITSELF rather than by the sheet
    // stopping the bubble: one handler instead of two, and the sheet stays a
    // dialog with nothing bound to it.
    <div className="shortcuts-scrim" role="presentation"
         onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
      <div className="shortcuts" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
        <div className="shortcuts-head">
          <span className="palette-key" aria-hidden>?</span>
          <h3>Keyboard shortcuts</h3>
          <span className="palette-hint" aria-hidden>ESC CLOSE</span>
        </div>
        <div className="shortcuts-body">
          {groups.map(([group, keys]) => (
            <div className="shortcuts-section" key={group}>
              <div className="shortcuts-group section-label">{group}</div>
              {keys.map((key) => (
                <div key={`${key.keys}|${key.label}`}
                     className={"shortcuts-row" + (key.enabled === false ? " off" : "")}>
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
