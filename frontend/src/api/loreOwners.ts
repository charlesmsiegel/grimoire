import { api, type EntityScope } from "./client";

export type LoreOwner = { ref: string; label: string; kind: "characters" | "pcs" | "locations"; avatar?: string };

/** All records in a container (world or campaign copy) that can own lore. */
export async function loreOwnerOptions(scope: EntityScope): Promise<LoreOwner[]> {
  const [chars, pcs, locs] = await Promise.all([
    api.listCharacters(scope),
    api.listPCs(scope),
    api.listEntities(scope, "locations"),
  ]);
  return [
    ...chars.map((c) => ({
      ref: `characters:${c.id}`, label: c.name, kind: "characters" as const,
      ...(c.has_avatar
        ? { avatar: api.actorImageUrl(scope, "characters", c.id, c.default_version, "avatar") }
        : {}),
    })),
    // A PC gets its portrait the same way now that PCs have images (#219) —
    // the owner chips beside a lore entry showed initials for every one of them.
    ...pcs.map((p) => ({
      ref: `pcs:${p.id}`, label: p.name, kind: "pcs" as const,
      ...(p.has_avatar
        ? { avatar: api.actorImageUrl(scope, "pcs", p.id, p.default_version, "avatar") }
        : {}),
    })),
    ...locs.map((l) => ({ ref: `locations:${l.id}`, label: l.name, kind: "locations" as const })),
  ];
}
