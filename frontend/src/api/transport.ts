/** How a request is made: the `ApiError` family, JSON requests with in-flight
 *  sharing of identical GETs, the multipart and archive uploads, and the two
 *  SSE readers (`streamPost` to start work, `streamGet` to join it).
 *
 *  Split out of `client.ts`, which re-exports the error classes. Every write
 *  goes through `writing` (`./freshness`), so nothing here decides on its own
 *  what a remembered read may still say. */
import { isProviderFailure } from "./errors";
import { inflightGets, invalidateConfigCache, noteRunSettled, retireInflight, writing } from "./freshness";
import { noteIssued } from "./memoEpoch";
import { type ChatEvent, parseSSEChunk } from "./stream";
import { configChanged } from "../appEvents";

export class ApiError extends Error {
  /** `body` is the whole decoded error payload. Most callers only need
   *  `detail`/`kind`, but a route can attach structured data a retry has to act
   *  on rather than merely display — the chronicle save's conflict rows (#111)
   *  are the first — and flattening the response to two strings would drop it. */
  constructor(public status: number, public detail: string, public kind?: string,
              public body?: Record<string, unknown>) {
    super(detail);
  }
}

/** A detached run SEEN to end other than landed: an `ApiError`, so every
 *  caller reads it as before, and the one sign that its status is the run's
 *  own failure rather than a request that did not answer -- a 5xx here is
 *  the provider's, and the run it names is over. */
export class RunEndedError extends ApiError {}

/** A continuity Refresh refused by its own POST (409 `busy` while the store
 *  moves, 404 for a campaign that is not there): no run was started, so no
 *  sweep saved anything (§26). Told apart from the run's own failures by
 *  class, not by shape -- a poll's `run_gone` 404 looks just like a refusal,
 *  and comes from a run that may have saved its findings before it was
 *  reaped or the server restarted. */
export class RefreshRefused extends ApiError {}

// `attempt` is this client's own name for one piece of work, sent as
// `X-Grimoire-Attempt`. It is the same contract `streamPost` already carries and
// it is here for the same two reasons: it makes a duplicate delivery adopt the
// original run instead of paying for the work twice, and it is the only handle
// left when the response never arrives -- which for a detached `draft` is the
// difference between finding the run and starting a second one.
async function requestRaw<T>(method: string, path: string, body?: unknown,
                             signal?: AbortSignal, attempt?: string): Promise<T> {
  if (method === "GET") return fetchJson<T>(method, path, body, signal, attempt);
  // Anything else may change what a remembered read says (see `writing`).
  return writing(() => fetchJson<T>(method, path, body, signal, attempt));
}

async function fetchJson<T>(method: string, path: string, body?: unknown,
                            signal?: AbortSignal, attempt?: string): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(attempt ? { "X-Grimoire-Attempt": attempt } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    ...(signal === undefined ? {} : { signal }),
  });
  if (!res.ok) {
    const data = await errorBody(res);
    const err = new ApiError(res.status, data.detail ?? res.statusText, data.kind, data);
    // A provider that just refused us has changed what the status bar is
    // claiming about it (#146). The server recorded the failure as it
    // happened; this is what makes the client go and read it, rather than
    // showing yesterday's verdict until the next navigation.
    if (isProviderFailure(err)) {
      invalidateConfigCache();
      configChanged();
    }
    throw err;
  }
  return res.json() as Promise<T>;
}

// `signal` aborts the request. Only non-GETs and `fresh` GETs take one, and
// deliberately so: an aborted shared GET would settle the shared promise below
// for every caller waiting on it, not just the one that asked to stop -- and a
// `fresh` read is never shared, so its promise is its caller's alone.
export function request<T>(method: string, path: string, body?: unknown,
                           opts?: { fresh?: boolean; signal?: AbortSignal | undefined;
                                    attempt?: string }): Promise<T> {
  if (method !== "GET" || opts?.fresh) {
    if (opts?.fresh) retireInflight(path);
    const sent = requestRaw<T>(method, path, body, opts?.signal, opts?.attempt);
    if (method === "GET") noteIssued(sent);
    return sent;
  }
  const pending = inflightGets.get(path);
  if (pending) return pending as Promise<T>;
  // Only retire the entry if it is still *this* promise. A retired read still
  // settles, and by then a later caller may have installed a replacement for
  // the same path -- an unconditional delete evicts that live entry, so the
  // callers after it each issue their own request instead of joining it. On
  // /api/campaigns that is a full scan of every campaign's scenes per caller.
  const p: Promise<T> = requestRaw<T>(method, path, body).finally(() => {
    if (inflightGets.get(path) === p) inflightGets.delete(path);
  });
  inflightGets.set(path, p);
  noteIssued(p);
  return p;
}

export function sleepUnlessAborted(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      // The same shape `fetch` throws, so `isAbortError` recognises it and a
      // caller that already tells an abort from a failure needs no new branch.
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Read an SSE stream from a GET, for attaching to a run already in flight.
 *
 *  `streamPost` exists for starting work; this exists for joining it. Same
 *  parser, same index callback, no body and no attempt header -- the run is
 *  already named in the path.
 */
export async function streamGet<T = ChatEvent>(
  path: string,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
  onIndex?: (index: number) => void,
): Promise<void> {
  const res = await fetch(path, signal === undefined ? {} : { signal });
  await readSSE(res, onEvent, onIndex);
}

/** Read an SSE reply to its end, handing each frame to `onEvent`: the half
 *  `streamGet` and `streamPost` share once their request has been answered.
 *  A refusal, or a reply with no body to read, is the `ApiError` `request`
 *  would have raised. */
async function readSSE<T>(res: Response, onEvent: (e: T) => void,
                          onIndex?: (index: number) => void): Promise<void> {
  if (!res.ok || !res.body) {
    const data = await errorBody(res);
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }),
                              onEvent, onIndex);
  }
}

/** A failed reply's decoded body, or `{}` when it carried no JSON.
 *
 *  Typed rather than left `any`: this app's error handler puts `kind` at the
 *  top level of the body, so the shape is known and reading it blind only
 *  costs the type-checker's help. One reader for every path that builds an
 *  `ApiError` from a reply, so they cannot drift apart. */
type ErrorBody = { detail?: string; kind?: string } & Record<string, unknown>;

async function errorBody(res: Response): Promise<ErrorBody> {
  return await res.json().catch(() => ({})) as ErrorBody;
}

/** The reply of a raw `fetch` as `request` would hand it back: the parsed
 *  JSON on success, an `ApiError` carrying the server's `detail` and `kind`
 *  otherwise. `requestForm` and `postZip` bypass `request` only because their
 *  bodies are not JSON; the error shape must not diverge with them. */
async function parseReply<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const data = await errorBody(res);
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

export async function requestForm<T>(path: string, form: FormData, method = "POST",
                                     attempt?: string, signal?: AbortSignal): Promise<T> {
  return writing(async () => {
    const res = await fetch(path, {
      method, body: form,
      ...(attempt ? { headers: { "X-Grimoire-Attempt": attempt } } : {}),
      ...(signal === undefined ? {} : { signal }),
    });
    return parseReply<T>(res);
  });
}

/** POST an archive as the whole request body -- the two import endpoints. */
export async function postZip<T>(path: string, file: Blob): Promise<T> {
  return writing(async () => {
    const res = await fetch(path, { method: "POST", body: file,
                                    headers: { "content-type": "application/zip" } });
    return parseReply<T>(res);
  });
}

// `signal` unsubscribes; it no longer cancels. Aborting used to BE the cancel:
// the backend saw the disconnect, persisted whatever the model had produced,
// and unwound. A turn is detached from its request now -- that is the whole
// point, so a locked phone does not kill a generation -- so an abort closes
// only this subscriber and the run carries on. A caller that means "stop
// generating" has to say so, with `api.cancelRun`; aborting alone leaves the
// provider spending and the scene held. Callers still tell an abort from a
// real failure with `isAbortError`.
//
// `attempt` is the caller's own id for this turn, sent as `X-Grimoire-Attempt`.
// It is what makes a turn addressable BEFORE its leading `run` frame arrives:
// the id is chosen here, so a connection that dies between the server
// accepting the work and the first frame reaching the browser still leaves the
// client able to find the run (`api.findRun`) and stop it. It doubles as the
// idempotency key -- re-sending the same id replays the original outcome
// instead of running the turn twice.
//
// A POST, so it goes through `writing`: a turn, a localize or a tagline batch
// all write the store as they stream. Its terminal frame is a run seen to end
// as well (`settlingOn`), which matters when the frame arrives well before the
// response closes.
export function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
  attempt?: string,
  onIndex?: (index: number) => void,
): Promise<void> {
  return writing(() => streamPostRaw(path, body, settlingOn(onEvent), signal, attempt,
                                     onIndex));
}

/** Whether a stream frame is the last its run will send.
 *
 *  A turn ends on `done: true` or an `error`. The batch streams (localize,
 *  taglines, gallery) count progress in a numeric `done` and end on a
 *  `summary` or an `error` -- so `done` counts only when it is `true`. */
function isTerminalFrame(e: unknown): boolean {
  if (typeof e !== "object" || e === null) return false;
  const f = e as { done?: unknown; error?: unknown; summary?: unknown };
  return f.done === true || (f.error !== undefined && f.error !== null)
    || (f.summary !== undefined && f.summary !== null);
}

/** `onEvent`, with a terminal frame first noted as a run seen to end
 *  (`noteRunSettled`). Before the caller hears it, so whatever the caller does
 *  next -- reload a list, navigate -- reads after the forget rather than from
 *  before it. */
export function settlingOn<T>(onEvent: (e: T) => void): (e: T) => void {
  return (e) => {
    if (isTerminalFrame(e)) noteRunSettled();
    onEvent(e);
  };
}

async function streamPostRaw<T>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
  attempt?: string,
  onIndex?: (index: number) => void,
): Promise<void> {
  // Tagged so a caller can tell "the server never got this" from "the server
  // got it and then something went wrong" (#95). The line between them is the
  // response: `post_chat` appends the player's post and *then* returns the
  // streaming response, so a response arriving means the post exists, and a
  // failure before one means it may never have been written. A chat that
  // cannot tell the difference has to guess whether the prompt still exists
  // anywhere, and guessing either way loses or duplicates it.
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(attempt ? { "X-Grimoire-Attempt": attempt } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...(signal === undefined ? {} : { signal }),
  }).catch((err) => {
    if (err && typeof err === "object") (err as { beforeResponse?: boolean }).beforeResponse = true;
    throw err;
  });
  await readSSE(res, onEvent, onIndex);
}
