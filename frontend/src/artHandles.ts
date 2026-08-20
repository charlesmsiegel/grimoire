/** Hide art handles from text that is still streaming.
 *
 *  A narrator turn may include a picture by writing `[[art:...]]`, which
 *  `context.art.resolve_handles` rewrites into markdown when the reply is
 *  persisted — after the stream has finished. So the reader watching deltas
 *  arrive sees the raw handle sitting in the prose for a second or two, and
 *  then watches it turn into a picture.
 *
 *  DISPLAY ONLY. Nothing here touches what is sent or stored: the server does
 *  the real work on the full reply, and this is the live view declining to show
 *  a token that means nothing to a reader and is about to become an image.
 *
 *  A PARTIAL handle is hidden too — the tail of a stream is routinely half a
 *  token, and letting `[[art:characters:sera` appear and then vanish is the
 *  same flicker one step smaller. */
const COMPLETE = /`?\[\[art:[^\][]*(?::[^\][]*)*\]\]`?/g;

/** An unfinished handle at the very END of the buffer: everything from a `[[`
 *  that could still be growing into one. Anchored, so a stray `[[` earlier in
 *  the prose is left exactly where the author put it. */
const PARTIAL = /`?\[\[(?:a(?:r(?:t(?::[^\][]*)?)?)?)?$/;

export function hideArtHandles(text: string): string {
  return text.replace(COMPLETE, "").replace(PARTIAL, "");
}
