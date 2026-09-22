import { useEffect, useState } from "react";
import type { ThumbSet } from "../api/thumbs";

export function initialsOf(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("").toUpperCase();
}

/** Square portrait with an initials fallback (no src, or the file 404s).
 *
 *  `src` is a URL, or a `thumbSet` where the slot can be drawn large enough
 *  that the reader's screen density should pick the bucket.
 *
 *  Lazy and off-thread by default. A portrait is always one of many -- a cast
 *  column, a transcript of speaker plates, a rail of records -- and the ones
 *  scrolled out of view have no reason to be fetched or decoded before the
 *  ones on screen; a lazy image already in the viewport loads as soon as it is
 *  laid out, so nothing visible waits on this. */
export function Portrait({ src, name, focus }:
  { src: string | ThumbSet | null; name: string; focus?: number | null }) {
  const [broken, setBroken] = useState(false);
  // Keyed on the URL, never on the object: a `thumbSet` is rebuilt every
  // render, and an effect on it would clear `broken` straight after a failed
  // load set it -- a 404ing portrait re-requesting and failing in a loop.
  const url = typeof src === "string" ? src : src?.src ?? null;
  useEffect(() => setBroken(false), [url]);
  if (!src || broken) {
    return <span className="portrait-initials" aria-hidden>{initialsOf(name)}</span>;
  }
  const set = typeof src === "string" ? { src } : src;
  return <img className="portrait" alt={`${name} portrait`} {...set}
              loading="lazy" decoding="async"
              style={focus == null ? undefined : { objectPosition: `${focus}% ${focus}%` }}
              onError={() => setBroken(true)} />;
}
