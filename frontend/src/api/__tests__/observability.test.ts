import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { observabilityApi } from "../observability";

describe("observabilityApi", () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");

  beforeEach(() => {
    fetchSpy.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  function mockJsonResponse(body: unknown) {
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }

  function lastFetchUrl(): string {
    const call = fetchSpy.mock.calls[0];
    if (!call) throw new Error("fetch was not called");
    return String(call[0]);
  }

  it("getMetricsKnown calls /api/observability/metrics/known", async () => {
    mockJsonResponse([
      { module: "orchestrator", operation: "turn", last_recorded_at: "x" },
    ]);
    const result = await observabilityApi.getMetricsKnown();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String(fetchSpy.mock.calls[0]![0]);
    expect(url).toContain("/api/observability/metrics/known");
    expect(result).toEqual([
      { module: "orchestrator", operation: "turn", last_recorded_at: "x" },
    ]);
  });

  it("getMetricsSummary forwards module + operation + windowSeconds", async () => {
    mockJsonResponse({
      count: 1,
      successes: 1,
      failures: 0,
      p50_ms: 10,
      p95_ms: 10,
      p99_ms: 10,
      max_ms: 10,
    });
    await observabilityApi.getMetricsSummary("orchestrator", "turn", 3600);
    const url = String(fetchSpy.mock.calls[0]![0]);
    expect(url).toContain("/api/observability/metrics/summary");
    expect(url).toContain("module=orchestrator");
    expect(url).toContain("operation=turn");
    expect(url).toContain("window_seconds=3600");
  });

  it("getMetricsTrend forwards bucket and window_seconds", async () => {
    mockJsonResponse([]);
    await observabilityApi.getMetricsTrend("llm_gateway", "complete", "minute", 600);
    const url = String(fetchSpy.mock.calls[0]![0]);
    expect(url).toContain("bucket=minute");
    expect(url).toContain("window_seconds=600");
  });

  it("throws when response is not ok", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("boom", { status: 500, headers: { "Content-Type": "text/plain" } }),
    );
    await expect(observabilityApi.getMetricsKnown()).rejects.toThrow();
  });

  it("listTurns hits /api/observability/turns with campaign_id and limit", async () => {
    mockJsonResponse([]);
    await observabilityApi.listTurns("camp-1", 25);
    const url = lastFetchUrl();
    expect(url).toContain("/api/observability/turns");
    expect(url).toContain("campaign_id=camp-1");
    expect(url).toContain("limit=25");
  });

  it("listTurns defaults limit to 50", async () => {
    mockJsonResponse([]);
    await observabilityApi.listTurns("camp-1");
    expect(lastFetchUrl()).toContain("limit=50");
  });

  it("getTurnPrompt hits /api/observability/turns/{id}/prompt and url-encodes the id", async () => {
    mockJsonResponse({
      messages: [],
      sources: [],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });
    await observabilityApi.getTurnPrompt("turn id/with slash");
    expect(lastFetchUrl()).toContain(
      "/api/observability/turns/turn%20id%2Fwith%20slash/prompt",
    );
  });

  it("getTurnPrompt parses sources with inclusion_reasons", async () => {
    mockJsonResponse({
      messages: [],
      sources: [
        {
          source_id: "src_abc",
          owner_id: "library:world1",
          kind: "character",
          scope: "library",
          tier: "spotlight",
          library_version: 3,
          override_applied: false,
          tokens: 120,
          summary: "alice",
          inclusion_reasons: ["present_in_scene", "mentioned_in_recent_posts"],
        },
      ],
      budget_used: { spotlight: 120 },
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });
    const result = await observabilityApi.getTurnPrompt("t1");
    expect(result.sources).toHaveLength(1);
    const first = result.sources[0];
    if (!first) throw new Error("expected one source");
    expect(first.kind).toBe("character");
    expect(first.inclusion_reasons).toEqual([
      "present_in_scene",
      "mentioned_in_recent_posts",
    ]);
  });
});
