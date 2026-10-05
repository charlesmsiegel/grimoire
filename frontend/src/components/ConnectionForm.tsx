import type { ReactNode } from "react";
import type { LLMConnectionKind } from "../api/client";
import type { VisionOverride } from "../api/types";
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
  reasoning_effort?: "" | "low" | "high" | "max";
  /** Edited by the Connections page beside this form, not by it: the setup
   *  wizard shares the form and has no presets to offer yet. */
  sampler_preset?: string;
  sampler_support?: "" | "standard" | "extended";
  /** Whether the model may be sent post images (#377); `""` follows the catalog. */
  vision?: VisionOverride;
};

export const BLANK_CONNECTION: ConnectionFormValue = {
  kind: "openrouter", name: "", base_url: "", model: "", post_process: "none", reasoning_effort: "",
  vision: "",
};

/** The kind/name/credentials/model fields of an LLM connection.
 *
 *  Shared by the Connections page's editor and the first-run wizard (#194):
 *  the branching per kind (OpenRouter wants a key and an OpenRouter model,
 *  Claude wants neither, an OpenAI-compatible endpoint -- a local Ollama,
 *  llama.cpp, LM Studio or vLLM server, or a hosted one -- wants a base URL, an
 *  optional key and its own model list) is the part worth having exactly once.
 *
 *  Kept dumb on purpose — no fetching, no saving. `models` arrives as data and
 *  `modelsHint` as a slot, because the affordance that belongs there differs by
 *  caller: the editor offers a refresh of a saved connection's cached model
 *  list, and a wizard creating its first connection has nothing to refresh yet.
 *
 *  One `models` list, not one per kind (#149). It used to take OpenRouter's
 *  catalog and a custom endpoint's separately, which is what let the OpenRouter
 *  field show OpenRouter's models no matter which provider was configured; the
 *  caller now resolves "this connection's catalog" once and the branching here
 *  is only over which *fields* a kind has. */
export function ConnectionForm({
  value, onChange, apiKey, onApiKey, keySet = false, lockKind = false,
  models = [], modelsError = false, modelsHint,
}: {
  value: ConnectionFormValue;
  onChange: (next: ConnectionFormValue) => void;
  apiKey: string;
  onApiKey: (key: string) => void;
  /** A key is already stored server-side, so the field is a replace-or-leave. */
  keySet?: boolean;
  /** Kind is immutable once a connection exists — the stored shape depends on it. */
  lockKind?: boolean;
  /** This connection's own catalog, however the caller got it. */
  models?: Model[];
  /** The catalog could not be loaded — the combobox falls back to free text. */
  modelsError?: boolean;
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
          <option value="openai_compatible">OpenAI-compatible (Ollama, llama.cpp, LM Studio, …)</option>
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
                           models={models} error={modelsError} />
          </Field>
          {modelsHint}
          <VisionField value={value} models={models} set={set} />
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
          <Field label="Base URL"
                 hint="The address that ends in /v1. A local server on another machine (or reached from the Android app) is http://<that computer's LAN address>:<port>/v1, and has to be listening on the network rather than only on localhost.">
            <input type="text" placeholder="https://api.example.com/v1" value={value.base_url}
                   onChange={(e) => set({ base_url: e.target.value })} />
          </Field>
          <div className="field-hint" role="group" aria-label="Local server presets">
            Local servers on this machine:{" "}
            {LOCAL_PRESETS.map((p, i) => (
              <span key={p.label}>
                {i > 0 && " · "}
                <button type="button" className="link" title={p.url}
                        onClick={() => set({ base_url: p.url })}>{p.label}</button>
              </span>
            ))}
          </div>
          <Field label="API key" hint="Optional — leave blank for servers that don't require auth.">
            <input type="password" placeholder={keySet ? "A key is set — type to replace" : "(optional)"}
                   value={apiKey} onChange={(e) => onApiKey(e.target.value)} />
          </Field>
          <Field label="Model">
            <ModelCombobox value={value.model} onChange={(v) => set({ model: v })}
                           models={models} error={modelsError} />
          </Field>
          {modelsHint}
          {["glm-5.3", "glm-5.3-flash"].includes(value.model.toLowerCase().split("/").pop() ?? "") && (
            <Field label="Reasoning effort" hint="GLM 5.3 defaults to Max. Low asks for less thinking; it is not a fixed token limit. Thinking cannot be disabled for this model.">
              <select aria-label="Reasoning effort" value={value.reasoning_effort ?? ""}
                      onChange={(e) => set({ reasoning_effort: e.target.value as ConnectionFormValue["reasoning_effort"] })}>
                <option value="">Provider default</option>
                <option value="low">Low</option>
                <option value="high">High</option>
                <option value="max">Max</option>
              </select>
            </Field>
          )}
          <VisionField value={value} models={models} set={set} />
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

/** Each server's out-of-the-box OpenAI-compatible root on this machine.
 *
 *  A preset fills the field and nothing more: the port is only the default,
 *  and the reader can still edit it before saving. Every one of these speaks
 *  `POST /chat/completions`, and all but the oldest builds serve `GET /models`,
 *  which is what "Fetch models" and "Test connection" ask. */
const LOCAL_PRESETS: { label: string; url: string }[] = [
  { label: "Ollama", url: "http://localhost:11434/v1" },
  { label: "llama.cpp", url: "http://localhost:8080/v1" },
  { label: "LM Studio", url: "http://localhost:1234/v1" },
  { label: "vLLM", url: "http://localhost:8000/v1" },
  { label: "KoboldCpp", url: "http://localhost:5001/v1" },
  { label: "text-generation-webui", url: "http://localhost:5000/v1" },
];

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

/** What the catalog says about the selected model's image input (#377). */
function catalogSays(models: Model[], model: string): string {
  const vision = models.find((m) => m.id === model)?.vision;
  if (vision === true) return "Catalog: reads images.";
  if (vision === false) return "Catalog: text only.";
  return "Catalog: not stated.";
}

/** The image override (#377). Not offered for Claude, whose client sends every
 *  message as one string and so can never carry a picture. */
function VisionField({ value, models, set }: {
  value: ConnectionFormValue; models: Model[];
  set: (patch: Partial<ConnectionFormValue>) => void;
}) {
  return (
    <Field label="Reads images"
           hint={`${catalogSays(models, value.model)} Auto follows the model list; when it does `
                 + "not say, post images are not sent. Sending images to a model that cannot read "
                 + "them fails the turn."}>
      <select aria-label="Reads images" value={value.vision ?? ""}
              onChange={(e) => set({ vision: e.target.value as VisionOverride })}>
        <option value="">Auto</option>
        <option value="on">Yes</option>
        <option value="off">No</option>
      </select>
    </Field>
  );
}
