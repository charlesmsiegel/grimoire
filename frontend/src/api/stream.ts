export type RollProposalPayload = {
  id: string;
  check?: string; check_label?: string;
  actor?: string; actor_label?: string;
  difficulty?: number | null; modifier?: number; reason?: string;
  available?: Record<string, [string, string][]>;
  problems: string[];
};
// `post_returned` on a chat error means the backend took the player's post back
// off the transcript (#95) — so the composer has to give them their words back,
// or a failed send silently destroys what they typed.
/** A fresh attempt id: this client's name for one turn.
 *
 *  Chosen BEFORE the request goes out, which is the whole point -- it makes the
 *  turn addressable in the window between the server accepting the work and its
 *  leading `run` frame reaching the browser, and that window is exactly where a
 *  dying connection lands. It is also the idempotency key: re-sending the same
 *  id replays the original outcome instead of running the turn twice.
 *
 *  `randomUUID` is unavailable on insecure origins and in some test
 *  environments, so it is not assumed. The fallback does not need to be
 *  cryptographic -- ids are only ever compared within one scene, and the server
 *  never treats one as a secret.
 */
export function newAttemptId(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();
  return `a-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// `run` is the leading frame every producing route now emits, before any delta
// and before anything can fail. It names the detached run this send started, so
// a client whose connection dies immediately can still address it -- to cancel
// it, to poll it, or to re-attach and finish reading the reply.
export type RunHandle = {
  id: string;
  attempt_id: string;
  state: "running" | "landed" | "failed" | "cancelled";
  next_index: number;
  /** Present on a run the server recorded as failed. The discovery routes carry
   *  it so a client that was away while the turn died can say WHY, rather than
   *  silently unlocking a composer over a scene that never got its reply. */
  error?: { detail: string; kind: string; post_returned?: boolean } | null;
};
export type ChatEvent = {
  delta?: string; done?: boolean; proposal?: RollProposalPayload;
  run?: RunHandle;
  error?: { detail: string; kind: string; post_returned?: boolean };
};

export type LocalizeSummary = {
  total: number;
  localized: number;
  skipped: number;
  failed: number;
  capped: boolean;
};
export type LocalizeEvent = {
  total?: number;
  done?: number;
  summary?: LocalizeSummary;
  error?: { detail: string; kind: string };
};

/** One pass of `POST /worlds/{wid}/characters/taglines/generate` (#57).
 *
 *  Unlike the per-character preview, this route WRITES each sentence as it
 *  lands — so a frame carrying `tagline` is a fact about the store, not a
 *  draft awaiting a save. `skipped` says why nothing was written for that
 *  character — a "blank" reply, a tagline "already set" by someone else, an
 *  "unreadable card", or one "gone" (deleted while its own call was in
 *  flight); `error` is the provider failure that stopped the run, and no
 *  character after it was attempted. */
export type TaglineBatchSummary = {
  total: number;
  written: number;
  skipped: number;
  stopped: boolean;
};
export type TaglineBatchEvent = {
  total?: number;
  done?: number;
  character?: string;
  name?: string;
  tagline?: string;
  skipped?: string;
  summary?: TaglineBatchSummary;
  error?: { detail: string; kind: string };
};

export type ChubGallerySummary = { attempted: number; stored: number };
export type ChubGalleryEvent = {
  total?: number;
  done?: number;
  summary?: ChubGallerySummary;
  error?: { detail: string; kind: string };
};

// Aborting a stream is a deliberate outcome, not a failure, but `fetch` and the
// body reader report it the same way they report a dead network: a rejected
// promise. Everything that catches around a stream needs to tell the two apart
// before it decides whether to show the user an error, so the test lives here
// next to the parser rather than being re-derived at each call site.
//
// Matched on `name` rather than `instanceof DOMException`: the abort can be
// raised by `fetch`, by `read()`, or by a signal that was already aborted
// before the call, and only the name is common to all three.
export function isAbortError(err: unknown): boolean {
  return typeof err === "object" && err !== null
    && (err as { name?: string }).name === "AbortError";
}

// Appends a chunk to `buffer`, emits each complete `data:` event, returns the leftover buffer.
// A frame with no `data:` line is skipped — which is what makes the backend's
// `: heartbeat` comments free to ignore here (#95).
export function parseSSEChunk<T = ChatEvent>(
  buffer: string,
  chunk: string,
  emit: (event: T) => void,
  onIndex?: (index: number) => void,
): string {
  buffer += chunk;
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const lines = raw.split("\n");
    // BEFORE the `data:` check, and for every frame including comment ones.
    // The resume cursor has to come from the wire index, not from counting the
    // events this function surfaced: heartbeats are comment frames with no
    // `data:` line at all, so a counter undercounts by one per heartbeat and
    // resumes early, replaying text the reader has already seen mid-reply.
    const id = lines.find((l) => l.startsWith("id:"));
    if (id && onIndex) {
      const n = Number(id.slice("id:".length).trim());
      if (Number.isInteger(n)) onIndex(n);
    }
    const line = lines.find((l) => l.startsWith("data:"));
    if (!line) continue;
    const data = line.slice("data:".length).trim();
    if (!data) continue;
    try {
      emit(JSON.parse(data) as T);
    } catch {
      // ignore malformed event fragments
    }
  }
  return buffer;
}
