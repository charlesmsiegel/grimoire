// Canned `fetch` replies for the `api` client suites (`src/api/**/*.test.ts`),
// which stub `fetch` and assert on what the client sent and made of the answer.
import { fixture } from "./fixture";

export function sseResponse(chunks: string[]): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array<ArrayBuffer>>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(new TextEncoder().encode(chunks[i++]));
      else controller.close();
    },
  }, { highWaterMark: 0 });
  return fixture({ ok: true, body });
}

export function jsonOk(value: unknown): Response {
  return fixture({ ok: true, json: async () => value });
}

export function jsonBody(init: RequestInit | undefined): unknown {
  if (typeof init?.body !== "string") throw new Error("Expected a JSON request body");
  return JSON.parse(init.body) as unknown;
}

export function draftRunResponse(state: string, extra: Record<string, unknown> = {}) {
  return jsonOk({ run: { id: "r1", attempt_id: "a1", state, next_index: 0,
                         cls: "draft", kind: "tagline", ...extra } });
}
