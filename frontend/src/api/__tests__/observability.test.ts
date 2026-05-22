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
});
