/** What a failed call MEANS, read off whatever the rejection turned out to be.
 *
 *  A leaf module with no imports, deliberately. Half the components that
 *  render an error are tested against a `vi.mock("../api/client")` that
 *  replaces the module wholesale, so a helper living there is undefined by
 *  the time the component asks for it — which is what happened when these two
 *  were first put next to `ApiError` (every such suite went red at once).
 *  Nothing here may import from `client.ts`.
 *
 *  Both read the rejection structurally rather than testing `instanceof
 *  ApiError`, and that is load-bearing rather than lazy: a rejection also
 *  arrives as an SSE `error` frame, which is a plain `{detail, kind}` object
 *  and never an `ApiError` at all. */

/** The message to show for a failed call.
 *
 *  `catch (err: any)` followed by `err.detail ?? String(err)` is what this
 *  replaces, at every call site that had it. The `any` bought nothing and cost
 *  the compiler's check on everything else the handler touched.
 *
 *  Two deliberate departures from that `??`, both because `??` only guards
 *  null and undefined:
 *
 *  - an EMPTY detail falls back instead of rendering as a blank banner. The
 *    old expression showed the empty string, so a backend that answered
 *    `{"detail": ""}` produced an error box with nothing in it.
 *  - a null or non-object rejection no longer throws. `err.detail` on `null`
 *    is a TypeError raised inside the `catch`, which is the one place a throw
 *    has nowhere left to go.
 *
 *  A non-string detail falls back too. The app's own routes never send one —
 *  `main.py`'s HTTPException handler flattens a dict detail, so `detail` and
 *  `kind` arrive side by side at the top level — but FastAPI's own validator
 *  answers a 422 with `detail` as an ARRAY of error rows, and React throws on
 *  an object child. */
export function errorText(err: unknown): string {
  const detail = typeof err === "object" && err !== null
    ? (err as { detail?: unknown }).detail
    : undefined;
  return typeof detail === "string" && detail ? detail : String(err);
}

/** Is this failure the network being gone rather than anything the app did?
 *
 *  `LLMError.kind` is the seam (#210): every provider tags a refused
 *  connection, a DNS failure or a dropped socket `network`, and that tag
 *  reaches the browser two ways — as `kind` beside `detail` on an error body,
 *  which `request` lifts onto `ApiError`, and as `kind` on an SSE `error`
 *  frame. `client.test.ts` pins the lift; without it nothing here can fire.
 *
 *  Deliberately only `network`. `missing_key` is the one it is most often
 *  confused with and the one it must stay apart from: an unconfigured key is
 *  not an offline app, and the two have opposite fixes. */
export function isOffline(err: unknown): boolean {
  return typeof err === "object" && err !== null
    && (err as { kind?: unknown }).kind === "network";
}
