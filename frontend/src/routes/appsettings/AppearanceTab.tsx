import { useTheme } from "../../state/useTheme";

export function AppearanceTab() {
  const { mode, setMode, fontFamily, setFontFamily, density, setDensity } = useTheme();

  return (
    <div className="settings-form">
      <fieldset className="wizard-style-mode" aria-label="Theme">
        <legend>Theme</legend>
        {(["light", "dark", "system"] as const).map((t) => (
          <label key={t}>
            <input type="radio" name="theme" checked={mode === t} onChange={() => setMode(t)} />
            <span>{t}</span>
          </label>
        ))}
      </fieldset>
      <label className="wizard-field">
        <span>Font family</span>
        <select
          value={fontFamily}
          onChange={(e) => setFontFamily(e.target.value as "system" | "serif" | "dyslexia")}
        >
          <option value="system">System</option>
          <option value="serif">Serif</option>
          <option value="dyslexia">Dyslexia-friendly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Density</span>
        <select
          value={density}
          onChange={(e) => setDensity(e.target.value as "comfortable" | "compact")}
        >
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
      </label>
      <p className="wizard-meta">
        Font family and density apply across the app and persist locally.
      </p>
    </div>
  );
}
