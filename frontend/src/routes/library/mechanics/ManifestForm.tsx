import type { ManifestSpec } from "../../../api/library/mechanics";

interface Props {
  value: ManifestSpec;
  onChange: (next: ManifestSpec) => void;
  idEditable: boolean;
}

function listValue(v: string[] | undefined): string {
  return (v ?? []).join(", ");
}

function parseList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function ManifestForm({ value, onChange, idEditable }: Props) {
  function set<K extends keyof ManifestSpec>(key: K, v: ManifestSpec[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="manifest-form">
      <label htmlFor="m-id">ID</label>
      <input
        id="m-id"
        value={value.id}
        disabled={!idEditable}
        onChange={(e) => set("id", e.target.value)}
      />

      <label htmlFor="m-name">Name</label>
      <input id="m-name" value={value.name} onChange={(e) => set("name", e.target.value)} />

      <label htmlFor="m-version">Version</label>
      <input
        id="m-version"
        value={value.version}
        onChange={(e) => set("version", e.target.value)}
      />

      <label htmlFor="m-api">API version</label>
      <input
        id="m-api"
        value={value.api_version}
        onChange={(e) => set("api_version", e.target.value)}
      />

      <label htmlFor="m-author">Author</label>
      <input
        id="m-author"
        value={value.author ?? ""}
        onChange={(e) => set("author", e.target.value)}
      />

      <label htmlFor="m-homepage">Homepage</label>
      <input
        id="m-homepage"
        value={value.homepage ?? ""}
        onChange={(e) => set("homepage", e.target.value)}
      />

      <label htmlFor="m-desc">Description</label>
      <textarea
        id="m-desc"
        value={value.description ?? ""}
        onChange={(e) => set("description", e.target.value)}
      />

      <label htmlFor="m-sheets">Sheet kinds</label>
      <input
        id="m-sheets"
        value={listValue(value.sheet_kinds)}
        placeholder="character, item"
        onChange={(e) => set("sheet_kinds", parseList(e.target.value))}
      />

      <label htmlFor="m-content">Content kinds</label>
      <input
        id="m-content"
        value={listValue(value.content_kinds)}
        placeholder="spells, disciplines"
        onChange={(e) => set("content_kinds", parseList(e.target.value))}
      />

      <label htmlFor="m-caps">Capabilities</label>
      <input
        id="m-caps"
        value={listValue(value.capabilities)}
        placeholder="dice, combat"
        onChange={(e) => set("capabilities", parseList(e.target.value))}
      />
    </div>
  );
}
