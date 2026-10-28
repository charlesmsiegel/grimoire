import type { Edges, GreetingDetail } from "../../api/client";

export const BLANK = {
  name: "", character: "", version: "", body: "", present: [] as string[],
  requires_tags: [] as string[], predecessor_join: "all" as "all" | "any",
  pcless: false, location: "", phase: "", sequence: null as number | null, optional: false,
};

/** Same edges, order included: these are stored as written, and a reorder is a
 *  change this form has no way to have made by accident. */
export const sameEdges = (a: Edges, b: Edges) =>
  a.leads_to.join("\u0000") === b.leads_to.join("\u0000")
  && a.excludes.join("\u0000") === b.excludes.join("\u0000");
export const NO_EDGES: Edges = { leads_to: [], excludes: [] };

export type GreetingDraft = typeof BLANK;

/** `list` with `id` added, or taken out if it is already there. */
export const toggled = (list: string[], id: string) =>
  list.includes(id) ? list.filter((x) => x !== id) : [...list, id];


/** The editor keeps the same defaults for current and older greeting files. */
export function greetingDraft(g: GreetingDetail): GreetingDraft {
  return {
    name: g.meta.name, character: g.meta.character, version: g.meta.version,
    body: g.body.trim(), present: g.meta.present ?? [], requires_tags: g.meta.requires_tags,
    predecessor_join: g.meta.predecessor_join, pcless: g.meta.pcless ?? false,
    location: g.meta.location ?? "", phase: g.meta.phase ?? "",
    sequence: g.meta.sequence ?? null, optional: g.meta.optional ?? false,
  };
}

export function repointPrimary(form: GreetingDraft, next: string): GreetingDraft {
  // A re-point follows the primary into the cast: the old
  // character's chip becomes the new one's (deduped if both
  // were present), and a non-empty cast that lacks the new
  // primary gains it -- the speaker must not be absent from
  // its own opener. An EMPTY cast stays empty (the store
  // falls back to the primary), and clearing to narrator
  // leaves the chips to the reader.
  let present = form.present;
  if (next) {
    present = present
      .map((id) => (form.character && id === form.character ? next : id))
      .filter((id, i, arr) => arr.indexOf(id) === i);
    if (present.length > 0 && !present.includes(next)) {
      present = [...present, next];
    }
  }
  return { ...form, character: next, version: "", present };
}
