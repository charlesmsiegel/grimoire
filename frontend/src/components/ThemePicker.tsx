import { themeList } from "../theme/themes";

/** The theme cards, shared by the Configuration page and the first-run wizard
 *  (#194). Stateless: the current theme lives with whoever persists it.
 *
 *  `disabled` is for a caller whose `onPick` writes asynchronously. Two picks
 *  in flight at once are two `PUT /api/config` calls, which is an unlocked
 *  read-modify-write of one file racing itself, and whichever response lands
 *  last decides the stored theme regardless of which card is showing as
 *  active. */
export function ThemePicker(
  { value, onPick, disabled = false }:
  { value: string; onPick: (name: string) => void; disabled?: boolean },
) {
  return (
    <div className="theme-cards">
      {themeList.map((t) => (
        <button
          key={t.name}
          className={"theme-card" + (value === t.name ? " active" : "")}
          onClick={() => onPick(t.name)}
          disabled={disabled}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
