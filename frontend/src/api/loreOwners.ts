import { api, type EntityScope, type RefKind } from "./client";

/** A record another record can point at: a lore entry's owner, or the target of
 *  a `ref` entity field (#222). One shape for both because a ref is one thing —
 *  `<kind>:<id>` plus something to show the reader — and the picker that offers
 *  them is the same picker. */
export type RecordRef = { ref: string; label: string; kind: RefKind; avatar?: string };

/** The historical name, kept because `owners:` is what most of the app calls
 *  these. Identical type. */
export type LoreOwner = RecordRef;

/** A ref is `<kind>:<id>` and a field holds a comma-separated list of them, so
 *  an id containing a comma cannot be named by one — it would parse as two.
 *  Mirrors `entity_schema.referenceable`; `slugify` cannot produce such an id,
 *  but a hand-authored or imported file can, and offering it would put a
 *  candidate in the picker that the backend refuses on save with nothing
 *  anywhere saying why. */
const referenceable = (id: string) => !id.includes(",");

/** What a candidate fetch came back with. `failed` names the kinds whose
 *  listing did not load, and it is not a detail: a caller that cannot tell a
 *  failed load from an empty one renders every stored ref as deleted and
 *  invites the reader to clear relationships that are perfectly fine. */
export type RefOptions = { options: RecordRef[]; failed: RefKind[] };

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
): Promise<RefOptions> {
  // `allSettled`, not `all`: one kind's listing failing must not throw away the
  // kinds that answered. A `holder` offers four, and losing the other three to
  // one bad request is both a worse picker and — through the dangling chip — a
  // false claim about records that still exist.
  const settled = await Promise.all(
    kinds.map(async (kind) => {
      try {
        return { kind, options: await optionsForKind(scope, kind) };
      } catch {
        return { kind, options: null };
      }
    }),
  );
  return {
    options: settled.flatMap((r) => r.options ?? []),
    failed: settled.filter((r) => r.options === null).map((r) => r.kind),
  };
}

async function optionsForKind(scope: EntityScope, kind: RefKind): Promise<RecordRef[]> {
  if (kind === "characters") {
    const chars = await api.listCharacters(scope);
    return chars.filter((c) => referenceable(c.id)).map((c) => ({
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
    return pcs.filter((p) => referenceable(p.id)).map((p) => ({
      ref: `pcs:${p.id}`, label: p.name, kind,
      ...(p.has_avatar
        ? { avatar: api.actorImageUrl(scope, "pcs", p.id, p.default_version, "avatar") }
        : {}),
    }));
  }
  const entities = await api.listEntities(scope, kind);
  return entities.filter((e) => referenceable(e.id))
    .map((e) => ({ ref: `${kind}:${e.id}`, label: e.name, kind }));
}

/** All records in a container that can own lore.
 *
 *  Narrower than what `refOptions` can offer, and deliberately: owners gate by
 *  *scene presence*, and nothing makes an item, a group or a creature present
 *  (the entity-kinds design settled this). A ref field has no such constraint,
 *  which is why the two lists are not the same list. */
export function loreOwnerOptions(scope: EntityScope): Promise<RecordRef[]> {
  return refOptions(scope, ["characters", "pcs", "locations"]).then((r) => r.options);
}
