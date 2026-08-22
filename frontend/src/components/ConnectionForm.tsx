import type { ReactNode } from "react";
import type { LLMConnectionKind } from "../api/client";
import type { Model } from "../api/models";
import ModelCombobox from "../routes/ModelCombobox";
import { Field } from "./Field";

/** Everything a connection is, minus its identity and its key. The key is
 *  separate because it is write-only — the server never sends one back, so it
 *  can't round-trip through the same value the other fields do. */
export type ConnectionFormValue = {
  kind: LLMConnectionKind;
  name: string;
  base_url: string;
  model: string;
  post_process: "none" | "strict";
};

export const BLANK_CONNECTION: ConnectionFormValue = {
  kind: "openrouter", name: "", base_url: "", model: "", post_process: "none",
};

/** The kind/name/credentials/model fields of an LLM connection.
 *
 *  Shared by the Connections page's editor and the first-run wizard (#194):
 *  the branching per kind (OpenRouter wants a key and an OpenRouter model,
 *  Claude wants neither, a custom endpoint wants a base URL and its own model
 *  list) is the part worth having exactly once.
 *
 *  Kept dumb on purpose — no fetching, no saving. `orModels`/`cachedModels`
 *  arrive as data and `modelsHint` as a slot, because the affordance that
 *  belongs there differs by caller: the editor offers a refresh of a saved
 *  connection's cached model list, and a wizard creating its first connection
 *  has nothing to refresh yet. */
export function ConnectionForm({
  value, onChange, apiKey, onApiKey, keySet = false, lockKind = false,
  orModels = [], orError = false, cachedModels = [], modelsHint,
}: {
  value: ConnectionFormValue;
  onChange: (next: ConnectionFormValue) => void;
  apiKey: string;
  onApiKey: (key: string) => void;
  /** A key is already stored server-side, so the field is a replace-or-leave. */
  keySet?: boolean;
  /** Kind is immutable once a connection exists — the stored shape depends on it. */
  lockKind?: boolean;
  orModels?: Model[];
  orError?: boolean;
  cachedModels?: Model[];
  modelsHint?: ReactNode;
}) {
  const set = (patch: Partial<ConnectionFormValue>) => onChange({ ...value, ...patch });

  return (
    <>
      <Field label="Kind">
        <select value={value.kind} disabled={lockKind}
                onChange={(e) => set({ kind: e.target.value as LLMConnectionKind })}>
          <option value="openrouter">OpenRouter</option>
          <option value="claude">Claude</option>
          <option value="openai_compatible">Custom (OpenAI-compatible)</option>
        </select>
      </Field>
      <Field label="Name">
        <input type="text" value={value.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>

      {value.kind === "openrouter" && (
        <>
          <Field label="API key">
            <input type="password" placeholder={keySet ? "A key is set — type to replace" : "sk-or-…"}
                   value={apiKey} onChange={(e) => onApiKey(e.target.value)} />
          </Field>
          <Field label="Model">
            <ModelCombobox value={value.model} onChange={(v) => set({ model: v })}
                           models={orModels} error={orError} />
          </Field>
        </>
      )}

      {value.kind === "claude" && (
        <Field label="Claude model">
          <select aria-label="Claude model" value={value.model}
                  onChange={(e) => set({ model: e.target.value })}>
            <optgroup label="Latest">
              {CLAUDE_ALIASES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
            <optgroup label="Pinned versions">
              {CLAUDE_PINNED.map((mid) => <option key={mid} value={mid}>{mid}</option>)}
            </optgroup>
            {value.model &&
              !CLAUDE_ALIASES.some((m) => m.id === value.model) &&
              !CLAUDE_PINNED.includes(value.model) && (
                <optgroup label="Custom">
                  <option value={value.model}>{value.model}</option>
                </optgroup>
              )}
          </select>
        </Field>
      )}

      {value.kind === "openai_compatible" && (
        <>
          <Field label="Base URL">
            <input type="text" placeholder="https://api.example.com/v1" value={value.base_url}
                   onChange={(e) => set({ base_url: e.target.value })} />
          </Field>
          <Field label="API key" hint="Optional — leave blank for servers that don't require auth.">
            <input type="password" placeholder={keySet ? "A key is set — type to replace" : "(optional)"}
                   value={apiKey} onChange={(e) => onApiKey(e.target.value)} />
          </Field>
          <Field label="Model">
            <ModelCombobox value={value.model} onChange={(v) => set({ model: v })}
                           models={cachedModels} />
          </Field>
          {modelsHint}
          <Field label="Prompt post-processing"
                 hint="Strict folds system messages into user turns and forces the sequence to start with a user turn — needed by some coding-style endpoints (e.g. z.ai's GLM) that reject a system message mid-conversation.">
            <select value={value.post_process}
                    onChange={(e) => set({ post_process: e.target.value as "none" | "strict" })}>
              <option value="none">None</option>
              <option value="strict">Strict</option>
            </select>
          </Field>
        </>
      )}
    </>
  );
}

// Aliases resolve to the newest model of each tier at request time (the Agent
// SDK passes them through to Claude Code); pinned ids freeze a version and
// need a refresh here when new models ship.
const CLAUDE_ALIASES = [
  { id: "fable", label: "Fable (latest)" },
  { id: "opus", label: "Opus (latest)" },
  { id: "sonnet", label: "Sonnet (latest)" },
  { id: "haiku", label: "Haiku (latest)" },
];
const CLAUDE_PINNED = [
  "claude-fable-5",
  "claude-opus-4-8",
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];

/** The same roster as the `<select>` above, in the shape `ModelCombobox` reads.
 *
 *  Exported because a Claude connection is the one kind with no model *list* to
 *  fetch — OpenRouter has a catalog and a custom endpoint has its cached
 *  sidecar — so any other picker offering Claude models has to get them from
 *  here or hard-code a second copy that drifts the next time a model ships.
 *  Priced and sized as null: this file knows the ids, not the tariff. */
export const CLAUDE_MODEL_OPTIONS: Model[] = [
  ...CLAUDE_ALIASES.map((m) => ({ id: m.id, name: m.label })),
  ...CLAUDE_PINNED.map((id) => ({ id, name: id })),
].map((m) => ({ ...m, context: null, prompt: null, completion: null }));
