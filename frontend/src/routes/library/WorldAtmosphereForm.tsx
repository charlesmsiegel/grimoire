import { StructuredValueEditor } from "./StructuredValueEditor";

const KNOWN_KEYS = ["default_register", "default_palette"] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

interface Props {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}

export function WorldAtmosphereForm({ value, onChange }: Props) {
  const known: Record<KnownKey, string> = {
    default_register: typeof value.default_register === "string" ? value.default_register : "",
    default_palette: typeof value.default_palette === "string" ? value.default_palette : "",
  };
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (!(KNOWN_KEYS as readonly string[]).includes(k)) extras[k] = v;
  }

  function patch(key: KnownKey, next: string) {
    onChange({ ...value, [key]: next });
  }
  function setExtras(next: unknown) {
    const out: Record<string, unknown> = { ...known };
    if (next && typeof next === "object" && !Array.isArray(next)) {
      for (const [k, v] of Object.entries(next as Record<string, unknown>)) out[k] = v;
    }
    onChange(out);
  }

  return (
    <fieldset className="world-meta-fieldset">
      <legend>Atmosphere</legend>
      <label>
        <span>Default register</span>
        <input
          type="text"
          value={known.default_register}
          onChange={(e) => patch("default_register", e.target.value)}
        />
      </label>
      <label>
        <span>Default palette</span>
        <input
          type="text"
          value={known.default_palette}
          onChange={(e) => patch("default_palette", e.target.value)}
        />
      </label>
      <fieldset className="world-meta-extras">
        <legend>Other fields</legend>
        <StructuredValueEditor value={extras} onChange={setExtras} />
      </fieldset>
    </fieldset>
  );
}
