/** What this client holds between requests, and when it lets go of it: the
 *  in-flight GETs identical reads share, the remembered reads a page paints
 *  from on its first render, and the cached config -- plus the forgetting a
 *  write, a run seen to end and a store move each trigger.
 *
 *  Split out of `client.ts`. A leaf below `./transport`: the request path
 *  calls in here, and nothing here makes a request. */
import { announceRunSettled, bumpMemoEpoch, issuedEpoch, memoEpoch, noteIssued } from "./memoEpoch";
import { newAttemptId } from "./stream";
import type { Config } from "./types";
import { campaignsChanged, configChanged, onRunEnded, storeMovedElsewhere } from "../appEvents";

// Identical GETs that overlap share one request: opening a scene fires the
// same cast/appearances/datetime lookups from several components at once.
// The map only holds in-flight promises, so nothing is ever served stale.
export const inflightGets = new Map<string, Promise<unknown>>();

// `fresh` opts out of the sharing above, for the rare caller whose whole reason
// for asking is that it needs a read issued *now*. Sharing is normally free
// because the answer cannot be stale — the map only holds in-flight promises —
// but that reasoning assumes the caller does not care when the read happened.
// A read verifying that a write has landed does care: handed a promise started
// before that write, it gets a pre-write answer and concludes the write never
// happened (#95, `CampaignView.settleProposal`).
//
// It is never *served* the shared promise, and it also retires the entry: a
// caller asking for a fresh read is telling us the thing behind this path just
// changed, so any read still in flight predates that change and must not be
// handed to whoever asks next. Dropping the map entry cannot disturb the
// callers already awaiting that promise — they hold it directly, and it still
// resolves for them — it only stops new ones joining. Without this the stale
// read outlives the refresh that was supposed to replace it, and the next
// caller along adopts it.
export function retireInflight(path: string): void {
  inflightGets.delete(path);
}

/** Retire *every* pending GET. A store move repoints the root, so each
 *  in-flight read is describing a library that is no longer the one being
 *  shown -- the Library hub's five section counts as much as the campaign
 *  list. A write and a run seen to end do it too (`forgetAndRetire`): either
 *  can change what any store-scoped read answers, and a read started after one
 *  must not join a read sent before it. Retiring by path would mean listing
 *  every store-scoped endpoint here and keeping that list current, which is
 *  the enumeration this file has already been bitten by. */
export function retireAllInflight(): void {
  inflightGets.clear();
}

// ---- remembered reads ------------------------------------------------------
//
// The last good answer to the few reads a page paints from -- the world record
// (`getWorld`), a scope's character rows (`listCharacters`), a campaign's
// roster (`listAppearances`), and the three overview pages' lists
// (`listWorlds`, `listCampaigns`, `getTodo`) -- so that a page seen once paints
// on its FIRST render the next time it opens, instead of an empty grid for as
// long as the round trip takes, and so that an intent prefetch (`prefetch.ts`)
// has somewhere to leave what it fetched. The overview pages read through
// `useRevalidated`, which holds them to the rules below on screen as well as
// in here.
//
// This is not a cache anybody reads INSTEAD of asking. Every page that paints
// from it also issues the read, and the answer replaces what was painted
// wholesale; so a remembered value is on screen for at most one revalidation,
// and that bound is the whole safety argument. It is also why it lives in
// memory only: it goes with the tab, and the first paint after a reload waits
// for the server like every other.
//
// What it must never do is paint one library's or one scope's rows for
// another, because a card's handlers act on the scope the page is showing.
// Hence:
//
// - Keys name the read's scope kind and id, and nothing is ever looked up
//   under a key other than the page's own.
// - Everything belongs to one store root: the `data_dir` a config read
//   reported. A different one -- a move made here (`putDataDir`), one made in
//   another tab of this origin (`ROOT_MOVED_KEY`), or one made anywhere else
//   and noticed by the next config read, which the app makes on every
//   navigation -- forgets all of it, and until a root is known nothing is
//   remembered or recalled at all. The other tab is told directly because the
//   config read comes too late for it: the app makes it from an effect, after
//   the route's first render, and the first render is the one that paints
//   from memory -- another library's cast, under ids that need not exist in
//   this one.
// - A read only lands if nothing was forgotten while it was in flight: a
//   root change, a write (`writing`) or a run seen to end (`noteRunSettled`)
//   bumps the epoch (`memoEpoch.ts`), and an answer issued before the bump
//   describes a store that may no longer be the one being shown. "Issued" is
//   when the request was SENT, not when a caller asked: the in-flight sharing
//   in `request` can hand a late caller the identical GET still on the wire
//   from before the bump, and that answer is as old as its request, however
//   late it was joined (`issuedEpoch`). A write and a run's end also retire
//   every in-flight GET, so after either nobody can join one sent before it;
//   a root change noticed by a config read does not, which is one of the ways
//   a page can still be handed an old answer, and why `useRevalidated` checks
//   the epoch before it paints one.
let memoRoot: string | null = null;
const memo = new Map<string, unknown>();
/** How many answers are kept, the least recently used going first. A reader
 *  moves between a handful of worlds and campaigns at a time, and that
 *  handful is all the memo has to cover; without a bound, a long session
 *  across a large library would keep every roster it ever opened -- each a
 *  summary row per character, which on a phone's WebView is heap that
 *  nothing is going to paint again. Worth tuning against real navigation. */
const MEMO_MAX = 24;

export function memoKey(...parts: string[]): string {
  // Ids cannot hold a colon (`paths.safe_id`), but a key that relied on it
  // would be one rule away from colliding; JSON says where each part ends.
  return JSON.stringify(parts);
}

export function forgetRemembered(): void {
  memo.clear();
  bumpMemoEpoch();
}

/** Forget everything remembered and retire every in-flight GET: what a write
 *  does on each side of its request, and what a run this client saw end does
 *  once. */
function forgetAndRetire(): void {
  forgetRemembered();
  retireAllInflight();
}

/** A detached run this client observed reaching a terminal state.
 *
 *  It wrote after the request that started it had settled -- a turn this tab
 *  re-attached to, an absorb, a review persisted for later -- so the second
 *  forget of the `writing` that sent it came too early to cover what it
 *  wrote. This is that forget, at the moment the run is known to be over:
 *  `draftRun`'s completion, a stream's terminal frame, and the run registry
 *  settling a send (through `onRunEnded`, because the registry is rendered
 *  under suites that mock this module wholesale). */
export function noteRunSettled(): void {
  forgetAndRetire();
  // And a page already painted from before the run asks again: forgetting
  // only covers the next visit.
  announceRunSettled();
}
onRunEnded(noteRunSettled);

/** The localStorage key a store move writes, which is how every OTHER tab of
 *  this origin hears of it: a `storage` event fires in the tabs that did not
 *  make the write, and only when the value changes, so each move writes one
 *  nobody has written before. */
const ROOT_MOVED_KEY = "grimoire.store.moved";

/** Tell this origin's other tabs the store moved. Fail-soft, like every
 *  localStorage use: a private window or blocked storage costs those tabs the
 *  early notice, and their next config read still catches the move. */
export function announceRootMoved(): void {
  try { localStorage.setItem(ROOT_MOVED_KEY, newAttemptId()); } catch { /* see above */ }
}

/** A move another tab announced: what this tab remembers, what it has in
 *  flight and its config all describe the old store. The root is forgotten
 *  with the rows, so nothing is recalled until a config read names the new
 *  one.
 *
 *  The same three signals `putDataDir` gives its own tab, and then one more.
 *  `campaignsChanged` alone re-read the rail from the new store while `App`
 *  kept the old one's data dir, connection and open campaign until the next
 *  navigation -- chrome describing two libraries at once. `configChanged` is
 *  what makes `App` read the config again. And the routed page is the one
 *  thing neither signal reaches: it was built from the old library's records,
 *  and a save from it would land in the new one under the old one's ids, so
 *  `storeMovedElsewhere` has `App` remount it (Codex review). */
function hearRootMoved(e: StorageEvent): void {
  if (e.key !== ROOT_MOVED_KEY) return;
  memoRoot = null;
  forgetRemembered();
  retireAllInflight();
  invalidateConfigCache();
  campaignsChanged();
  configChanged();
  storeMovedElsewhere();
}
if (typeof window !== "undefined") window.addEventListener("storage", hearRootMoved);

/** The store root a config response describes. Called with every config the
 *  client receives, so the memo notices a root change the first time any of
 *  them reports one. */
export function noteRoot(root: string | undefined): void {
  if (!root || root === memoRoot) return;
  memoRoot = root;
  forgetRemembered();
}

/** `read`, with its answer remembered under `key` if it lands before anything
 *  is forgotten. The outcome is passed through untouched, a failure included
 *  -- which also drops what was remembered under `key`: a read that could not
 *  confirm those rows is no reason to go on painting them on the next visit
 *  (a world deleted elsewhere would flash its old cast every time). */
export function remembering<T>(key: string, read: Promise<T>): Promise<T> {
  // Every GET `request` makes is stamped; the fallback is for one that was
  // not, which is sent by this very call.
  const epoch = issuedEpoch(read) ?? memoEpoch();
  const answer = read.then((value) => {
    if (epoch === memoEpoch() && memoRoot !== null) {
      memo.delete(key);   // re-inserted at the young end
      memo.set(key, value);
      if (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value as string);
    }
    return value;
  }, (err: unknown) => {
    if (epoch === memoEpoch()) memo.delete(key);
    throw err;
  });
  // The promise the caller holds is the one a page asks about, so it carries
  // the epoch of the request under it.
  noteIssued(answer, epoch);
  return answer;
}

export function recall<T>(key: string): T | undefined {
  if (memoRoot === null || !memo.has(key)) return undefined;
  // Painting from an answer is using it: it moves to the young end too.
  const value = memo.get(key);
  memo.delete(key);
  memo.set(key, value);
  return value as T;
}

/** Every write this client sends forgets everything remembered and retires
 *  every in-flight GET, both when it is sent and when it settles.
 *
 *  Every write, and all of it, rather than the entry for the write's own
 *  scope: a campaign's character rows are an overlay union over its world's,
 *  so a write to a world's roster changes every campaign's that inherits it --
 *  and which campaigns those are is something this client does not know. The
 *  other way round, a library move from a campaign writes the world. And the
 *  To do list answers from writes under `/api/config`, `/api/llm-connections`,
 *  `/api/inference`, `/api/todo`, `/api/modules` and the image routes.
 *  Over-forgetting is the safe direction and it is cheap (the next visit
 *  paints after its read, the way every visit did before anything was
 *  remembered); a precise rule would be a list of which endpoints touch which
 *  reads, which is the enumeration `retireAllInflight` declines for the same
 *  reason. It does mean a turn forgets too -- `streamPost` comes through here
 *  -- so nothing is painted from memory on the first visit after play.
 *
 *  Retiring is what stops a read asked for after the write from joining one
 *  sent before it. Whoever already holds the retired promise still gets its
 *  answer; the epoch keeps that answer out of memory, and `useRevalidated`
 *  keeps it off the screen.
 *
 *  Twice, because a read issued while the write is on the wire can be
 *  answered from before it and land after the first forget. The second
 *  forget drops that answer too; a page open across the write re-reads for
 *  itself, as it always has. */
export async function writing<T>(send: () => Promise<T>): Promise<T> {
  forgetAndRetire();
  try {
    return await send();
  } finally {
    forgetAndRetire();
  }
}

// The config is fetched at app start and again by campaign/config views; it
// only changes through putConfig / a data-dir move, so cache it until then.
let configCache: Promise<Config> | null = null;

export function invalidateConfigCache() {
  configCache = null;
  // The resolved cache is only half of what is stale. An in-flight GET of the
  // same path is a read issued before whatever prompted this call, and the
  // next getConfig() -- now that the cache is empty -- would join it and store
  // the pre-change connection. Both holders of an old answer go together.
  retireInflight("/api/config");
}

/** The config read this client is holding, if any. `getConfig` and
 *  `putConfig` decide what goes in; everything that must drop it goes
 *  through `invalidateConfigCache` above. */
export function cachedConfig(): Promise<Config> | null {
  return configCache;
}

export function cacheConfig(config: Promise<Config> | null): void {
  configCache = config;
}
