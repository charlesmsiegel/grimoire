import { api } from "../api/client";
import type { RunHandle } from "../api/stream";

/** Discover a lost send, fencing a POST still in setup before calling it
 * absent. A GET returning no run says nothing about a reservation that has
 * not happened yet; attempt-cancel records that decision at the reservation
 * boundary. Both play presentations need this ordering before releasing the
 * composer or returning words which the delayed POST might still append. */
export async function findOrFenceAttempt(
  cid: string, sid: string, attempt: string, signal?: AbortSignal,
): Promise<RunHandle | null> {
  const { run } = await api.findRun(cid, sid, attempt, signal);
  signal?.throwIfAborted();
  if (run) return run;
  const fenced = await api.cancelAttempt(cid, sid, attempt);
  signal?.throwIfAborted();
  return fenced.run;
}
