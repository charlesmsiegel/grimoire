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

  it("surfaces a well-formed error frame with the backend-supplied status", async () => {
    mockImport(['event: error\ndata: {"detail":"bad input","status":400}']);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe("bad input");
  });

  it("defaults to status 500 when the error frame omits a status", async () => {
    mockImport(['event: error\ndata: {"detail":"pipeline boom"}']);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("pipeline boom");
  });

  it("rejects an empty scene id as a malformed payload", async () => {
    mockImport(['event: result\ndata: {"scene_id":""}']);

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("malformed import payload");
  });

  it("skips a well-formed JSON progress frame that is the wrong shape", async () => {
    const progress: ImportProgress[] = [];
    mockImport([
      'event: progress\ndata: {"step":"parse","current":"x","total":2,"detail":"reading"}',
      "event: progress\ndata: {}",
      'event: result\ndata: {"scene_id":"scene-7"}',
    ]);

    const id = await importSceneApi.import("camp-1", body, (p) => progress.push(p));

    expect(id).toBe("scene-7");
    expect(progress).toHaveLength(0);
  });

  it("skips progress frames with non-finite values or a non-positive total", async () => {
    const progress: ImportProgress[] = [];
    mockImport([
      'event: progress\ndata: {"step":"parse","current":1,"total":0,"detail":"reading"}',
      'event: progress\ndata: {"step":"parse","current":1e400,"total":2,"detail":"reading"}',
      'event: progress\ndata: {"step":"parse","current":-1,"total":2,"detail":"reading"}',
      'event: result\ndata: {"scene_id":"scene-8"}',
    ]);

    const id = await importSceneApi.import("camp-1", body, (p) => progress.push(p));

    expect(id).toBe("scene-8");
    expect(progress).toHaveLength(0);
  });

  it("parses a final result frame that arrives without a trailing blank line", async () => {
    // Construct the body directly so no trailing "\n\n" delimiter is appended.
    globalThis.fetch = vi.fn(
      async () => new Response('event: result\ndata: {"scene_id":"scene-eof"}', { status: 200 }),
    ) as unknown as typeof fetch;

    const id = await importSceneApi.import("camp-1", body, () => {});

    expect(id).toBe("scene-eof");
  });

  it("surfaces a mid-frame truncated result at EOF as a typed error", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response('event: result\ndata: {"scene_id":"scen', { status: 200 }),
    ) as unknown as typeof fetch;

    const err = await importSceneApi.import("camp-1", body, () => {}).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("malformed import payload");
  });
});
