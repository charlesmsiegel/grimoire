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
      ...(c.has_avatar ? { avatar: api.actorImageUrl(scope, c.id, c.default_version, "avatar") } : {}),
    })),
    ...pcs.map((p) => ({ ref: `pcs:${p.id}`, label: p.name, kind: "pcs" as const })),
    ...locs.map((l) => ({ ref: `locations:${l.id}`, label: l.name, kind: "locations" as const })),
  ];
}
