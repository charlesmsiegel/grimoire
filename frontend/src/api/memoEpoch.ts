/** The remembered reads' epoch, and the epoch each read was issued in.
 *
 *  A leaf with no imports of its own, for the reason `./errors` is one: the
 *  pages that paint from memory are tested against a mock that replaces
 *  `./client` wholesale, and `useRevalidated` has to ask these two questions
 *  whatever that mock carries. Under such a mock nothing ever bumps the epoch
 *  and no promise has an issued epoch, so every answer counts as current --
 *  which is what a mocked read is.
 *
 *  The memo, its root and its forgetting stay in `client.ts`; only the counter
 *  lives here. `client.ts` is the one writer (`bumpMemoEpoch` from
 *  `forgetRemembered`, `noteIssued` from `request` and `remembering`). */

let epoch = 0;
/** The epoch each GET was sent in, keyed by the promise its callers hold --
 *  the shared one `request` hands every caller who joins it, and the one
 *  `remembering` hands back over it. */
const issuedIn = new WeakMap<Promise<unknown>, number>();

/** The epoch now. Moves on every forget: a write, a run seen to end, a store
 *  root that changed. */
export function memoEpoch(): number {
  return epoch;
}

export function bumpMemoEpoch(): void {
  epoch += 1;
}

/** Who is told when this client sees a detached run end. A run writes after
 *  the request that started it settled, so a page already on screen may be
 *  holding an answer from before what the run wrote: `useRevalidated`
 *  listens here and asks again (spec 04 section 6.5). Here rather than in
 *  `./client` for the epoch's reason: a page under a wholesale mock of the
 *  client still hears it. */
const runSettledListeners = new Set<() => void>();

export function onRunSettled(listener: () => void): () => void {
  runSettledListeners.add(listener);
  return () => { runSettledListeners.delete(listener); };
}

/** Tell every listener a run this client observed has ended. `client.ts`'s
 *  `noteRunSettled` is the one caller, after it has forgotten and retired. */
export function announceRunSettled(): void {
  for (const listener of [...runSettledListeners]) listener();
}

export function noteIssued(read: Promise<unknown>, at: number = epoch): void {
  issuedIn.set(read, at);
}

/** The epoch `read` was SENT in, or `undefined` for a promise the client did
 *  not issue. An answer whose epoch is older than `memoEpoch()` when it lands
 *  was on the wire across a forget, and describes a store that may since have
 *  changed under it. */
export function issuedEpoch(read: Promise<unknown>): number | undefined {
  return issuedIn.get(read);
}
