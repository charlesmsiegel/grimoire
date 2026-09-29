import { render, screen, within } from "@testing-library/react";
import CostTrend from "./CostTrend";

test("the projected series is present for a modelled-only month and marks gaps", () => {
  render(<CostTrend rows={[{ month: "2026-09", cost_usd: 0, estimated_usd: 0,
    modelled_usd: 1.25, estimated_total_usd: 1.25, unpriced_calls: 1,
    priced_calls: 0, subscription_calls: 0, modelled_calls: 1 } as never]} />);
  expect(screen.getByText("Estimated total")).toBeInTheDocument();
  expect(screen.getByText("Modelled")).toBeInTheDocument();
  expect(within(screen.getByRole("list")).getAllByText("$1.25")).toHaveLength(2);
  expect(screen.getByText(/Incomplete: 1 unpriced call/)).toBeInTheDocument();
  const line = screen.getByRole("img", { name: /cost versus month/i });
  const bars = screen.getByRole("list");
  expect(line.compareDocumentPosition(bars) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("an unpriced-only month never claims an estimated zero", () => {
  render(<CostTrend rows={[{ month: "2026-09", cost_usd: 0, estimated_usd: 0,
    modelled_usd: 0, estimated_total_usd: 0, unpriced_calls: 1,
    priced_calls: 0, subscription_calls: 0, modelled_calls: 0 } as never]} />);
  expect(screen.getByText("Incomplete")).toBeInTheDocument();
});

test("newer months are first in the chart", () => {
  const bucket = { calls: 1, cost_usd: 1, estimated_usd: 0, modelled_usd: 0,
    estimated_total_usd: 1, unpriced_calls: 0, priced_calls: 1,
    subscription_calls: 0, modelled_calls: 0 };
  render(<CostTrend rows={[{ ...bucket, month: "2026-08" },
    { ...bucket, month: "2026-09" }] as never} />);
  const months = screen.getAllByTestId("cost-trend-month").map((node) => node.textContent);
  expect(months).toEqual(["2026-09", "2026-08"]);
});

test("the line chart starts at the first measured month and keeps later empty months", () => {
  const empty = { calls: 0, cost_usd: 0, estimated_usd: 0, modelled_usd: 0,
    estimated_total_usd: 0, unpriced_calls: 0, priced_calls: 0,
    subscription_calls: 0, modelled_calls: 0 };
  render(<CostTrend rows={[{ ...empty, month: "2026-06" },
    { ...empty, month: "2026-07", calls: 1, cost_usd: 2, estimated_total_usd: 2,
      priced_calls: 1 },
    { ...empty, month: "2026-08" }] as never} />);
  expect(screen.getByRole("img", { name: /cost versus month/i }))
    .toContainElement(screen.getByTestId("cost-line-estimated_total_usd"));
  expect(screen.queryByText("2026-06")).not.toBeInTheDocument();
  expect(screen.getAllByTestId("cost-trend-month").map((node) => node.textContent))
    .toEqual(["2026-08", "2026-07"]);
});
