import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as observabilityModule from "../../../api/observability";
import { PerformanceTab } from "../PerformanceTab";

describe("PerformanceTab", () => {
  const getMetricsKnown = vi.spyOn(observabilityModule.observabilityApi, "getMetricsKnown");
  const getMetricsSummary = vi.spyOn(observabilityModule.observabilityApi, "getMetricsSummary");
  const getMetricsTrend = vi.spyOn(observabilityModule.observabilityApi, "getMetricsTrend");

  beforeEach(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    getMetricsKnown.mockResolvedValue([
      { module: "orchestrator", operation: "turn", last_recorded_at: "2026-05-20T00:00:00Z" },
    ]);
    getMetricsSummary.mockResolvedValue({
      count: 12,
      successes: 11,
      failures: 1,
      p50_ms: 100,
      p95_ms: 200,
      p99_ms: 250,
      max_ms: 300,
    });
    getMetricsTrend.mockResolvedValue([
      {
        bucket_start: "2026-05-20T00:00:00Z",
        count: 1,
        successes: 1,
        failures: 0,
        p50_ms: 100,
        p95_ms: 100,
        p99_ms: 100,
      },
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders a row for each known (module, operation) pair", async () => {
    render(<PerformanceTab />);
    expect(await screen.findByText(/orchestrator/i)).toBeInTheDocument();
    expect(screen.getByText(/turn/i)).toBeInTheDocument();
  });

  it("displays summary counts and percentiles", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    expect(await screen.findByText(/count\s*12/i)).toBeInTheDocument();
    expect(screen.getByText(/p50\s*100ms/i)).toBeInTheDocument();
    expect(screen.getByText(/p95\s*200ms/i)).toBeInTheDocument();
  });

  it("re-fetches summary and trend when the bucket selector changes", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    getMetricsTrend.mockClear();
    const bucketSelect = screen.getByLabelText(/bucket/i);
    await act(async () => {
      fireEvent.change(bucketSelect, { target: { value: "hour" } });
    });
    expect(getMetricsTrend).toHaveBeenCalledWith(
      "orchestrator",
      "turn",
      "hour",
      expect.any(Number),
    );
  });

  it("shows an empty-state banner when /metrics/known errors", async () => {
    getMetricsKnown.mockRejectedValue(new Error("boom"));
    render(<PerformanceTab />);
    expect(await screen.findByText(/metrics unavailable/i)).toBeInTheDocument();
  });

  it("shows a failure count when summaries report failures", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    expect(await screen.findByText(/1 failed/i)).toBeInTheDocument();
  });
});
