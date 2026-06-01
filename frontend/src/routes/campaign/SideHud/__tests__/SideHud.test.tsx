import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { hudApi, type AggregateResult, type HudWidget } from "../../../../api/hud";
import { CampaignStreamContext } from "../../../../state/campaignStreamContext";
import { CampaignSocket, type WSListener, type WSStatusListener } from "../../../../ws/client";
import { SideHud } from "../SideHud";

function makeSocket(): CampaignSocket {
  const socket = Object.create(CampaignSocket.prototype) as CampaignSocket;
  const listeners = new Set<WSListener>();
  const statusListeners = new Set<WSStatusListener>();
  (socket as unknown as { listeners: Set<WSListener> }).listeners = listeners;
  (socket as unknown as { statusListeners: Set<WSStatusListener> }).statusListeners =
    statusListeners;
  (socket as unknown as { onMessage: (fn: WSListener) => () => boolean }).onMessage = (
    fn: WSListener,
  ) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  (socket as unknown as { onStatus: (fn: WSStatusListener) => () => boolean }).onStatus = (
    fn: WSStatusListener,
  ) => {
    statusListeners.add(fn);
    return () => statusListeners.delete(fn);
  };
  return socket;
}

const ACTIONS = {
  onUndo: vi.fn(),
  onEndScene: vi.fn(),
  onAnalyzeScene: vi.fn(),
  onDeleteScene: vi.fn(),
  onNewScene: vi.fn(),
  onOpenLedger: vi.fn(),
  onSkipTime: vi.fn(),
  onManualFact: vi.fn(),
  busy: false,
};

function renderHud(aggregate: AggregateResult, available: HudWidget[] = []) {
  vi.spyOn(hudApi, "aggregate").mockResolvedValue(aggregate);
  vi.spyOn(hudApi, "available").mockResolvedValue(available);
  const socket = makeSocket();
  return render(
    <MemoryRouter>
      <CampaignStreamContext.Provider value={{ socket, status: "open", campaignId: "test" }}>
        <SideHud
          campaignId="test"
          sceneId="test:0001"
          scene={null}
          pcs={[]}
          actions={ACTIONS}
          playerInput=""
          pcRef={null}
          latestNarratorTurnId={null}
        />
      </CampaignStreamContext.Provider>
    </MemoryRouter>,
  );
}

describe("SideHud", () => {
  it("shows loading state before data arrives", () => {
    vi.spyOn(hudApi, "aggregate").mockReturnValue(new Promise(() => {}));
    vi.spyOn(hudApi, "available").mockReturnValue(new Promise(() => {}));
    const socket = makeSocket();
    render(
      <MemoryRouter>
        <CampaignStreamContext.Provider value={{ socket, status: "open", campaignId: "test" }}>
          <SideHud
            campaignId="test"
            sceneId={null}
            scene={null}
            pcs={[]}
            actions={ACTIONS}
            playerInput=""
            pcRef={null}
            latestNarratorTurnId={null}
          />
        </CampaignStreamContext.Provider>
      </MemoryRouter>,
    );
    expect(screen.getByText(/loading hud/i)).toBeInTheDocument();
  });

  it("renders scene setting entries from row widgets", async () => {
    renderHud({
      campaign_id: "test",
      scene_id: "test:0001",
      generated_at: new Date().toISOString(),
      widgets: [
        {
          id: "core.in-game-date",
          status: "ok",
          data: { value: "May 4, 2025" },
          error: null,
          stale: false,
          title: "Date",
          render_hint: "row",
        },
        {
          id: "core.location",
          status: "ok",
          data: { value: "Dorm Room" },
          error: null,
          stale: false,
          title: "Location",
          render_hint: "row",
        },
      ],
    });
    await waitFor(() => {
      expect(screen.getByText("May 4, 2025")).toBeInTheDocument();
    });
    expect(screen.getByText("Dorm Room")).toBeInTheDocument();
    expect(screen.getByText("Date")).toBeInTheDocument();
    expect(screen.getByText("Location")).toBeInTheDocument();
  });

  it("renders cast badges as links", async () => {
    renderHud({
      campaign_id: "test",
      scene_id: "test:0001",
      generated_at: new Date().toISOString(),
      widgets: [
        {
          id: "core.present-cast",
          status: "ok",
          data: {
            chips: [
              { character_id: "kyoka", character_ref: "kyoka", name: "Kyoka Jiro", source: "library" },
              { character_id: "shia", character_ref: "emergent/shia", name: "Shia", source: "campaign-local" },
            ],
          },
          error: null,
          stale: false,
          title: "Cast",
          render_hint: "chip-list",
        },
      ],
    });
    await waitFor(() => {
      expect(screen.getByText("Kyoka Jiro")).toBeInTheDocument();
    });
    expect(screen.getByText("Shia")).toBeInTheDocument();
    const kyokaLink = screen.getByText("Kyoka Jiro").closest("a");
    expect(kyokaLink).toHaveAttribute("href", expect.stringContaining("/cast?character=kyoka"));
  });

  it("renders quick action buttons", async () => {
    renderHud({
      campaign_id: "test",
      scene_id: "test:0001",
      generated_at: new Date().toISOString(),
      widgets: [],
    });
    await waitFor(() => {
      expect(screen.getByText("Undo turn")).toBeInTheDocument();
    });
    expect(screen.getByText("End scene")).toBeInTheDocument();
  });
});
