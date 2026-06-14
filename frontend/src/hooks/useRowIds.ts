import { useRef } from "react";

/**
 * Stable, non-index keys for a controlled list whose items carry no id of
 * their own (slot values, power rows, calendar months, …).
 *
 * The ids travel with the *logical* row across insert/delete so a React `key`
 * stays attached to the same row — input focus and value never bleed into a
 * neighbour when an earlier row is removed or a new one is inserted (#547).
 * They are generated client-side and never persisted: they exist only for the
 * lifetime of the mounted list.
 *
 * Callers must keep ids in lockstep with their data:
 * - call `removeAt(i)` / `insertAt(i)` alongside the matching data mutation,
 * - leave ids untouched for in-place edits (length is unchanged).
 *
 * When the controlled value is replaced wholesale (e.g. a fresh sheet loads),
 * `count` no longer matches and the list is re-keyed from the end — exact
 * identity preservation isn't expected across a full replacement.
 */
export function useRowIds(count: number) {
  const ids = useRef<string[]>([]);

  if (ids.current.length !== count) {
    if (count > ids.current.length) {
      while (ids.current.length < count) ids.current.push(crypto.randomUUID());
    } else {
      ids.current = ids.current.slice(0, count);
    }
  }

  return {
    keys: ids.current as ReadonlyArray<string>,
    insertAt(idx: number) {
      ids.current = [...ids.current.slice(0, idx), crypto.randomUUID(), ...ids.current.slice(idx)];
    },
    removeAt(idx: number) {
      ids.current = ids.current.filter((_, i) => i !== idx);
    },
  };
}
