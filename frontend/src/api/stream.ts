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
export type ChatEvent = {
  delta?: string; done?: boolean; proposal?: RollProposalPayload;
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
): string {
  buffer += chunk;
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = raw.split("\n").find((l) => l.startsWith("data:"));
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
