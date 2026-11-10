// The shell's routes (108). A location is a hash: #/<route>/<segment>/...?<query>. Each segment is one
// URI-encoded string, so a reference such as a ChatRef travels exactly, never re-derived from a name.
// Pure functions only: the shell and every page share them, and they run under `node --test`.

export const ROUTE = /^[a-z][a-z0-9-]{0,39}$/;

// The routes the Wave 7 pages own, so a page can link to another sprint's screen before (or without) that
// screen existing: the shell shows an opened-but-unregistered reserved route as not yet available, naming
// its owner, instead of a broken page. `segments` says what the path after the route carries.
export const RESERVED = Object.freeze({
  home: { owner: "108", segments: "", what: "Home" },
  setup: { owner: "108", segments: "", what: "Library setup" },
  chats: { owner: "111", segments: "", what: "Chats (?filter=character|scenario&source=SourceRef)" },
  chat: { owner: "111", segments: "ChatRef", what: "One chat" },
  start: { owner: "111", segments: "", what: "Start Chat (?source=SourceRef)" },
  sources: { owner: "109", segments: "", what: "Library sources (?role=character|persona|scenario|lore|world)" },
  source: { owner: "109", segments: "SourceRef", what: "One source" },
  import: { owner: "110", segments: "", what: "Import and V1 migration" },
  providers: { owner: "112", segments: "", what: "Model providers and costs" },
  campaign: { owner: "113", segments: "campaign id", what: "One campaign's situation" },
  art: { owner: "114", segments: "", what: "Art" },
  sync: { owner: "114", segments: "", what: "Devices and sync" },
});

// Parse a location hash. Anything that is not a route path (empty, "#", a stray "#token=") is the default
// route (null). A segment that does not decode is kept as written, so a broken link still shows its ref.
export function parse(hash) {
  let text = typeof hash === "string" ? hash : "";
  if (text.startsWith("#")) text = text.slice(1);
  if (!text.startsWith("/")) return { route: null, path: [], query: new URLSearchParams() };
  const mark = text.indexOf("?");
  const pathText = mark === -1 ? text.slice(1) : text.slice(1, mark);
  const query = new URLSearchParams(mark === -1 ? "" : text.slice(mark + 1));
  const parts = pathText.split("/").filter((part) => part !== "").map(decode);
  const route = parts.length && ROUTE.test(parts[0]) ? parts[0] : null;
  if (route === null) return { route: parts.length ? "" : null, path: parts, query };
  return { route, path: parts.slice(1), query };
}

function decode(part) {
  try { return decodeURIComponent(part); } catch (error) { return part; }
}

// The hash for a route, its path segments and an optional query (an object or URLSearchParams). Throws on
// a malformed route name: a link to nowhere is a bug, not something to show.
export function href(route, segments = [], query = undefined) {
  if (typeof route !== "string" || !ROUTE.test(route)) throw new TypeError("not a route name: " + String(route));
  const path = segments.map((segment) => {
    if (typeof segment !== "string" || segment === "") throw new TypeError("a route segment is a non-empty string");
    return encodeURIComponent(segment);
  });
  let text = "#/" + [route, ...path].join("/");
  const params = query instanceof URLSearchParams ? query : new URLSearchParams();
  if (!(query instanceof URLSearchParams) && query) {
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const tail = params.toString();
  return tail ? text + "?" + tail : text;
}

// The serve's token arrives once, as "#token=..." (or as "token=..." in a route's query): returns the token and the
// hash to show without it, or null when the hash carries none.
export function takeToken(hash) {
  const text = typeof hash === "string" ? hash : "";
  if (/^#token=/.test(text)) {
    const params = new URLSearchParams(text.slice(1));
    return { token: params.get("token") || "", hash: "" };
  }
  const mark = text.indexOf("?");
  if (mark === -1) return null;
  const params = new URLSearchParams(text.slice(mark + 1));
  if (!params.has("token")) return null;
  const token = params.get("token") || "";
  params.delete("token");
  const tail = params.toString();
  return { token, hash: text.slice(0, mark) + (tail ? "?" + tail : "") };
}

// The reserved route of that name, or null (only the routes listed above: "constructor" is not one).
export function reserved(route) {
  return typeof route === "string" && Object.hasOwn(RESERVED, route) ? RESERVED[route] : null;
}

// The page that owns a route, among the registry's pages.
export function pageFor(pages, route) {
  return pages.find((page) => page.route === route) || null;
}
