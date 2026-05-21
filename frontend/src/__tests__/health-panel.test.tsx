import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { HealthPanel } from "../routes/observability";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("HealthPanel", () => {
  it("renders target rows and aggregates", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        "llm:anthropic": {
          level: "healthy",
          target_id: "llm:anthropic",
          message: "ok",
          checked_at: "2026-05-20T12:00:00+00:00",
          details: {},
        },
        "imagegen:sdxl": {
          level: "unhealthy",
          target_id: "imagegen:sdxl",
          message: "connection refused",
          checked_at: "2026-05-20T12:00:00+00:00",
          details: {},
        },
      }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        orchestrator: { boom: 3 },
        extractor: { schema_error: 1 },
      }),
    );

    render(<HealthPanel />);

    await waitFor(() => {
      expect(screen.getByText("llm:anthropic")).toBeInTheDocument();
    });
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Unhealthy")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
    expect(screen.getByText("schema_error")).toBeInTheDocument();
    expect(screen.getByText(/1 need attention/)).toBeInTheDocument();
  });

  it("re-probes a target on button click", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        "llm:anthropic": {
          level: "unhealthy",
          target_id: "llm:anthropic",
          message: "old",
          checked_at: "2026-05-20T12:00:00+00:00",
          details: {},
        },
      }),
    );
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    // Probe response:
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        level: "healthy",
        target_id: "llm:anthropic",
        message: "ok now",
        checked_at: "2026-05-20T12:05:00+00:00",
        details: {},
      }),
    );

    render(<HealthPanel />);
    await waitFor(() => {
      expect(screen.getByText("llm:anthropic")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /re-probe/i }));

    await waitFor(() => {
      expect(screen.getByText("ok now")).toBeInTheDocument();
    });
    expect(screen.getByText("Healthy")).toBeInTheDocument();

    const probeCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/api/observability/health/probe"),
    );
    expect(probeCall).toBeTruthy();
    expect(String(probeCall![0])).toContain("target_id=llm%3Aanthropic");
  });

  it("shows an empty-state message when no targets are registered", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    fetchMock.mockResolvedValueOnce(jsonResponse({}));

    render(<HealthPanel />);
    await waitFor(() => {
      expect(screen.getByText(/No targets registered/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/No errors recorded/i)).toBeInTheDocument();
  });
});
