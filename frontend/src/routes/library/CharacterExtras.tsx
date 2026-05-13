/**
 * Character-specific frontmatter sub-editors: voice card and image prompt
 * template (see `data/library/.../characters/<id>.md` in spec 18). The rest
 * of the frontmatter falls through to the generic editor.
 */

import type { Frontmatter, FrontmatterValue } from "./frontmatter";

interface Props {
  frontmatter: Frontmatter;
  onChange: (next: Frontmatter) => void;
}

interface Voice {
  summary?: string;
  register?: string;
  samples?: string[];
  address_terms?: Record<string, string>;
  dos?: string[];
  donts?: string[];
}

interface Image {
  base_prompt?: string;
  negative_prompt?: string;
  canonical_seed?: number;
}

function asVoice(v: FrontmatterValue | undefined): Voice {
  if (!v || typeof v !== "object" || Array.isArray(v)) return {};
  return v as Voice;
}

function asImage(v: FrontmatterValue | undefined): Image {
  if (!v || typeof v !== "object" || Array.isArray(v)) return {};
  return v as Image;
}

function asStringList(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

function setKey(fm: Frontmatter, key: string, value: FrontmatterValue): Frontmatter {
  return { ...fm, [key]: value };
}

export function CharacterExtras({ frontmatter, onChange }: Props) {
  const voice = asVoice(frontmatter.voice);
  const image = asImage(frontmatter.image);
  const samples = asStringList(voice.samples);
  const dos = asStringList(voice.dos);
  const donts = asStringList(voice.donts);

  function patchVoice(patch: Partial<Voice>) {
    onChange(setKey(frontmatter, "voice", { ...voice, ...patch } as FrontmatterValue));
  }
  function patchImage(patch: Partial<Image>) {
    onChange(setKey(frontmatter, "image", { ...image, ...patch } as FrontmatterValue));
  }

  return (
    <div className="character-extras">
      <fieldset className="character-card">
        <legend>Identity</legend>
        <label>
          <span>Name</span>
          <input
            type="text"
            value={typeof frontmatter.name === "string" ? frontmatter.name : ""}
            onChange={(e) => onChange(setKey(frontmatter, "name", e.target.value))}
          />
        </label>
        <label>
          <span>ID</span>
          <input
            type="text"
            value={typeof frontmatter.id === "string" ? frontmatter.id : ""}
            onChange={(e) => onChange(setKey(frontmatter, "id", e.target.value))}
          />
        </label>
      </fieldset>

      <fieldset className="character-card character-voice">
        <legend>Voice</legend>
        <label>
          <span>Summary</span>
          <textarea
            rows={2}
            value={voice.summary ?? ""}
            onChange={(e) => patchVoice({ summary: e.target.value })}
          />
        </label>
        <label>
          <span>Register</span>
          <input
            type="text"
            value={voice.register ?? ""}
            onChange={(e) => patchVoice({ register: e.target.value })}
          />
        </label>
        <StringListEditor
          label="Sample lines"
          value={samples}
          onChange={(next) => patchVoice({ samples: next })}
          textarea
        />
        <StringListEditor label="Dos" value={dos} onChange={(next) => patchVoice({ dos: next })} />
        <StringListEditor
          label="Don'ts"
          value={donts}
          onChange={(next) => patchVoice({ donts: next })}
        />
      </fieldset>

      <fieldset className="character-card character-image">
        <legend>Image prompt template</legend>
        <label>
          <span>Base prompt</span>
          <textarea
            rows={3}
            value={image.base_prompt ?? ""}
            onChange={(e) => patchImage({ base_prompt: e.target.value })}
          />
        </label>
        <label>
          <span>Negative prompt</span>
          <textarea
            rows={2}
            value={image.negative_prompt ?? ""}
            onChange={(e) => patchImage({ negative_prompt: e.target.value })}
          />
        </label>
        <label>
          <span>Canonical seed</span>
          <input
            type="number"
            value={image.canonical_seed ?? 0}
            onChange={(e) => patchImage({ canonical_seed: Number(e.target.value) })}
          />
        </label>
      </fieldset>
    </div>
  );
}

function StringListEditor({
  label,
  value,
  onChange,
  textarea,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  textarea?: boolean;
}) {
  return (
    <div className="string-list-editor">
      <span className="string-list-label">{label}</span>
      <ul>
        {value.map((s, idx) => (
          <li key={idx}>
            {textarea ? (
              <textarea
                rows={2}
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            ) : (
              <input
                type="text"
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            )}
            <button
              type="button"
              aria-label="Remove"
              onClick={() => {
                const next = value.filter((_, i) => i !== idx);
                onChange(next);
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => onChange([...value, ""])}>
        + Add
      </button>
    </div>
  );
}
