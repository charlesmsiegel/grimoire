import { themeList } from "../theme/themes";

/** The theme cards, shared by the Configuration page and the first-run wizard
 *  (#194). Stateless: the current theme lives with whoever persists it. */
export function ThemePicker({ value, onPick }: { value: string; onPick: (name: string) => void }) {
  return (
    <div className="theme-cards">
      {themeList.map((t) => (
        <button
          key={t.name}
          className={"theme-card" + (value === t.name ? " active" : "")}
          onClick={() => onPick(t.name)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
