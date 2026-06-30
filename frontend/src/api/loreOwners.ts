import { api } from "./client";

export type LoreOwner = { ref: string; label: string; kind: "characters" | "pcs" | "locations" };

/** All records in a world that can own lore, as selectable owner refs. */
export async function loreOwnerOptions(wid: string): Promise<LoreOwner[]> {
  const [chars, pcs, locs] = await Promise.all([
    api.listCharacters(wid),
    api.listPCs(wid),
    api.listEntities({ kind: "world", id: wid }, "locations"),
  ]);
  return [
    ...chars.map((c) => ({ ref: `characters:${c.id}`, label: c.name, kind: "characters" as const })),
    ...pcs.map((p) => ({ ref: `pcs:${p.id}`, label: p.name, kind: "pcs" as const })),
    ...locs.map((l) => ({ ref: `locations:${l.id}`, label: l.name, kind: "locations" as const })),
  ];
}
