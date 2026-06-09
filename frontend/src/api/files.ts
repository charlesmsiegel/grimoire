/**
 * URL for a data-root-relative file path served by `GET /api/files/{path}`
 * (generated campaign images and their thumbnails).
 *
 * encodeURI doesn't encode "+", "?", "#"; a "+" in a filename breaks the
 * URL. Encode each path segment with encodeURIComponent then re-join so
 * "/" boundaries are preserved.
 */
export function fileUrl(path: string): string {
  return `/api/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}
