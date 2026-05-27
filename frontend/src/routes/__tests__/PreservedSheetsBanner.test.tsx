import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { campaignApi } from "../../api/campaign";
import { PreservedSheetsBanner } from "../CampaignView";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PreservedSheetsBanner", () => {
  it("does not fetch preserved sheets when mechanicsModule is null", async () => {
    const spy = vi.spyOn(campaignApi, "preservedSheets");
    render(
      <MemoryRouter>
        <PreservedSheetsBanner campaignId="c1" mechanicsModule={null} />
      </MemoryRouter>,
    );
    // give any errant effect a tick to fire
    await new Promise((r) => setTimeout(r, 0));
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not fetch while mechanicsModule is undefined (still loading)", async () => {
    const spy = vi.spyOn(campaignApi, "preservedSheets");
    render(
      <MemoryRouter>
        <PreservedSheetsBanner campaignId="c1" mechanicsModule={undefined} />
      </MemoryRouter>,
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(spy).not.toHaveBeenCalled();
  });

  it("fetches and renders banner when mechanicsModule is set and orphans exist", async () => {
    vi.spyOn(campaignApi, "preservedSheets").mockResolvedValue({
      active: "wod-m20",
      preserved: [{ mechanics_id: "another-campaign", count: 2 }],
    });
    render(
      <MemoryRouter>
        <PreservedSheetsBanner campaignId="c1" mechanicsModule="wod-m20" />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        /another-campaign preserved from a previous mechanics binding/,
      );
    });
  });
});
