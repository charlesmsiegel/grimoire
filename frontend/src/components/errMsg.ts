/** The message to show for a failed call, from whatever shape it arrived in.
 *
 *  A rejection reaches a handler as an `ApiError` (`detail` a string), as a
 *  stream `error` frame (`{detail, kind}`, likewise), or as anything else a
 *  promise can reject with. The last group is why the `detail` here is read
 *  structurally and why a non-string one falls through rather than being
 *  rendered: React throws on an object child, and a 422 from FastAPI's own
 *  validator answers `detail` as an ARRAY of error rows -- the one shape the
 *  app's routes never produce and the framework produces for free.
 *
 *  It does NOT dig for the `kind`: `main.py`'s HTTPException handler flattens
 *  a dict detail, so every route that tags a failure puts `detail` and `kind`
 *  side by side at the top level of the body and `ApiError` carries both.
 *  `isOffline` below reads it straight off. (`client.test.ts` pins that lift;
 *  an earlier version of this comment claimed the 502s nested their detail one
 *  level deep, which the handler has never done.) */
export function errMsg(err: any): string {
  const d = err?.detail;
  return typeof d === "string" ? d : (d?.detail ?? String(err));
}

/** Is this failure the network being gone rather than anything the app did?
 *
 *  `LLMError.kind` is the seam (#210): every provider tags a refused
 *  connection, a DNS failure or a dropped socket `network`, and that tag
 *  survives to the browser two ways — as `kind` on the JSON error body (the
 *  HTTPException handler in `main.py` flattens a dict detail, so it arrives
 *  top-level and `ApiError` carries it) and as `kind` on an SSE `error` frame,
 *  which reaches a handler as a plain `{detail, kind}` object. Both are read
 *  here structurally, which is why this takes `unknown` rather than `ApiError`.
 *
 *  Deliberately only `network`. `missing_key` is the one it is most often
 *  confused with and the one it must stay apart from: an unconfigured key is
 *  not an offline app, and the two have opposite fixes. */
export function isOffline(err: unknown): boolean {
  return typeof err === "object" && err !== null
    && (err as { kind?: unknown }).kind === "network";
}
