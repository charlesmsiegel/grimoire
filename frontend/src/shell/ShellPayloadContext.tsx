import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import type { ShellPayload } from "../api/types";
import type { ShellState } from "./useShellPayload";

/** The rail's `GET /api/shell`, handed down to the pages that draw the same
 *  numbers.
 *
 *  The hub and the scenes list used to issue a read of their own on every
 *  arrival, beside the rail's, and hold the whole page until it answered. The
 *  read is library-scaled -- it walks the campaign's open scenes, its review
 *  sidecars and the usage rollup -- and two identical ones in flight convoy on
 *  the server rather than running side by side, so a list of scene titles that
 *  had arrived in tens of milliseconds sat behind the slowest request in the
 *  chrome, twice over. Now there is one read, the rail's, and a page draws
 *  whatever of it is about its own campaign as it arrives.
 *
 *  `cid` is the campaign the read ASKED about, which is not always the one the
 *  payload names: asked about nothing, the server answers with the campaign
 *  played last. `status` is about that ask, and `useCampaignShell` is what
 *  turns the pair into something a page can trust. */
export type ShellContextValue = ShellState & { cid: string | null };

/** Outside a provider: nothing known, and nothing to ask. Every consumer
 *  renders its shell-derived figures as pending under this, never as zero, so
 *  a page mounted without the chrome degrades to "not yet" rather than lying. */
const NONE: ShellContextValue = { status: "loading", payload: null, retry: () => {}, cid: null };

const ShellPayloadContext = createContext<ShellContextValue>(NONE);

export function ShellPayloadProvider({ value, children }: {
  value: ShellContextValue; children: ReactNode;
}) {
  return <ShellPayloadContext.Provider value={value}>{children}</ShellPayloadContext.Provider>;
}

/** The shell read, as a page about campaign `cid` may use it.
 *
 *  - `payload` is the payload only when it is about `cid`, and `null` otherwise
 *    -- pending, failed, or about a different campaign entirely (the one the
 *    rail remembered before the route's own id reached it, or the one the
 *    server resolved an unknown id to). A page never draws another campaign's
 *    counts, and the caller renders `null` as "not yet", never as zero: the
 *    cost rule, since the money columns ride this payload.
 *  - `failed` is true only when the latest read asked about `cid` and failed.
 *    A payload can be present beside it -- the last good answer for this
 *    campaign, stale but usable, which is the bargain the rail makes too.
 *
 *  **Asks for a read on arrival.** The rail re-reads when its campaign
 *  changes, not on every navigation, and nothing notifies it when a turn lands
 *  -- so without this a page reached from the play view would draw the money
 *  and turn counts the rail heard before the reader went off to play. It costs
 *  no extra request when the rail is reading anyway (see `demand` in
 *  `useShellPayload`), and the page draws whatever is held in the meantime
 *  rather than waiting.
 *
 *  Once per arrival: the ref survives StrictMode's rehearsed unmount, which is
 *  not a second arrival and must not demand a second read. A real remount is a
 *  new component, with a new ref, and asks again. */
export function useCampaignShell(cid: string): {
  payload: ShellPayload | null; failed: boolean; retry: () => void;
} {
  const { payload, status, retry, cid: asked } = useContext(ShellPayloadContext);
  const arrived = useRef<string | null>(null);
  useEffect(() => {
    if (!cid || arrived.current === cid) return;
    arrived.current = cid;
    retry();
  }, [cid, retry]);
  return {
    payload: payload?.campaign?.id === cid ? payload : null,
    failed: asked === cid && status === "failed",
    retry,
  };
}
