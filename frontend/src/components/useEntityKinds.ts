import { useEffect, useState } from "react";
import { api } from "../api/client";
// From `types` and not through `client`: the suites that render an import
// dialog mock `../api/client` wholesale, so a fallback list reached through
// that mock would be whatever the mock declared rather than what ships.
import { ENTITY_KINDS } from "../api/types";

/** The categories an import review row may be reclassified to (#138).
 *
 *  Server-first, built-in as the floor. `GET /api/entity-kinds` is
 *  `store.entities.ENTITY_KINDS` itself, and `lorebook.commit` /
 *  `scenario.apply` validate against that same tuple — so every option this
 *  offers is one they accept, and a kind added to it reaches the dropdown with
 *  no frontend edit. The list starts on this build's own kinds so the table is
 *  usable on the first frame, and a failed read keeps them: an auxiliary GET
 *  must not take the Category column down beside an import the user has
 *  already parsed and is about to commit. `string[]` rather than
 *  `EntityKind[]`, because the server is allowed to know a kind this build does
 *  not — which is the one case the endpoint exists for.
 *
 *  `enabled` means "there is a review table on screen". Both dialogs sit inside
 *  a collapsed `<details>` that React mounts with the page, so an
 *  unconditional read would fire on every world Overview and Lore section for
 *  an importer nobody opened. Nothing is cached across mounts either: the
 *  answer is a handful of bytes, it is asked for only while a table is up, and
 *  a module-level promise would outlive the tests that set what it resolves to.
 *
 *  A build whose own list has fallen behind the server's is a separate failure
 *  with its own guard (`test_entities_store.py::
 *  test_the_frontend_ships_the_same_kind_list`): the dropdown would be right to
 *  offer that kind, and this build would still have no tab or label to show the
 *  record it created.
 */
export function useEntityKinds(enabled: boolean): string[] {
  const [kinds, setKinds] = useState<string[]>(() => [...ENTITY_KINDS]);

  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    // `void`: an effect cannot await, and there is nothing to await for — the
    // catch is the whole error path.
    void (async () => {
      try {
        const fresh = (await api.entityKinds()).kinds;
        // A malformed or empty answer is treated as no answer: an empty
        // dropdown makes every row uncommittable, which is strictly worse than
        // a list that is merely out of date.
        if (live && Array.isArray(fresh) && fresh.length > 0) setKinds(fresh);
      } catch {
        // Keep the built-ins. See above.
      }
    })();
    return () => { live = false; };
  }, [enabled]);

  return kinds;
}

/** The options one review row may show: the server's kinds, plus the row's own
 *  category when that list does not contain it.
 *
 *  A `<select>` whose `value` matches no `<option>` renders as its FIRST option
 *  — so a row filed under a kind this list is missing would *display* as
 *  `locations` and import as whatever it actually holds, with nothing on screen
 *  saying so. Reachable in one real case: the read failed, the fallback is this
 *  build's own list, and the entry came back under a kind added after this
 *  build shipped. Showing the row's own category is both the honest render and
 *  the only one that lets the user keep it.
 */
export function kindOptions(kinds: string[], current: string): string[] {
  return kinds.includes(current) ? kinds : [...kinds, current];
}
