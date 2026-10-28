import { api } from "../api/client";

// ---- cancelling a turn (#95) ----

/** api.chat that streams `deltas` and then hangs until its signal aborts,
 *  rejecting the way fetch does — the shape a real in-flight turn has. */
export function hangingChat(deltas: string[] = []): typeof api.chat {
  return async (_c, _s, _t, onEvent, _r, signal) => {
    deltas.forEach((d) => onEvent({ delta: d }));
    await new Promise<void>((_resolve, reject) => {
      signal?.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  };
}

/** A stream that hangs until aborted, whatever position its signal sits in.
 *
 *  `hangingChat` destructures by position and so only fits `api.chat`. Retry
 *  and the proposal resolution put the signal one earlier, and a mock that
 *  listened to the wrong argument would hang forever instead of reporting.
 */
export function hangingStream() {
  return async (...args: unknown[]) => {
    const signal = args.find((a): a is AbortSignal => a instanceof AbortSignal);
    await new Promise<void>((_resolve, reject) => {
      signal?.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  };
}
