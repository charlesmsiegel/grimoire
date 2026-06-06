import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../client";
import { importSceneApi, type ImportProgress } from "./importScene";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

// Build a streaming Response whose body emits the given SSE event frames.
function sseResponse(frames: string[]): Response {
  return new Response(frames.map((f) => `${f}\n\n`).join(""), { status: 200 });
}

function mockImport(frames: string[]): void {
  globalThis.fetch = vi.fn(async () => sseResponse(frames)) as unknown as typeof fetch;
}

const body = { path: "/tmp/scene.md", title: "Scene" };

describe("importSceneApi.import", () => {
  it("streams progress and returns the scene id from the result frame", async () => {
    const progress: ImportProgress[] = [];
    mockImport([
      'event: progress\ndata: {"step":"parse","current":1,"total":2,"detail":"reading"}',
      'event: result\ndata: {"scene_id":"scene-123"}',
    ]);

    const id = await importSceneApi.import("camp-1", body, (p) => progress.push(p));

    expect(id).toBe("scene-123");
    expect(progress).toHaveLength(1);
    expect(progress[0]?.step).toBe("parse");
  });

  it("skips a single malformed progress frame without aborting the import", async () => {
    const progress: ImportProgress[] = [];
    mockImport([
      "event: progress\ndata: {not valid json",
      'event: result\ndata: {"scene_id":"scene-9"}',
    ]);

    const id = await importSceneApi.import("camp-1", body, (p) => progress.push(p));

    expect(id).toBe("scene-9");
    expect(progress).toHaveLength(0);
  });

  it("throws a typed ApiError when the result payload is malformed", async () => {
    mockImport(["event: result\ndata: {truncated"]);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("malformed import payload");
  });

  it("throws a typed ApiError when the error payload is malformed", async () => {
    mockImport(["event: error\ndata: <html>500</html>"]);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("malformed import payload");
  });

  it("surfaces a well-formed error frame as its declared ApiError", async () => {
    mockImport(['event: error\ndata: {"detail":"pipeline boom","status":422}']);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe("pipeline boom");
  });
});
