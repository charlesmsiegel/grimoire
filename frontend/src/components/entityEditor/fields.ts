import { ACTIVATION_FIELDS, type EntityFieldSpec, type RefKind } from "../../api/client";
import type { RecordRef } from "../../api/loreOwners";

// What each referenceable kind is called, for saying what an empty picker is
// missing (`many`) and for telling two same-named candidates apart (`one`).
// Wider than KIND_LABELS because a ref field can name an actor, which is not an
// entity kind and so has no entry there.
export const KIND_NOUNS: Record<RefKind, { one: string; many: string }> = {
  characters: { one: "character", many: "characters" },
  pcs: { one: "PC", many: "PCs" },
  locations: { one: "location", many: "locations" },
  lore: { one: "lore entry", many: "lore entries" },
  items: { one: "item", many: "items" },
  groups: { one: "group", many: "groups" },
  creatures: { one: "creature", many: "creatures" },
};

/** Display text per ref, unique within `options`.
 *
 *  Names are not unique — only ids are — and a field like `holder` offers four
 *  kinds at once, so a PC and a group can both be called Mara. Two radios
 *  reading "Mara" is a control where the reader cannot tell which relationship
 *  they are about to store, and the accessible name is just as ambiguous as
 *  the visible one.
 *
 *  Qualified only where it is needed, and by the least that settles it: the
 *  kind for a cross-kind collision, the ref itself when even that still
 *  collides (two characters really can share a name). An unambiguous candidate
 *  keeps its plain name, so the common picker is not decorated for a case it
 *  does not have. */
export function displayLabels(options: RecordRef[]): Map<string, string> {
  const byLabel = new Map<string, RecordRef[]>();
  for (const o of options) byLabel.set(o.label, [...(byLabel.get(o.label) ?? []), o]);
  const out = new Map<string, string>();
  for (const [label, group] of byLabel) {
    if (group.length === 1) { out.set(group[0].ref, label); continue; }
    const kinds = new Map<string, number>();
    for (const o of group) kinds.set(o.kind, (kinds.get(o.kind) ?? 0) + 1);
    for (const o of group) {
      out.set(o.ref, kinds.get(o.kind) === 1
        ? `${label} (${KIND_NOUNS[o.kind].one})`
        : `${label} (${o.ref})`);
    }
  }
  return out;
}

/** The union of the record kinds this kind's `ref` fields can name, in first
 *  appearance order and without repeats — what to fetch once for the whole
 *  form. Empty for a kind that declares no ref field, which is what the effect
 *  below checks before making any request at all. */
export function refKindsFor(specs: EntityFieldSpec[]): RefKind[] {
  const out: RefKind[] = [];
  for (const spec of specs) {
    for (const k of spec.kinds ?? []) if (!out.includes(k)) out.push(k);
  }
  return out;
}

/** The declared fields whose value differs from what was loaded.
 *
 *  A create passes `{}` as `loaded`, so everything the user typed is "changed"
 *  and an untouched field is simply absent — which is what `create_entity`
 *  wants anyway, since it drops empties. */
export function changedFields(
  current: Record<string, string>, loaded: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(current)) {
    if (value !== (loaded[key] ?? "")) out[key] = value;
  }
  return out;
}

/** Refs stored in one field, in the `owners:` spelling: comma-separated
 *  `<kind>:<id>`. Same parse as `entity_schema.parse_refs` on the other side.
 *
 *  Takes the undefined a `fields[key]` lookup can hand back rather than
 *  `unknown`: everything that reaches this has already been read out of the
 *  editor's own string map, and widening it would only invite a caller to
 *  stringify something that has no spelling. */
export const parseRefs = (v: string | undefined): string[] =>
  (v ?? "").split(",").map((r) => r.trim()).filter(Boolean);

/** The activation controls as the field specs the form already knows how to
 *  draw. `bool` has no spec (it is a checkbox, drawn by hand below); `refs` is
 *  the multi-ref picker over the two kinds of actor `known_by` may name. */
export const KNOWN_BY_SPEC: EntityFieldSpec = {
  key: "known_by", label: "Known by", widget: "ref", kinds: ["characters", "pcs"], multi: true,
};
export const activationSpec = (a: (typeof ACTIVATION_FIELDS)[number]): EntityFieldSpec =>
  a.widget === "refs" ? KNOWN_BY_SPEC
    : { key: a.key, label: a.label, widget: a.widget as EntityFieldSpec["widget"],
        ...(a.min === undefined ? {} : { min: a.min }),
        ...(a.max === undefined ? {} : { max: a.max }),
        ...(a.options === undefined ? {} : { options: a.options }) };
// Read at call time, not at import: this module is loaded by suites that mock
// the client without the catalog and never render an editor.
export const activationKeys = () => ACTIVATION_FIELDS.map((a) => a.key);
// Timed and windowed values count posts, and the chip says so.
export const ACTIVATION_POSTS = new Set(["sticky", "cooldown", "scan_depth"]);
// `keep` is "true" or absent on the backend; anything else reads as off there,
// so the sidebar must not draw a chip for it either.
export const isKeepOn = (v: string | undefined) => (v ?? "").trim().toLowerCase() === "true";

// What a ref that resolves to nothing says when hovered. Not an error and not
// hidden: a delete deliberately leaves refs dangling (#222), so the chip's job
// is to say the record is gone rather than to look like a field nobody filled in.
export const DANGLING_HINT = "This record no longer exists here — it may have been deleted, "
  + "or it may live outside this campaign";

// ...and what it says when we do not actually know. A candidate list that
// failed to load leaves every ref unresolved, and reporting that as "deleted"
// turns a dropped request into a claim about the store — one the reader may
// act on by clearing a relationship that was perfectly fine.
export const UNLOADED_HINT = "Could not load the list of records, so this reference could not be "
  + "resolved — it has not necessarily gone anywhere";

/** What a sourced `choice` field knows about its list: the list, `"failed"`
 *  when the request for it did not come back, and absent while it is still in
 *  flight. Three states, because two would conflate the two the picker must
 *  keep apart -- a list that has not arrived is not an empty list, and only
 *  one that has can say a stored value is not on it. */
export type ChoiceList = ChoiceOption[] | "failed";

/** The options a `choice` spec offers: its literal list, or what its source
 *  answered so far. */
export function choiceOptions(spec: EntityFieldSpec,
                       loaded: Record<string, ChoiceList>): ChoiceList | undefined {
  if (spec.options) return spec.options.map((o) => ({ value: o, label: o }));
  return loaded[spec.key];
}

export type ChoiceOption = { value: string; label: string };

/** HTML's "valid floating-point number", the grammar `<input type="number">`
 *  applies before it sanitizes a value it cannot parse to blank. `Number()`
 *  is wider (`0x10`, padded strings) and would say yes to values the input
 *  then shows as empty. */
const HTML_FLOAT = /^-?(\d+(\.\d+)?|\.\d+)([eE][+-]?\d+)?$/;

/** Can a number input show this stored value? A hand-written `wet` cannot be
 *  rendered by `<input type="number">` -- the browser sanitizes it to blank --
 *  which would make the field look unfilled while still holding the value it
 *  would resend. Such a value gets the text box instead, where it is visible. */
export function numberInputCanShow(value: string): boolean {
  return value === "" || HTML_FLOAT.test(value);
}

export const worldRefLabel = (ref: string) => `World reference: ${ref}`;
export const WORLD_BINDING_HINT = "Binding uses the present or future campaign actor with this reference.";
export const SEVERAL_REFS_HINT = "This field holds more than one reference; choose one (or None) to bind it or to save.";

