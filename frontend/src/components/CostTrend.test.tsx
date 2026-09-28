import { render, screen } from "@testing-library/react";
import CostTrend from "./CostTrend";

test("the projected series is present for a modelled-only month and marks gaps", () => {
  render(<CostTrend rows={[{ month: "2026-09", cost_usd: 0, estimated_usd: 0,
    modelled_usd: 1.25, estimated_total_usd: 1.25, unpriced_calls: 1,
    priced_calls: 0, subscription_calls: 0, modelled_calls: 1 } as never]} />);
  expect(screen.getByText("Estimated total")).toBeInTheDocument();
  expect(screen.getByText("Modelled")).toBeInTheDocument();
  expect(screen.getAllByText("$1.25")).toHaveLength(2);
  expect(screen.getByText(/Incomplete: 1 unpriced call/)).toBeInTheDocument();
});

test("an unpriced-only month never claims an estimated zero", () => {
  render(<CostTrend rows={[{ month: "2026-09", cost_usd: 0, estimated_usd: 0,
    modelled_usd: 0, estimated_total_usd: 0, unpriced_calls: 1,
    priced_calls: 0, subscription_calls: 0, modelled_calls: 0 } as never]} />);
  expect(screen.getByText("Incomplete")).toBeInTheDocument();
});
