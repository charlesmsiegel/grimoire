/** One URL path segment, encoded the way Python's `quote(safe="")` encodes it.
 *
 *  `encodeURIComponent` alone is not that rule: it deliberately leaves
 *  `!'()*` alone, and `(` and `)` are exactly the two characters that end a
 *  markdown destination. The tail escape is the standard RFC 3986 correction.
 *
 *  Needed in two places that must agree — the markdown a picker inserts, and
 *  the request paths that address an image — because `assets.storable` accepts
 *  names URL syntax owns: `a#b` truncates a request at the fragment, `my art`
 *  breaks the path, and a literal `%` can decode into something else entirely.
 *  A leaf module with no imports of its own, so both sides can reach it. */
export function encodeSegment(seg: string): string {
  return encodeURIComponent(seg).replace(
    /[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}
