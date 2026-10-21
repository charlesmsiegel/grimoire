/** URL builders the endpoint modules share: a scope's base path and the
 *  `{w, v}` query every image URL takes. Split out of `client.ts`, which
 *  re-exports `ImageOpts`. */
import { THUMB_REV } from "./thumbs";
import type { EntityScope } from "./types";
import { encodeSegment } from "../urlSegment";

export function entityBase(scope: EntityScope): string {
  return scope.kind === "world" ? `/api/worlds/${scope.id}` : `/api/campaigns/${scope.id}`;
}

// Hand-managed/restored scope ids may contain a URL fragment or query delimiter.
// Membership uses a path segment, never the raw browser URL spelling.
export function membershipBase(scope: EntityScope): string {
  return entityBase({ ...scope, id: encodeSegment(scope.id) });
}

/** What an image URL can ask of the route that serves it: `w`, a downscale
 *  (`api/thumbs.ts` says which), and `v`, the store's token for the bytes,
 *  which makes the response cache immutable. Either left out, or empty, is
 *  simply not asked -- a bare URL is the revalidated one. */
export type ImageOpts = { w?: number | undefined; v?: string | null | undefined };

/** `path` with `opts` as its query. One builder for every image URL, so `w`
 *  and `v` read the same way, in the same order, wherever a picture is drawn.
 *  A `w` brings `t` with it, the thumbnail pipeline's revision (`THUMB_REV`),
 *  so a thumbnail made the old way is never the one a browser has cached for
 *  the URL. */
export function withImageQuery(path: string, opts?: ImageOpts): string {
  const q = new URLSearchParams();
  if (opts?.w) {
    q.set("w", String(opts.w));
    q.set("t", String(THUMB_REV));
  }
  if (opts?.v) q.set("v", opts.v);
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}
