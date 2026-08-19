// Vite's `?raw` — the file as text, resolved the same way the real import
// is, so the guard below cannot end up reading some other copy.
import errorsSource from "./errors?raw";
import { ApiError } from "./client";
import { errorText, isOffline } from "./errors";

// These two are what every error banner in the app is made of, so they are
// tested apart from the client that mostly produces their input: `errors.ts`
// imports nothing, which is the whole reason it exists (see its docstring).
// `errorText` replaced `err.detail ?? String(err)` at 34 call sites across the
// two actor editors, so what it returns IS the error message the user reads.
test("errorText reads an ApiError's detail", () => {
  expect(errorText(new ApiError(404, "character not found"))).toBe("character not found");
});

test("errorText reads a bare {detail} too, not only an ApiError", () => {
  // Stream error frames and hand-built rejections arrive as plain objects, and
  // an `instanceof` test alone would render every one as "[object Object]".
  expect(errorText({ detail: "could not fetch from chub.ai" }))
    .toBe("could not fetch from chub.ai");
});

test("errorText falls back for anything with no usable detail", () => {
  expect(errorText(new Error("boom"))).toBe("Error: boom");
  expect(errorText("just a string")).toBe("just a string");
  expect(errorText({ code: 7 })).toBe("[object Object]");
  expect(errorText({ detail: 42 })).toBe("[object Object]");   // not a string
});

test("errorText survives a null rejection instead of throwing inside the catch", () => {
  // `err.detail` on null is a TypeError raised inside the `catch` that was
  // meant to be handling the failure -- the one place a throw has nowhere left
  // to go. This is a deliberate departure from the `??` it replaced.
  expect(errorText(null)).toBe("null");
  expect(errorText(undefined)).toBe("undefined");
});

test("errorText treats an empty detail as no message at all", () => {
  // Also deliberate, and also because `??` only guards null/undefined: a
  // backend answering {"detail": ""} used to produce an error banner with
  // nothing written in it, from an ApiError and a plain object alike.
  expect(errorText(new ApiError(500, ""))).toBe("Error");
  expect(errorText({ detail: "" })).toBe("[object Object]");
});

// --- isOffline (#210) ---

test("a rejection tagged network is offline; the same one untagged is not", () => {
  expect(isOffline(new ApiError(502, "connection reset", "network"))).toBe(true);
  expect(isOffline(new ApiError(502, "connection reset"))).toBe(false);
});

test("a stream error frame carries its kind as a plain object", () => {
  // Never an ApiError: the frame is parsed out of the SSE body and handed to
  // the caller as-is, which is why `isOffline` reads structurally.
  expect(isOffline({ detail: "connection reset", kind: "network" })).toBe(true);
});

test("missing_key is NOT offline — an unconfigured key is a different fix", () => {
  expect(isOffline({ detail: "No LLM connection selected", kind: "missing_key" })).toBe(false);
  // nor is any other kind the taxonomy carries
  for (const kind of ["auth", "rate_limit", "bad_response", "timeout", "busy", "localize"]) {
    expect(isOffline({ detail: "x", kind })).toBe(false);
  }
});

test("a rejection that is not an object at all does not throw", () => {
  expect(isOffline("boom")).toBe(false);
  expect(isOffline(null)).toBe(false);
  expect(isOffline(undefined)).toBe(false);
});

test("errors.ts imports nothing, which is the only reason it works", () => {
  // Not a style rule. These helpers were first written next to `ApiError` in
  // `client.ts`, and five suites went red at once: the components that render
  // an error are tested against a `vi.mock("../api/client")` that replaces
  // that module wholesale, so the helper is undefined by the time the
  // component asks for it. A single import here re-opens that door -- most
  // likely `import { ApiError }` to "tidy" the structural reads below into an
  // `instanceof`, which would also break the SSE frames, which are plain
  // objects. The file's own docstring says so; this is what holds it.
  expect(errorsSource).not.toMatch(/^\s*import\b/m);
  expect(errorsSource).not.toMatch(/\brequire\s*\(/);
});
