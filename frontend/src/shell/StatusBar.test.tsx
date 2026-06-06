import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";

import { StatusBar } from "./StatusBar";
import { StoreProvider } from "../state/store";
import { ThemeProvider } from "../state/theme";
import { useStore } from "../state/useStore";
import * as observabilityModule from "../api/observability";

vi.mock("../api/observability", async () => {
  const actual = await vi.importActual<typeof observabilityModule>("../api/observability");
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

function SeedCampaign({ children }: { children: ReactNode }) {
  const { dispatch } = useStore();
  useEffect(() => {
    dispatch({ type: "set-campaigns", campaigns: [{ id: "c1", name: "Ironhold" }] });
    dispatch({ type: "set-active-campaign", id: "c1" });
  }, [dispatch]);
  return <>{children}</>;
}

function renderStatusBar() {
  return render(
    <ThemeProvider>
      <StoreProvider>
        <SeedCampaign>
          <StatusBar wsStatus="open" />
        </SeedCampaign>
      </StoreProvider>
    </ThemeProvider>,
  );
}

describe("StatusBar cost item", () => {
  beforeEach(() => {
    vi.mocked(observabilityModule.observabilityApi.getCostConfig).mockResolvedValue({
      surface_in_status_bar: true,
      daily_budget_warn_usd: 5.0,
      daily_budget_alert_usd: 20.0,
    });
    vi.mocked(observabilityModule.observabilityApi.getSessionCost).mockResolvedValue({
      total_usd: 3.21,
      input_tokens: 0,
      output_tokens: 0,
      call_count: 1,
    });
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 1.0,
    });
  });

  it("renders the session cost when a campaign is active", async () => {
    renderStatusBar();
    await waitFor(() => expect(screen.getByText(/cost: \$3\.21/)).toBeInTheDocument());
  });

  it("flags warn severity when today's spend crosses the warn threshold", async () => {
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 6.0,
    });
    const { container } = renderStatusBar();
    await waitFor(() => expect(screen.getByText(/cost: \$3\.21/)).toBeInTheDocument());
    const item = container.querySelector('.status-item[data-warn="true"]');
    expect(item?.textContent).toMatch(/cost:/);
  });

  it("flags alert severity when today's spend crosses the alert threshold", async () => {
    vi.mocked(observabilityModule.observabilityApi.getTotalToday).mockResolvedValue({
      total_usd: 25.0,
    });
    const { container } = renderStatusBar();
    await waitFor(() => expect(screen.getByText(/cost: \$3\.21/)).toBeInTheDocument());
    const item = container.querySelector('.status-item[data-alert="true"]');
    expect(item?.textContent).toMatch(/cost:/);
  });

  it("hides the cost item when surface_in_status_bar is false", async () => {
    vi.mocked(observabilityModule.observabilityApi.getCostConfig).mockResolvedValue({
      surface_in_status_bar: false,
      daily_budget_warn_usd: 5.0,
      daily_budget_alert_usd: 20.0,
    });
    renderStatusBar();
    // The cost item renders while the config is still loading (costConfig is
    // null → showCost defaults true), so assert that it ends up *removed* once
    // getCostConfig resolves with surface=false. A bare post-fetch assertion
    // races the two independent fetches; poll until the item is gone instead.
    await waitFor(() =>
      expect(observabilityModule.observabilityApi.getCostConfig).toHaveBeenCalled(),
    );
    await waitFor(() => expect(screen.queryByText(/cost:/)).not.toBeInTheDocument());
  });
});
