import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import GlobalCostsView from "./GlobalCostsView";

vi.mock("../api/client", () => ({ api: {
  getMonthlyCosts: vi.fn(),
  listCampaigns: vi.fn().mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }]),
} }));
import { api } from "../api/client";

const zero = { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
  total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
  cost_usd: 0, estimated_usd: 0, modelled_usd: 0, estimated_total_usd: 0,
  priced_calls: 0, unpriced_calls: 0, subscription_calls: 0,
  modelled_calls: 0, unmetered_calls: 0, duration_ms: 0 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getMonthlyCosts).mockResolvedValue({
    month: "2026-09", since: "2026-09-01", until: "2026-09-30",
    available_months: ["2026-08", "2026-09"],
    totals: { ...zero, calls: 3, cost_usd: 0.5, modelled_usd: 1.25,
      estimated_total_usd: 1.75, unpriced_calls: 1 },
    campaigns: [{ ...zero, calls: 2, modelled_usd: 1.25, estimated_total_usd: 1.25,
      unpriced_calls: 1, campaign_id: "saltmarch", campaign_name: "Saltmarch" }],
    unassigned: { ...zero, calls: 1, cost_usd: 0.5, estimated_total_usd: 0.5 },
    trend: [{ ...zero, month: "2026-09", calls: 3, cost_usd: 0.5, modelled_usd: 1.25,
      estimated_total_usd: 1.75, unpriced_calls: 1 }],
  });
});

test("global Costs shows campaign rows and modelled activity in its projected trend", async () => {
  render(<MemoryRouter initialEntries={["/costs?month=2026-09"]}><Routes>
    <Route path="/costs" element={<GlobalCostsView />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Saltmarch" })).toHaveAttribute(
    "href", "/campaigns/saltmarch/costs?month=2026-09");
  expect(screen.getByText("Outside a campaign")).toBeInTheDocument();
  expect(screen.getByText(/2026-09: 2026-09-01 through 2026-09-30 UTC/)).toBeInTheDocument();
  expect(screen.getAllByText(/Incomplete: 1 unpriced call/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText("$1.25").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
  await waitFor(() => expect(api.getMonthlyCosts).toHaveBeenCalledWith("2026-08"));
});
