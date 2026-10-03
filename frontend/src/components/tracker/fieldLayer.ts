import type { TrackerField, TrackerLayer } from "../../api/client";

/** The pure half of the field-layer editors: validation that mirrors
 *  `store/tracker/fields.py`, and the functions that build the NEXT layer.
 *
 *  Every builder starts from the layer as STORED (the bundle's `layer`), never
 *  from the effective list: writing effective fields back would freeze every
 *  inherited definition into this layer, and the world could no longer change
 *  one underneath it. A leaf module with no React and no `api`, so a test can
 *  hold the rules without mounting anything. */

/** `fields._KEY`, mirrored so a form can say so before a round trip. */
const KEY = /^[a-z][a-z0-9_]{0,31}$/;
export const KEY_RULE = "lowercase letters, digits and underscores, starting with a letter, "
  + "at most 32 characters";

/** Why `key` is not a field key, or null. */
export const keyProblem = (key: string): string | null =>
  KEY.test(key) ? null : `A key is ${KEY_RULE}.`;

/** A whole layer with every part present, so a builder never branches on absence. */
export type FullLayer = {
  fields: TrackerField[]; change: Record<string, Partial<TrackerField>>; off: string[];
};

export const fullLayer = (layer: TrackerLayer | undefined): FullLayer => ({
  fields: layer?.fields ?? [], change: layer?.change ?? {}, off: layer?.off ?? [],
});

/** What a form edits. `options` is the comma list as typed. */
export type FieldDraft = {
  key: string; label: string; hint: string;
  type: TrackerField["type"]; options: string; aware: TrackerField["aware"];
};

export const BLANK_DRAFT: FieldDraft =
  { key: "", label: "", hint: "", type: "text", options: "", aware: "present" };

export const splitOptions = (text: string): string[] =>
  text.split(",").map((s) => s.trim()).filter(Boolean);

export const draftOf = (f: TrackerField): FieldDraft => ({
  key: f.key, label: f.label, hint: f.hint, type: f.type,
  options: (f.options ?? []).join(", "), aware: f.aware,
});

/** A key from a label, for a form whose key box has not been touched. */
export const keyFromLabel = (label: string): string =>
  label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
    .replace(/^[0-9_]+/, "").slice(0, 32).replace(/_+$/, "");

/** The first reason `draft` cannot be saved, or null. `taken` is every key the
 *  layer's field list already holds, off ones included; it is consulted only
 *  for a new field (`keyFixed` false), whose key the form lets the reader pick. */
export function draftProblem(draft: FieldDraft, opts: { keyFixed: boolean; taken: readonly string[] }): string | null {
  if (!opts.keyFixed) {
    const bad = keyProblem(draft.key);
    if (bad) return bad;
    if (opts.taken.includes(draft.key)) return `A field with the key "${draft.key}" already exists.`;
  }
  if (!draft.label.trim()) return "A field needs a label.";
  if (draft.type === "enum" && splitOptions(draft.options).length === 0) {
    return "An enum needs at least one option.";
  }
  return null;
}

/** A draft as a whole field. Options only for an enum, because the server
 *  refuses them on any other type. */
export function fieldOf(draft: FieldDraft): TrackerField {
  const field: TrackerField = {
    key: draft.key, label: draft.label.trim(), type: draft.type,
    aware: draft.aware, hint: draft.hint,
  };
  if (draft.type === "enum") field.options = splitOptions(draft.options);
  return field;
}

const sameList = (a: readonly string[] | undefined, b: readonly string[] | undefined) =>
  JSON.stringify(a ?? []) === JSON.stringify(b ?? []);

/** The properties `draft` sets that differ from `inherited`, which is what a
 *  `change` entry holds. Options ride along only for an enum, and only when
 *  they moved or the field has just become one. */
export function changeOf(inherited: TrackerField, draft: FieldDraft): Partial<TrackerField> {
  const change: Partial<TrackerField> = {};
  if (draft.label.trim() !== inherited.label) change.label = draft.label.trim();
  if (draft.hint !== inherited.hint) change.hint = draft.hint;
  if (draft.type !== inherited.type) change.type = draft.type;
  if (draft.aware !== inherited.aware) change.aware = draft.aware;
  if (draft.type === "enum") {
    const options = splitOptions(draft.options);
    if (inherited.type !== "enum" || !sameList(options, inherited.options)) change.options = options;
  }
  return change;
}

const without = <T>(list: readonly T[], drop: T): T[] => list.filter((x) => x !== drop);

/** `layer` with `key`'s change entry set to `change`, or dropped if it is empty
 *  (an edit that put the field back as inherited is no change at all). */
export function withChange(layer: FullLayer, key: string, change: Partial<TrackerField>): FullLayer {
  const next = { ...layer.change };
  if (Object.keys(change).length === 0) delete next[key]; else next[key] = change;
  return { ...layer, change: next };
}

/** Drop this layer's change to `key`. */
export const revert = (layer: FullLayer, key: string): FullLayer => withChange(layer, key, {});

export const switchOff = (layer: FullLayer, key: string): FullLayer =>
  layer.off.includes(key) ? layer : { ...layer, off: [...layer.off, key] };

export const switchOn = (layer: FullLayer, key: string): FullLayer =>
  ({ ...layer, off: without(layer.off, key) });

/** Remove a field this layer added. Its key leaves `off` as well: a layer that
 *  later re-adds the key should not find it already switched off. */
export const removeAdded = (layer: FullLayer, key: string): FullLayer =>
  ({ ...layer, fields: layer.fields.filter((f) => f.key !== key), off: without(layer.off, key) });

/** Add `field`, or rewrite it in place if this layer already has the key. */
export function upsertAdded(layer: FullLayer, field: TrackerField): FullLayer {
  const at = layer.fields.findIndex((f) => f.key === field.key);
  const fields = at < 0 ? [...layer.fields, field]
    : layer.fields.map((f, i) => (i === at ? field : f));
  return { ...layer, fields };
}
