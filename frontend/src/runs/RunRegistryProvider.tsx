/** What survives a component unmounting: which sends are still unaccounted
 *  for, what the player typed into them, and how far each run has been read.
 *
 *  All three used to live in `CampaignView`, which is mounted under the router
 *  -- so navigating away, or a phone suspending the tab, destroyed them. That
 *  is fine while a turn dies with its socket, because there is then nothing to
 *  come back to. Now the turn outlives the socket, and the only record of "I
 *  sent something and never heard back" went with the component.
 *
 *  Mounted ABOVE `BrowserRouter` (see `main.tsx`). A provider under the router
 *  remounts on navigation, which is the thing this exists to prevent.
 *
 *  Deliberately refs rather than state. Nothing here renders: the consumer asks
 *  at mount and on `visibilitychange`, and making it state would re-render the
 *  whole app on every buffered frame for no visible gain.
 */
import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";

/** A send whose outcome this client has not yet established.
 *
 *  `text` is kept because the id alone cannot honour `post_returned`: if the
 *  response dies before the run frame and the turn then fails, the backend
 *  takes the player's post back off the transcript and the words exist
 *  nowhere -- not in the scene, not in the unmounted component. Held here,
 *  they go back in the composer.
 */
export type Attempt = {
  cid: string;
  sid: string;
  attempt: string;
  text: string;
  runId: string | null;
};

export type RunRegistry = {
  /** Record a send BEFORE it is issued.
   *
   *  Before, not when the leading `run` frame arrives, and that ordering is
   *  the whole mechanism: the window this covers is exactly the one where the
   *  server accepted the request and the response was lost before any frame.
   *  Registering on the frame would leave that case with nothing recorded.
   */
  begin(a: Attempt): void;
  /** Note the run id once the leading frame names it -- a shortcut for
   *  recovery, which can otherwise find it by attempt. */
  attach(cid: string, sid: string, runId: string): void;
  /** The unresolved send for this scene, if there is one. */
  pending(cid: string, sid: string): Attempt | undefined;
  /** Forget a send whose outcome is established. */
  settle(cid: string, sid: string): void;
  /** Follow a scene that has been renamed.
   *
   *  A `sid` carries the slug, so a rename mints a new one -- and an entry
   *  left under the old key is unreachable: opening the renamed scene looks
   *  under the new id, finds nothing, and a failed run whose post was rolled
   *  back leaves the player's words stranded under an id no route will ever
   *  select again. (The backend has the same problem and solved it by keying
   *  runs on the scene's stable identity; the identity is deliberately kept
   *  out of `read_scene`'s payload, so the client cannot key on it and follows
   *  the rename instead.)
   *
   *  This covers a rename made HERE, which is the case the freeze guard leaves
   *  reachable -- a scene can only be renamed once its run is terminal. A
   *  rename from another device still strands the entry; the words are then
   *  recoverable only from that other client. */
  rekey(cid: string, from: string, to: string): void;
  /** Record how far a run has been read, by WIRE index. */
  consume(runId: string, index: number): void;
  /** Where to resume this run: one past the last frame actually read. */
  resumeFrom(runId: string): number;
};

// JSON, not concatenation. `store.safe_id` permits interior spaces, so
// `("a", "b c")` and `("a b", "c")` both flatten to `"a b c"` under a
// separator-joined key -- and because this provider survives navigation, both
// pairs can hold a pending send at once. Beginning a turn for one would then
// overwrite the other's saved prompt, and recovery would resolve into the
// wrong scene. `JSON.stringify` escapes the parts, so no pair of valid ids can
// collide whatever characters they contain.
const key = (cid: string, sid: string) => JSON.stringify([cid, sid]);

const Ctx = createContext<RunRegistry | null>(null);

/** Where the pending map is mirrored so it can outlive the whole renderer.
 *
 *  React state does not survive a reload, and on Android the WebView's renderer
 *  can be restarted out from under a perfectly healthy backend turn -- which is
 *  the exact scenario this feature exists for. The provider then comes back
 *  empty, reattaches to the live run, and holds no copy of what the player
 *  typed; if that run fails and rolls its post back, the words are in neither
 *  the transcript nor here.
 *
 *  `sessionStorage` is deliberately not used: it is per-tab and a renderer
 *  restart is not a new tab, but a genuine reload of a killed process is close
 *  enough to one that the distinction is not worth betting the player's text
 *  on. `localStorage` survives both.
 */
const STORE_KEY = "grimoire.runs.pending";

/** Every storage touch is wrapped. Access itself throws in a private window or
 *  with site data blocked, and a registry that could not save is still a
 *  registry -- degrading to in-memory is exactly what it did before. */
function isEntry(v: unknown): v is [string, Attempt] {
  if (!Array.isArray(v) || v.length !== 2 || typeof v[0] !== "string") return false;
  const a = v[1] as Partial<Attempt> | null;
  return !!a && typeof a === "object"
    && typeof a.cid === "string" && typeof a.sid === "string"
    && typeof a.attempt === "string" && typeof a.text === "string";
}

function readStored(): [string, Attempt][] {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    // `unknown`, and every entry shape-checked. This is data from a PREVIOUS
    // build of the app -- an older schema, a half-written value, or anything
    // else on the origin -- and trusting it would put a malformed record where
    // recovery reads the player's text from.
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isEntry) : [];
  } catch {
    return [];
  }
}

/** How many unresolved sends are kept on disk, oldest dropped first.
 *
 *  Bounded because nothing guarantees an entry is ever settled -- the app can
 *  be closed mid-turn -- and an unbounded list would grow for the life of the
 *  install. Generous next to the handful that can plausibly be outstanding: a
 *  scene holds at most one, so this is 50 scenes left mid-send.
 *
 *  Age is deliberately NOT the bound. An old entry is not restored blindly:
 *  recovery asks the server whether that attempt's post is still in the
 *  transcript, and settles without restoring when it is. Expiring by time would
 *  throw away words in exactly the case they are least recoverable elsewhere.
 */
const KEEP = 50;

function writeStored(map: Map<string, Attempt>): void {
  try {
    // `Map` preserves insertion order, so the tail is the most recent.
    const entries = [...map].slice(-KEEP);
    window.localStorage.setItem(STORE_KEY, JSON.stringify(entries));
  } catch {
    /* full, blocked, or unavailable: the in-memory map is still authoritative */
  }
}

export function RunRegistryProvider({ children }: { children: ReactNode }) {
  // Rehydrated ONCE, at construction. An effect would run after the first
  // render -- and `CampaignView`'s mount-time adoption pass reads this
  // synchronously in that same render, so a reload would find it empty in
  // precisely the case it was persisted for.
  const pending = useRef<Map<string, Attempt>>(new Map(readStored()));
  const consumed = useRef(new Map<string, number>());

  const value = useMemo<RunRegistry>(() => ({
    begin(a) {
      pending.current.set(key(a.cid, a.sid), a);
      writeStored(pending.current);
    },
    attach(cid, sid, runId) {
      const found = pending.current.get(key(cid, sid));
      if (found) { found.runId = runId; writeStored(pending.current); }
    },
    pending(cid, sid) { return pending.current.get(key(cid, sid)); },
    settle(cid, sid) {
      pending.current.delete(key(cid, sid));
      writeStored(pending.current);
    },
    rekey(cid, from, to) {
      if (from === to) return;
      const found = pending.current.get(key(cid, from));
      if (!found) return;
      pending.current.delete(key(cid, from));
      pending.current.set(key(cid, to), { ...found, sid: to });
      writeStored(pending.current);
    },
    consume(runId, index) {
      const seen = consumed.current.get(runId);
      // Monotonic: a replay that re-delivers an earlier frame must not move
      // the cursor backwards and make the next resume repeat text again.
      if (seen === undefined || index > seen) consumed.current.set(runId, index);
    },
    resumeFrom(runId) {
      const seen = consumed.current.get(runId);
      // `consumed + 1`, never the run's `next_index`. That field is the live
      // tail, so resuming from it drops everything generated while this client
      // was away -- which is the entire reply, in the case this exists for.
      return seen === undefined ? 0 : seen + 1;
    },
  }), []);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The registry, or a no-op stand-in when no provider is mounted.
 *
 *  Returning a stand-in rather than throwing is deliberate: several suites
 *  render `CampaignView` on its own, and a hard requirement here would turn
 *  every one of them into a provider test. The cost is that recovery does
 *  nothing in those, which is what they expect anyway -- and `main.tsx` is
 *  where the real mounting is held to account.
 */
export function useRunRegistry(): RunRegistry {
  const found = useContext(Ctx);
  return useMemo<RunRegistry>(() => found ?? {
    begin() {}, attach() {}, pending() { return undefined; },
    settle() {}, rekey() {}, consume() {}, resumeFrom() { return 0; },
  }, [found]);
}
