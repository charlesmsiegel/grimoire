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
  /** Record how far a run has been read, by WIRE index. */
  consume(runId: string, index: number): void;
  /** Where to resume this run: one past the last frame actually read. */
  resumeFrom(runId: string): number;
};

const key = (cid: string, sid: string) => `${cid} ${sid}`;

const Ctx = createContext<RunRegistry | null>(null);

export function RunRegistryProvider({ children }: { children: ReactNode }) {
  const pending = useRef(new Map<string, Attempt>());
  const consumed = useRef(new Map<string, number>());

  const value = useMemo<RunRegistry>(() => ({
    begin(a) { pending.current.set(key(a.cid, a.sid), a); },
    attach(cid, sid, runId) {
      const found = pending.current.get(key(cid, sid));
      if (found) found.runId = runId;
    },
    pending(cid, sid) { return pending.current.get(key(cid, sid)); },
    settle(cid, sid) { pending.current.delete(key(cid, sid)); },
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
    settle() {}, consume() {}, resumeFrom() { return 0; },
  }, [found]);
}
