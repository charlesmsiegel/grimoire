import { useCallback } from "react";

import { libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { StructuredValueEditor } from "./StructuredValueEditor";

const KNOWN_KEYS = [
  "starting_location",
  "default_style_guide_id",
  "default_image_preset_id",
] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

interface Props {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}

export function WorldDefaultsForm({ value, onChange }: Props) {
  const known: Record<KnownKey, string> = {
    starting_location: typeof value.starting_location === "string" ? value.starting_location : "",
    default_style_guide_id:
      typeof value.default_style_guide_id === "string" ? value.default_style_guide_id : "",
    default_image_preset_id:
      typeof value.default_image_preset_id === "string" ? value.default_image_preset_id : "",
  };
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (!(KNOWN_KEYS as readonly string[]).includes(k)) extras[k] = v;
  }

  const { data: catalog } = useResource(
    useCallback(
      () => Promise.all([libraryApi.listStyleGuides(), libraryApi.listImagePresets()]),
      [],
    ),
  );
  const styleGuides = catalog?.[0] ?? [];
  const imagePresets = catalog?.[1] ?? [];

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
      <legend>Defaults</legend>
      <label>
        <span>Starting location</span>
        <input
          type="text"
          value={known.starting_location}
          onChange={(e) => patch("starting_location", e.target.value)}
        />
      </label>
      <label>
        <span>Default style guide</span>
        <select
          value={known.default_style_guide_id}
          onChange={(e) => patch("default_style_guide_id", e.target.value)}
        >
          <option value="">(none)</option>
          {styleGuides.map((sg) => (
            <option key={sg.asset_id} value={sg.asset_id}>
              {sg.name || sg.asset_id}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Default image preset</span>
        <select
          value={known.default_image_preset_id}
          onChange={(e) => patch("default_image_preset_id", e.target.value)}
        >
          <option value="">(none)</option>
          {imagePresets.map((ip) => (
            <option key={ip.asset_id} value={ip.asset_id}>
              {ip.name || ip.asset_id}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="world-meta-extras">
        <legend>Other fields</legend>
        <StructuredValueEditor value={extras} onChange={setExtras} />
      </fieldset>
    </fieldset>
  );
}
