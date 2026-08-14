import { MODES } from "../theme/themes";

/** The Light / Dark / System segmented control, shared by the Configuration
 *  page and the first-run wizard (#194). Stateless: the current mode lives
 *  with whoever persists it.
 *
 *  `disabled` is for a caller whose `onPick` writes asynchronously. Two picks
 *  in flight at once are two `PUT /api/config` calls, which is an unlocked
 *  read-modify-write of one file racing itself, and whichever response lands
 *  last decides the stored theme regardless of which segment is showing as
 *  active. */
export function ThemePicker(
  { value, onPick, disabled = false }:
  { value: string; onPick: (mode: string) => void; disabled?: boolean },
) {
  return (
    <div className="mode-switch" role="group" aria-label="Appearance">
      {MODES.map((m) => (
        <button
          key={m.mode}
          type="button"
          className={"mode-option" + (value === m.mode ? " active" : "")}
          aria-pressed={value === m.mode}
          onClick={() => onPick(m.mode)}
          disabled={disabled}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
