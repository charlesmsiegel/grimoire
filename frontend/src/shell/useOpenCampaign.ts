import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { railless } from "./rail";

/** Which campaign the rail's second tier is about, across the whole session.
 *
 *  State the app has never had: until the rail, "which campaign" was purely
 *  `/campaigns/:cid` in the URL. The rail shows a campaign's rows while you are
 *  standing on Configuration, so something has to remember it.
 *
 *  **It is a hint, not a fact.** What the rail *renders* comes from
 *  `payload.campaign`; this only decides which id the next `GET /api/shell`
 *  asks about. The rail never draws a campaign name out of storage — a name
 *  cached here would outlive a rename, and there is nothing to notice that.
 *
 *  Keyed by the store root, so a different library pointed at from
 *  Configuration gets its own answer rather than inheriting this one's. (That
 *  idea is borrowed from `App.tsx`'s setup latch, which compares against
 *  `dataDir` — but note it keeps that latch in memory, not in storage.)
 *
 *  Cross-tab: last writer wins, and no `storage` listener. Two tabs in one
 *  library can hold different campaigns and a reload takes whichever wrote
 *  last. A rail heading changing under a reader because another tab moved is
 *  worse than a stale one, so this is a decision rather than an oversight.
 */
const PREFIX = "grimoire.openCampaign:";

function key(dataDir: string): string {
  return PREFIX + dataDir;
}

function load(dataDir: string): string | null {
  if (!dataDir) return null;
  // Storage throws rather than returning null in a locked-down WebView, the
  // same bargain `focus.tsx` makes: a rail heading is not worth a blank screen.
  try { return localStorage.getItem(key(dataDir)); } catch { return null; }
}

function save(dataDir: string, cid: string | null): void {
  if (!dataDir) return;
  try {
    if (cid) localStorage.setItem(key(dataDir), cid);
    else localStorage.removeItem(key(dataDir));
  } catch { /* see load() */ }
}

/** The campaign id in `pathname`, or null.
 *
 *  `/campaigns/new` is excluded through `railless`, and that exclusion is
 *  load-bearing rather than tidy: as a route pattern the wizard matches
 *  `/campaigns/:cid`, so starting a campaign and abandoning it would replace
 *  the remembered campaign with the literal `"new"` — which the next successful
 *  shell read would then clear as unknown, losing the real one. */
export function cidIn(pathname: string): string | null {
  if (railless(pathname)) return null;
  const m = /^\/campaigns\/([^/]+)/.exec(pathname);
  return m ? m[1] : null;
}

export function useOpenCampaign(dataDir: string): {
  cid: string | null;
  /** Called with a *successful* read's answer. `null` clears the memory; a
   *  failed or pending read must never call this. */
  reconcile: (resolved: string | null) => void;
} {
  const { pathname } = useLocation();
  const [cid, setCid] = useState<string | null>(() => load(dataDir));

  // The store root can arrive after the first render (App reads config
  // asynchronously) and can change mid-session from Configuration. Either way
  // the answer for the new root is that root's own, never the previous one's.
  useEffect(() => { setCid(load(dataDir)); }, [dataDir]);

  useEffect(() => {
    const here = cidIn(pathname);
    if (!here) return;          // leaving a campaign does not close it
    setCid(here);
    save(dataDir, here);
  }, [pathname, dataDir]);

  const reconcile = useCallback((resolved: string | null) => {
    // Only a successful read saying "this id resolves to nothing" clears the
    // memory. A dropped connection must not: confusing a failed request for a
    // deleted campaign is how valid state gets erased, and the rail would then
    // lose its second tier every time the server hiccuped.
    if (resolved !== null) return;
    setCid(null);
    save(dataDir, null);
  }, [dataDir]);

  return { cid, reconcile };
}
