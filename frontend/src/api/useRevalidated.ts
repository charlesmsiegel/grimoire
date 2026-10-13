import { useCallback, useEffect, useRef, useState } from "react";
import { issuedEpoch, memoEpoch, onRunSettled } from "./memoEpoch";

/** Paint a page's list from memory, then from the server: first paint, then
 *  revalidate.
 *
 *  `recall` is the client's remembered answer for `key` (`api.rememberedWorlds`
 *  and its two siblings), and `read(fresh)` is the read that replaces it. The
 *  remembered answer is on screen for at most one revalidation, which is the
 *  safety argument of the remembered reads themselves (`client.ts`), held here
 *  on the screen as well as in the memo:
 *
 *  - **The state is keyed.** `data` belongs to `key`; on a key change the same
 *    render recalls for the new key, or yields `undefined`. It never shows the
 *    previous key's payload -- campaign A's chores under campaign B, with
 *    Ignore live on them -- which is the identity rule `useShellPayload.keyOf`
 *    keeps for the rail.
 *  - **Every mount and every key change issues the read.** Painting from
 *    memory never replaces asking.
 *  - **Only the newest read for the current key may write `data`.** Each read
 *    gets a sequence number. `reload()` supersedes whatever is in flight with
 *    a `fresh` read, so a slow mount-time read that lands after a rename's
 *    refresh cannot paint the list from before the rename over it.
 *  - **An answer sent before the current memo epoch is never painted.** One
 *    that was on the wire across a write, a run's end or a store change -- a
 *    page that joined a read some other caller sent before a write, say -- is
 *    discarded and a `fresh` read is issued in its place.
 *  - **A failure drops what was painted.** A read that could not confirm the
 *    rows is no reason to keep painting them; `failed` says so, and the page
 *    shows its failure state. (The rail keeps its last good payload instead,
 *    because navigation must survive; a page's list is not navigation.)
 *
 *  `stale` is true while `data` came from memory and no answer for this key
 *  has landed. A page marks its main region `aria-busy` then, and disables
 *  what acts on a record's identity -- delete, rename, fork, Ignore, Restore --
 *  because a frame painted from memory can name records that no longer exist,
 *  or that an id now names something else (a store moved from another origin,
 *  which no event of this one hears).
 *
 *  `reload()` answers whether the read it started (or the fresh one an old
 *  answer was swapped for) landed: `false` only when that read failed while it
 *  was still the newest. A read superseded by a later one answers `true`; the
 *  later one speaks for itself.
 */
export type Revalidated<T> = {
  data: T | undefined;
  stale: boolean;
  failed: boolean;
  reload: () => Promise<boolean>;
};

type Held<T> = { key: string; data: T | undefined; stale: boolean; failed: boolean };

function fromMemory<T>(key: string, recall: () => T | undefined): Held<T> {
  const data = recall();
  return { key, data, stale: data !== undefined, failed: false };
}

export function useRevalidated<T>(key: string,
                                  recall: () => T | undefined,
                                  read: (fresh: boolean) => Promise<T>): Revalidated<T> {
  const [held, setHeld] = useState<Held<T>>(() => fromMemory(key, recall));
  // Keyed: a held state for another key is replaced in this very render, before
  // anything of it can be drawn. React re-renders at once with the new state.
  let shown = held;
  if (held.key !== key) {
    shown = fromMemory(key, recall);
    setHeld(shown);
  }

  // The newest `read`, so a page may pass an inline closure without the read
  // being re-issued on every render; the effect below keys on `key` alone.
  const readRef = useRef(read);
  readRef.current = read;
  // The key the newest read was issued for. A read for any other is never
  // allowed to write.
  const keyRef = useRef(key);
  const seq = useRef(0);

  const issue = useCallback((k: string, fresh: boolean): Promise<boolean> => {
    const n = ++seq.current;
    let answer: Promise<T>;
    try {
      answer = readRef.current(fresh);
    } catch (err) {
      // A read that threw rather than rejected is a failed read all the same.
      answer = Promise.reject(err instanceof Error ? err : new Error(String(err)));
    }
    // Undefined for a promise the client did not issue -- a mocked read --
    // which is current by definition.
    const sentIn = issuedEpoch(answer);
    const newest = () => n === seq.current && keyRef.current === k;
    return answer.then((value) => {
      if (!newest()) return true;
      if (sentIn !== undefined && sentIn < memoEpoch()) return issue(k, true);
      setHeld({ key: k, data: value, stale: false, failed: false });
      return true;
    }, () => {
      if (!newest()) return true;
      setHeld({ key: k, data: undefined, stale: false, failed: true });
      return false;
    });
  }, []);

  useEffect(() => {
    keyRef.current = key;
    void issue(key, false);
    // Whatever is still in flight for this key may no longer write: the key is
    // moving, or the page is going.
    return () => { seq.current += 1; };
  }, [key, issue]);

  const reload = useCallback(() => {
    // The failure banner goes while the new read is asked; what follows is the
    // answer's to say.
    setHeld((h) => (h.failed ? { ...h, failed: false } : h));
    return issue(keyRef.current, true);
  }, [issue]);

  // A detached run this client saw end wrote after whatever is on screen was
  // read: ask again, so the page does not go on showing what came before it
  // (spec 04 section 6.5). A write this tab sends is the page's own to
  // follow with `reload()`.
  useEffect(() => onRunSettled(() => { void reload(); }), [reload]);

  return { data: shown.data, stale: shown.stale, failed: shown.failed, reload };
}
