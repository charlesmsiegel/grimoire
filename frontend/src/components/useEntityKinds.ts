import { useEffect, useState } from "react";
import { api } from "../api/client";
// From `types` and not from `client`: the suites that render an import dialog
// mock `../api/client` wholesale, and a fallback list reached through that mock
// would be whatever the mock happened to declare rather than what this build
// actually ships.
import { ENTITY_KINDS } from "../api/types";

/** The categories an import review row may be reclassified to (#138).
 *
 *  Server-first, built-in as the floor. The dropdown starts on this build's own
 *  `ENTITY_KINDS` so the table is usable on the first frame, and swaps to
 *  `GET /api/entity-kinds` when it answers — which is the whole point: a kind
 *  added to `store.entities.ENTITY_KINDS` shows up here with no frontend edit,
 *  and `lorebook.commit` / `scenario.apply` validate against that same tuple,
 *  so every option offered is one they accept.
 *
 *  A failed read keeps the built-ins rather than emptying the dropdown. This is
 *  an auxiliary GET beside an import the user has already parsed and is about
 *  to commit; letting it take the Category column down would cost them that
 *  work to tell them something they did not ask. `string[]`, not
 *  `EntityKind[]`, deliberately — the server is allowed to know a kind this
 *  build does not, and typing the answer as the local union would erase the one
 *  case the endpoint exists for.
 *
 *  Not cached across mounts: the answer is a handful of bytes, the two dialogs
 *  that ask are on one page (so `client.ts` already shares the overlapping
 *  GET), and a module-level promise would outlive the tests that set what it
 *  resolves to. A build whose own list has fallen behind the server's is a
 *  separate failure and has its own guard — `test_entities_store.py::
 *  test_the_frontend_ships_the_same_kind_list` — because a kind this build
 *  cannot label or give a tab to is one it cannot show a user after import,
 *  even though the dropdown was right to offer it.
 */
export function useEntityKinds(): string[] {
  const [kinds, setKinds] = useState<string[]>(() => [...ENTITY_KINDS]);

  useEffect(() => {
    let live = true;
    // `void`: an effect cannot await, and there is nothing to await for — the
    // catch below is the whole error path.
    void (async () => {
      try {
        const { kinds } = await api.entityKinds();
        // A malformed or empty answer is treated as no answer: an empty
        // dropdown makes every row uncommittable, which is strictly worse than
        // a list that is merely out of date.
        if (live && Array.isArray(kinds) && kinds.length) setKinds(kinds);
      } catch {
        // Keep the built-ins. See above.
      }
    })();
    return () => { live = false; };
  }, []);

  return kinds;
}

/** The options one review row may show: the server's kinds, plus the row's own
 *  category when that list does not contain it.
 *
 *  A `<select>` whose `value` matches no `<option>` renders as its FIRST option
 *  — so a row parsed as a kind this list is missing would *display* as
 *  `locations` and commit as whatever it actually holds, with nothing on screen
 *  saying so. Reachable in one real case: the kinds read failed, the fallback is
 *  this build's own list, and the entry came back under a kind added after this
 *  build shipped. Showing the row's own category is both the honest render and
 *  the only one that lets the user keep it.
 */
export function kindOptions(kinds: string[], current: string): string[] {
  return kinds.includes(current) ? kinds : [...kinds, current];
}
