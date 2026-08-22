import { useEffect, useState } from "react";
import { api } from "../api/client";
// From `types` and not through `client`: the suites that render an import
// dialog mock `../api/client` wholesale, so a fallback list reached through
// that mock would be whatever the mock declared rather than what ships.
import { ENTITY_KINDS, type EntityKind } from "../api/types";

/** The categories an import review row may be reclassified to (#138).
 *
 *  The INTERSECTION of what the server files entities under and what this
 *  build can show — never a copy of either, and never a union. `GET
 *  /api/entity-kinds` is `store.entities.ENTITY_KINDS` itself, so the server
 *  half means the dropdown cannot offer a category `lorebook.commit` or
 *  `scenario.apply` would refuse; `ENTITY_KINDS` is this bundle's own, so the
 *  build half means it cannot offer one with no tab, no label and no per-kind
 *  fields to reach the record afterwards. Both halves only bite when a bundle
 *  and the backend serving it disagree — in-tree they are held equal by
 *  `test_entities_store.py::test_the_frontend_ships_the_same_kind_list`, so a
 *  kind added to the tuple still reaches the dropdown with neither dialog
 *  edited, which is what this exists for.
 *
 *  Deliberately not a union, though the endpoint could support one: a kind
 *  this build has never heard of can be *committed* correctly and then has
 *  nowhere to be viewed, edited or deleted from, which is a silently lost
 *  record rather than a feature (Codex P2 on #418). A row that ARRIVES under
 *  such a kind is the other case and is kept — see `kindOptions`.
 *
 *  `enabled` means "there are rows on screen to file". Both dialogs sit inside
 *  a collapsed `<details>` that React mounts with the page, so an
 *  unconditional read would fire on every world Overview and Lore section for
 *  an importer nobody opened — and a parse that yields no entries has no
 *  Category column either, so it does not ask. Nothing is cached across mounts
 *  either: the answer is a handful of bytes, it is asked for only while a table
 *  is up, and a module-level promise would outlive the tests that set what it
 *  resolves to.
 *
 *  A failed read keeps the built-ins rather than emptying the dropdown: an
 *  auxiliary GET must not take the Category column down beside an import the
 *  user has already parsed and is about to commit. An intersection that comes
 *  out empty is treated the same way — two lists with nothing in common is a
 *  disagreement this cannot adjudicate, and an empty dropdown makes every row
 *  uncommittable, which is strictly worse than a list that is merely stale.
 */
export function useEntityKinds(enabled: boolean): EntityKind[] {
  const [kinds, setKinds] = useState<EntityKind[]>(() => [...ENTITY_KINDS]);

  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    // `void`: an effect cannot await, and there is nothing to await for — the
    // catch is the whole error path.
    void (async () => {
      try {
        const fresh = (await api.entityKinds()).kinds;
        if (!live || !Array.isArray(fresh)) return;
        const shared = ENTITY_KINDS.filter((k) => fresh.includes(k));
        if (shared.length > 0) setKinds(shared);
      } catch {
        // Keep the built-ins. See above.
      }
    })();
    return () => { live = false; };
  }, [enabled]);

  return kinds;
}

/** The options one review row may show: the kinds above, plus the row's own
 *  category when they do not contain it.
 *
 *  A `<select>` whose `value` matches no `<option>` renders as its FIRST option
 *  — so a row filed under a kind the list is missing would *display* as
 *  `locations` and import as whatever it actually holds, with nothing on screen
 *  saying so. That row exists whenever a bundle meets a backend that knows a
 *  kind it does not: the server parsed the file and filed the entry under a
 *  category of its own. Keeping it is both the honest render and the only
 *  thing that lets the user keep an entry the server made — which is a
 *  different act from letting them newly assign a kind this build cannot show,
 *  and that one `useEntityKinds` refuses.
 */
export function kindOptions(kinds: readonly string[], current: string): string[] {
  return kinds.includes(current) ? [...kinds] : [...kinds, current];
}
