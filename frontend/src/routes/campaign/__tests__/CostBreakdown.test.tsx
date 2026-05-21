import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { CostBreakdown } from "../CostBreakdown";
import { observabilityApi } from "../../../api/observability";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CostBreakdown", () => {
  it("renders one row per task plus a totals footer", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.05, input_tokens: 800, output_tokens: 350, call_count: 1 },
      { task: "extraction", total_usd: 0.001, input_tokens: 400, output_tokens: 50, call_count: 1 },
    ]);

    render(<CostBreakdown turnId="t1" />);

    expect(await screen.findByText("primary")).toBeInTheDocument();
    expect(screen.getByText("extraction")).toBeInTheDocument();
    expect(screen.getByTestId("cost-total-usd")).toHaveTextContent("$0.0510");
  });

  it("renders an empty-state message when there are no rows", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<CostBreakdown turnId="t_empty" />);
    expect(await screen.findByText(/no recorded cost/i)).toBeInTheDocument();
  });

  it("surfaces the error when the fetch fails", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockRejectedValue(new Error("boom"));
    render(<CostBreakdown turnId="t_err" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
