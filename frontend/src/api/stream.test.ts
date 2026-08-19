import { isAbortError, parseSSEChunk } from "./stream";

test("accumulates deltas and detects done", () => {
  const events: any[] = [];
  let buf = "";
  buf = parseSSEChunk(buf, 'data: {"delta": "Hel"}\n\n', (e) => events.push(e));
  buf = parseSSEChunk(buf, 'data: {"delta": "lo"}\n\ndata: {"done": true}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "Hel" }, { delta: "lo" }, { done: true }]);
  expect(buf).toBe("");
});

test("holds a partial event until its terminator arrives", () => {
  const events: any[] = [];
  let buf = "";
  buf = parseSSEChunk(buf, 'data: {"delta": "Hel', (e) => events.push(e));
  expect(events).toEqual([]);
  parseSSEChunk(buf, 'lo"}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "Hello" }]);
});

test("a heartbeat comment is traffic, not an event", () => {
  // The backend sends these through the quiet stretch before the first token
  // so proxies see the connection is alive (#95). They must not reach the
  // consumer as an event, and must not disturb a delta split around them.
  const events: any[] = [];
  let buf = "";
  buf = parseSSEChunk(buf, ": heartbeat\n\n: heartbeat\n\n", (e) => events.push(e));
  expect(events).toEqual([]);
  buf = parseSSEChunk(buf, 'data: {"delta": "At', (e) => events.push(e));
  buf = parseSSEChunk(buf, ' last."}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "At last." }]);
  expect(buf).toBe("");
});

test("isAbortError tells a cancelled turn from a broken one", () => {
  const aborted = new Error("The operation was aborted.");
  aborted.name = "AbortError";
  expect(isAbortError(aborted)).toBe(true);
  expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true);
  expect(isAbortError(new Error("NetworkError"))).toBe(false);
  expect(isAbortError({ detail: "upstream said no" })).toBe(false);
  expect(isAbortError(null)).toBe(false);
  expect(isAbortError("AbortError")).toBe(false);
});
