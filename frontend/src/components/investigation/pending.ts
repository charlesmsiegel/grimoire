/** A detached investigation run, remembered past the panel that started it.
 *
 *  The run is detached on the server and spends whether or not anyone is
 *  watching, so leaving the page must not lose what it produces or let a
 *  second one start: a remount re-attaches by the attempt id kept here.
 *
 *  Every storage touch is wrapped: storage can throw (a private window,
 *  blocked site data), and a panel that cannot remember still works. */
import { ApiError } from "../../api/client";

/** Where a run is remembered: per library (`root`, the store root a config
 *  read reports) and per campaign. A campaign id is a slug, and another
 *  library can hold one of the same name, so a record keyed by the slug
 *  alone would show one library's question in another. */
export function pendingKey(kind: string, root: string, cid: string): string {
  return `grimoire.${kind}:${JSON.stringify([root, cid])}`;
}

export function readPending<T>(key: string, valid: (v: Record<string, unknown>) => boolean): T | null {
  try {
    const raw = localStorage.getItem(key);
    const v: unknown = raw ? JSON.parse(raw) : null;
    if (v && typeof v === "object" && typeof (v as { attempt?: unknown }).attempt === "string"
        && valid(v as Record<string, unknown>)) return v as T;
  } catch { /* nothing remembered */ }
  return null;
}

export function writePending(key: string, value: object | null): void {
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
  } catch { /* in memory only */ }
}

/** Whether a failure settles the run. An `ApiError` is a reply -- the start
 *  refused, the run ended failed, or it is gone (404) -- so the record goes.
 *  Anything else carried no reply (a lost 202, a poll that ran out of
 *  patience over a run still going) and proves nothing: the record stays, for
 *  a remount or a retry to pick the same run back up. */
export const settles = (err: unknown) => err instanceof ApiError;
