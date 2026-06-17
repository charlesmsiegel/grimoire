import { parseSSEChunk } from "./stream";

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
  buf = parseSSEChunk(buf, 'lo"}\n\n', (e) => events.push(e));
  expect(events).toEqual([{ delta: "Hello" }]);
});
