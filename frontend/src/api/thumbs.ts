/** The widths stored art is asked for through `?w=`, by the slot it fills.
 *
 *  Every image route answers `?w=` with a WebP downscale it caches on disk, and
 *  a slot drawing a portrait at a few dozen pixels has no use for the original
 *  -- a grid that asked for originals moved every card's full file and decoded
 *  it at full resolution, which on a phone's WebView is memory as much as time.
 *
 *  Mirrors `THUMB_BUCKETS` in `backend/src/grimoire/routes/common.py` (less
 *  320, which the server keeps for the gallery URLs it builds itself). The
 *  server snaps any width up to a bucket anyway; asking for one exactly is what
 *  keeps a `srcset`'s descriptors true to the bytes that come back, and keeps
 *  one picture to a handful of cache entries however many layouts draw it.
 *
 *  - `row`: a fixed slot up to ~28 CSS px (speaker plates, owner stacks, chips)
 *  - `face`: a fixed slot up to ~56 CSS px (the hub's faces, inspector rows)
 *  - `tile` / `large`: anything bigger, through `thumbSet`, which lets the
 *    browser pick by screen density. Those two bounds are `coverWidth` of the
 *    bucket over a DPR of 3, so a fixed slot stays sharp on a phone.
 */
export const THUMB = { row: 128, face: 256, tile: 512, large: 1024 } as const;

/** The server's thumbnail pipeline revision, sent as `t` beside every `w`
 *  (`withImageQuery` in `api/client.ts`). Mirrors `REVISION` in
 *  `backend/src/grimoire/store/thumbs.py`, which test_thumbs.py holds this to.
 *
 *  No route reads it. It is in the URL for the browser, whose cache is keyed on
 *  the URL: a `?v=` thumbnail is cached immutable, so one made before a change
 *  to how thumbnails are made -- a phone photo on its side, from before they
 *  were stood upright -- would otherwise go on being drawn for a year. */
export const THUMB_REV = 3;
export type Thumb = (typeof THUMB)[keyof typeof THUMB];
const BUCKETS: readonly Thumb[] = Object.values(THUMB);

/** The width, in device pixels, a thumbnail of `bucket` fills without
 *  upscaling when it is cover-cropped.
 *
 *  The server fits a thumbnail inside a `bucket`-by-`bucket` box, so the bucket
 *  is its LONG edge. A cover crop fills its slot along the image's SHORT edge,
 *  and character art is mostly 2:3 portraits, whose short edge is two thirds of
 *  the long one (a 3:2 landscape in a square slot is the same sum turned on its
 *  side). Describing the 512 as `512w` would let a browser fill a slot 512
 *  device pixels wide with a 341px-wide portrait -- the blur this exists to
 *  prevent. A square picture is under-described by the same factor, which only
 *  costs it being fetched a bucket early. */
const coverWidth = (bucket: number) => Math.floor((bucket * 2) / 3);

export type ThumbSet = { src: string; srcSet: string; sizes: string };

/** The width the original is described by when a slot offers it as the top
 *  candidate (`thumbSet`'s `original`). Nobody measured the original, so this
 *  claims only "more than the 1024 bucket guarantees" -- enough that a browser
 *  reaches for it where the 1024 would be drawn soft, and never where the 1024
 *  fills the slot. */
export const ORIGINAL_W = 1536;

/** `src`, `srcSet` and `sizes` for art cover-cropped into a slot `sizes` wide.
 *
 *  For any slot that can be large, rather than a fixed bucket: a 134px dossier
 *  portrait wants a 256 on a desktop and a 1024 on a DPR-3 phone, and only the
 *  browser knows which one it is on. `sizes` is the slot's CSS width -- a
 *  media-conditioned list where the layout changes it -- and should stay an
 *  upper bound on what the stylesheet draws, since an under-stated slot is a
 *  blurry one -- but a tight one where it can be, since the slack is paid in a
 *  whole bucket. `src` is the candidate where `srcset` is not honoured, and the
 *  one a test reads.
 *
 *  The 1024 is a ceiling, not a guarantee: a slot wider than 682 device pixels
 *  (a full-width card on a DPR-3 phone) gets it and is drawn under device
 *  resolution -- for a portrait cover-cropped into a wide slot, half again
 *  under. A slot that can be that wide passes `original`, the full-size URL,
 *  which joins the candidates at `ORIGINAL_W`: the browser takes it only where
 *  the 1024 falls short, so a desktop keeps the thumbnail and a phone gets the
 *  sharpness it had before thumbnails. A grid of small cards passes nothing,
 *  and a slot routinely wider than that (the inspector's location picture)
 *  keeps the original outright.
 *
 *  The keys are in the order they must reach the element: spread onto an
 *  `<img>`, React 18 sets attributes in prop order, and an engine that starts
 *  a fetch as `src` lands (WebKit) picks with whatever `sizes`/`srcset` it
 *  has by then -- fetching the fallback, or a 100vw pick, and then the right
 *  one. For the same reason `loading="lazy"` goes BEFORE the spread (Firefox
 *  ignores a `loading` set after `src`). */
export function thumbSet(url: (w: Thumb) => string, sizes: string,
                         src: Thumb = THUMB.tile, original?: string): ThumbSet {
  const candidates = BUCKETS.map((w) => `${url(w)} ${coverWidth(w)}w`);
  if (original) candidates.push(`${original} ${ORIGINAL_W}w`);
  return { sizes, srcSet: candidates.join(", "), src: url(src) };
}
