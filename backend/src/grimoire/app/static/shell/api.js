// The shell's client for the app server (044, 108): every page calls ops through this, never fetch itself.
// An op is POST /api/<op> with a JSON object; a refusal comes back as an ApiError carrying the server's own
// reason (ApiRefused), so a page can tell "changed" (a stale write) from "no_chat" from a failed request.

const TOKEN_KEY = "grimoire-token";
let memoryToken = "";

export function setToken(value) {
  memoryToken = value;
  try { sessionStorage.setItem(TOKEN_KEY, value); } catch (error) { /* kept in memory for this page */ }
}

export function token() {
  try { return sessionStorage.getItem(TOKEN_KEY) || memoryToken; } catch (error) { return memoryToken; }
}

export class ApiError extends Error {
  // reason: the server's machine-readable reason ("changed", "no_chat", "unknown_op"...), or "network" when the
  // request never got an answer and "bad_response" when the answer was not JSON. status: the HTTP status (0: none).
  constructor(reason, message, status) {
    super(message || reason);
    this.name = "ApiError";
    this.reason = reason;
    this.status = status;
  }
}

export async function call(op, args) {
  let response;
  try {
    response = await fetch("/api/" + encodeURIComponent(op), {
      method: "POST", headers: { "Content-Type": "application/json", "X-Grimoire-Token": token() },
      body: JSON.stringify(args || {}), cache: "no-store", credentials: "omit",
    });
  } catch (error) {
    throw new ApiError("network", "the app did not answer: is it still running?", 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok || body === null) {
    if (body && typeof body.error === "string") throw new ApiError(body.error, body.message, response.status);
    throw new ApiError("bad_response", "the app sent no answer", response.status);
  }
  return body;
}

// A picture by its ImageRef, as a blob: URL (the page's policy allows no other image source). Cached per page.
const pictures = new Map();
export async function picture(ref) {
  if (pictures.has(ref)) return pictures.get(ref);
  const response = await fetch("/api/image?ref=" + encodeURIComponent(ref), {
    headers: { "X-Grimoire-Token": token() }, cache: "no-store", credentials: "omit",
  }).catch(() => null);
  if (!response || !response.ok) return null;
  const url = URL.createObjectURL(await response.blob());
  pictures.set(ref, url);
  return url;
}

// The page registry (GET /shell/pages): code metadata, so it needs no token.
export async function registry() {
  const response = await fetch("/shell/pages", { cache: "no-store", credentials: "omit" });
  if (!response.ok) throw new ApiError("bad_response", "the app could not list its pages", response.status);
  return response.json();
}
