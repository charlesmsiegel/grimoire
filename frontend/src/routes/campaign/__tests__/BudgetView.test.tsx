import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BudgetView } from "../BudgetView";
import * as observabilityModule from "../../../api/observability";

vi.mock("../../../api/observability", async () => {
  const actual = await vi.importActual<typeof observabilityModule>("../../../api/observability");
  return {
    ...actual,
    observabilityApi: {
      getCostConfig: vi.fn(),
      getSessionCost: vi.fn(),
      getTotalToday: vi.fn(),
      getCostRollup: vi.fn(),
    },
  };
});

function renderAt(campaignId: string) {
  return render(
    <MemoryRouter initialEntries={[`/campaigns/${campaignId}/budget`]}>
      <Routes>
        <Route path="/campaigns/:campaignId/budget" element={<BudgetView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BudgetView", () => {
  beforeEach(() => {
    vi.mocked(observabilityModule.observabilityApi.getCostConfig).mockResolvedValue({
      surface_in_status_bar: true,
      daily_budget_warn_usd: 5.0,
      daily_budget_alert_usd: 20.0,
    });
    vi.mocked(observabilityModule.observabilityApi.getSessionCost).mockResolvedValue({
      total_usd: 12.34,
      input_tokens: 1000,
      output_tokens: 500,
      call_count: 3,
    });
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 1.25,
    });
    vi.mocked(observabilityModule.observabilityApi.getCostRollup).mockResolvedValue([
      { date: "2026-05-18T00:00:00+00:00", total_usd: 2.0, call_count: 4 },
      { date: "2026-05-19T00:00:00+00:00", total_usd: 4.17, call_count: 7 },
      { date: "2026-05-20T00:00:00+00:00", total_usd: 1.25, call_count: 2 },
    ]);
  });

  it("renders thresholds and totals", async () => {
    renderAt("c1");
    expect(await screen.findByText("Campaign total")).toBeInTheDocument();
    expect(screen.getByText("$12.34")).toBeInTheDocument(); // session total
    expect(screen.getByText("$5.00")).toBeInTheDocument(); // warn threshold
    expect(screen.getByText("$20.00")).toBeInTheDocument(); // alert threshold
  });

  it("renders the rollup newest-first with a total row", async () => {
    renderAt("c1");
    const rows = await screen.findAllByRole("row");
    // 1 header + 3 data + 1 footer
    expect(rows).toHaveLength(5);
    // Newest day first
    const firstDataCells = rows[1]!.querySelectorAll("td");
    expect(firstDataCells[0]).toHaveTextContent("2026-05-20");
    expect(firstDataCells[2]).toHaveTextContent("$1.25");
    // Footer aggregates
    expect(rows[4]!).toHaveTextContent(/\$7\.42/);
  });

  it("flags today as warn when over the warn threshold", async () => {
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 6.5,
    });
    const { container } = renderAt("c1");
    await screen.findByText("Campaign total");
    const todayCell = container.querySelector('[data-warn="true"]');
    expect(todayCell).not.toBeNull();
    expect(todayCell?.textContent).toMatch(/Today/);
  });

  it("flags today as alert when over the alert threshold", async () => {
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 25.0,
    });
    const { container } = renderAt("c1");
    await screen.findByText("Campaign total");
    const alertCell = container.querySelector('[data-alert="true"]');
    expect(alertCell).not.toBeNull();
  });

  it("shows an empty message when there are no rollup records", async () => {
    vi.mocked(observabilityModule.observabilityApi.getCostRollup).mockResolvedValue([]);
    renderAt("c1");
    await waitFor(() =>
      expect(screen.getByText(/No cost records in the last 30 days/)).toBeInTheDocument(),
    );
  });
});
