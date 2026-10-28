import { SECRECY_LEVELS, type EntityKind, type Secrecy } from "../../api/client";

export const KIND_LABELS: Record<EntityKind, string> = {
  locations: "location", lore: "lore entry", items: "item", groups: "group", creatures: "creature",
};

// What a record's body costs when it reaches a prompt (#51). Counted on the
// server with the same tokenizer the context inspector uses; the frontend only
// formats it.
//
// The same TOKENIZER, not the same total: the World info section is the
// activated bodies joined with blank lines, and BPE is not additive, so these
// badges summed come out a token or so per join UNDER the inspector's row for
// that section. Near enough to reason with, and not a figure to reconcile
// arithmetically — the inspector remains the authority on what a prompt cost.
//
// The cost is per ACTIVATION, not per turn, and the tooltip says so: a keyed
// entry pays it on the turns its keys hit and nothing on the rest, so a big
// number here is only a big prompt if the entry is always-on. Reading it as a
// standing cost is the obvious wrong conclusion to draw from a bare number.
//
// It is also a CEILING for a body that uses macros, which the tooltip says
// because the rail has no room for a second line: the prompt gets the expanded
// text, so `{{random:a,b,c}}` collapses to one option and the stored form can
// measure double what is sent. Exact for the ordinary body that uses none.
const TOKEN_HINT =
  "Tokens this body costs each time it enters the prompt — keyed entries pay it only when they "
  + "activate, and macros are counted unexpanded";

// A gm-only body is dropped before every activation rule (#49), so it costs
// nothing, ever. The count is still shown -- it is the size of the text, and it
// is what the entry would cost the moment someone marks it public again -- but
// it has to LOOK spent-out rather than sit there reading like a live charge.
const TOKEN_HINT_NEVER =
  "This body never reaches the prompt while it is GM-only — what it would cost if published";

export function tokenLabel(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? "token" : "tokens"}`;
}

const isNeverCharged = (level: Secrecy) => level === "gm-only";
export const tokenClass = (level: Secrecy, base = "token-badge") =>
  base + (isNeverCharged(level) ? " never-charged" : "");
export const tokenTitle = (level: Secrecy) => (isNeverCharged(level) ? TOKEN_HINT_NEVER : TOKEN_HINT);
// Struck-through text does not announce as struck through, so the exemption has
// to be in the name rather than only in the styling.
export const tokenAria = (n: number, level: Secrecy) =>
  isNeverCharged(level) ? `${tokenLabel(n)}, never charged` : tokenLabel(n);

// What each secrecy level does to the prompt, in the words the picker shows.
// "this text", not "this entry": secrecy gates the BODY, and the record still
// has a name the app uses where it must refer to the place at all — the
// scene-suggestion picker, a mechanics sheet label. Claiming the whole entry
// disappears would be a promise the app does not keep.
export const SECRECY_HINTS: Record<Secrecy, string> = {
  public: "activates normally; any character may know it",
  secret: "activates normally, but uninvolved characters must not reveal it",
  "gm-only": "this text never reaches the model; the name may still be used",
};

// Anything unrecognised (a hand-edited file) reads as public, exactly as
// store.entities.normalize_secrecy does on the other side -- INCLUDING the trim
// and the lowercase. Matching only the canonical spelling looked harmless and
// was not: the backend reads `secrecy: Secret` as secret and keeps the entry
// out of ignorant characters' mouths, while this returned "public", badged the
// entry Public, and made the next save send `secrecy: "public"` -- a valid
// level, so the route accepts it and the key is deleted. Editing the body of a
// hand-marked secret silently published it.
export const asSecrecy = (v: unknown): Secrecy => {
  // JSON singleton arrays historically stringify like their scalar. Unwrap
  // only that recognized form; arbitrary objects must never supply a coercion.
  // A hand-built cyclic array is not JSON, but must still fail safely.
  const seen = new Set<unknown>();
  while (Array.isArray(v)) {
    if (v.length !== 1 || seen.has(v)) return "public";
    seen.add(v);
    v = v[0];
  }
  const level = typeof v === "string" ? v.trim().toLowerCase() : "";
  return (SECRECY_LEVELS as readonly string[]).includes(level) ? (level as Secrecy) : "public";
};

