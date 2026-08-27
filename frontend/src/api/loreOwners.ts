import { api, type EntityScope, type RefKind } from "./client";

/** A record another record can point at: a lore entry's owner, or the target of
 *  a `ref` entity field (#222). One shape for both because a ref is one thing —
 *  `<kind>:<id>` plus something to show the reader — and the picker that offers
 *  them is the same picker. */
export type RecordRef = { ref: string; label: string; kind: RefKind; avatar?: string };

/** The historical name, kept because `owners:` is what most of the app calls
 *  these. Identical type. */
export type LoreOwner = RecordRef;

/** Every record of `kinds` in a container (world or campaign copy), as pickable
 *  refs.
 *
 *  Kind order is the caller's, not the store's: a field declares the kinds it
 *  accepts in the order it wants them offered, and a picker that re-sorted them
 *  would put the answer the field is usually about somewhere else per field.
 *
 *  One request per kind, in parallel. Actors and entities come from different
 *  endpoints and that is the only reason this is a switch rather than a map. */
export async function refOptions(
  scope: EntityScope, kinds: readonly RefKind[],
): Promise<RecordRef[]> {
  const lists = await Promise.all(kinds.map((kind) => optionsForKind(scope, kind)));
  return lists.flat();
}

async function optionsForKind(scope: EntityScope, kind: RefKind): Promise<RecordRef[]> {
  if (kind === "characters") {
    const chars = await api.listCharacters(scope);
    return chars.map((c) => ({
      ref: `characters:${c.id}`, label: c.name, kind,
      ...(c.has_avatar
        ? { avatar: api.actorImageUrl(scope, "characters", c.id, c.default_version, "avatar") }
        : {}),
    }));
  }
  if (kind === "pcs") {
    const pcs = await api.listPCs(scope);
    // A PC gets its portrait the same way now that PCs have images (#219) —
    // the owner chips beside a lore entry showed initials for every one of them.
    return pcs.map((p) => ({
      ref: `pcs:${p.id}`, label: p.name, kind,
      ...(p.has_avatar
        ? { avatar: api.actorImageUrl(scope, "pcs", p.id, p.default_version, "avatar") }
        : {}),
    }));
  }
  const entities = await api.listEntities(scope, kind);
  return entities.map((e) => ({ ref: `${kind}:${e.id}`, label: e.name, kind }));
}

/** All records in a container that can own lore.
 *
 *  Narrower than what `refOptions` can offer, and deliberately: owners gate by
 *  *scene presence*, and nothing makes an item, a group or a creature present
 *  (the entity-kinds design settled this). A ref field has no such constraint,
 *  which is why the two lists are not the same list. */
export function loreOwnerOptions(scope: EntityScope): Promise<RecordRef[]> {
  return refOptions(scope, ["characters", "pcs", "locations"]);
}
