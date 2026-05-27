/**
 * useHud is the engine of the SideHud — covered here are:
 *   - initial aggregate fetch + widget descriptors hydration
 *   - per-widget refresh when a matching WS event fires
 *   - error surface when the aggregate endpoint fails
 *
 * The WS connection itself is provided via a stub ``CampaignSocket`` in the
 * stream context, since the hook doesn't talk to ``fetch`` for events —
 * only the REST layer is mocked.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

import { hudApi, type AggregateResult, type HudWidget } from "../../../../api/hud";
import { CampaignStreamContext } from "../../../../state/campaignStreamContext";
import {
  CampaignSocket,
  type WSListener,
  type WSStatusListener,
} from "../../../../ws/client";
import { useHud } from "../useHud";

function makeSocket(): { socket: CampaignSocket; emit: (msg: { type: string }) => void } {
  // We don't actually open a WebSocket — just use the class's listener
  // plumbing so useCampaignEvent can subscribe and we can fire messages.
  // The real connect() is never called.
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
  const emit = (msg: { type: string }) => {
    for (const l of listeners) l(msg as never);
  };
  return { socket, emit };
}

const widgetDescriptor = (id: string, refreshOn: string[]): HudWidget => ({
  id,
  title: id.replace("core.", ""),
  scope: "campaign",
  visible_when: null,
  render_hint: "row",
  read: { endpoint: `/${id}`, poll_interval_s: null },
  edit: null,
  refresh_on: refreshOn,
  stale_threshold_s: null,
  owner_module: null,
});

const aggregate = (): AggregateResult => ({
  campaign_id: "c1",
  scene_id: "s1",
  generated_at: "2026-05-19T14:00:00Z",
  widgets: [
    { id: "core.in-game-date", status: "ok", data: { date: "1894-10-13" }, error: null, stale: false, title: "Date", render_hint: "row" },
    { id: "core.weather", status: "ok", data: { conditions: "Rain" }, error: null, stale: false, title: "Weather", render_hint: "row" },
  ],
});

function Probe({ campaignId }: { campaignId: string }) {
  const hud = useHud(campaignId);
  return (
    <div>
      <p data-testid="status">{hud.loading ? "loading" : hud.error ? `error:${hud.error}` : "ready"}</p>
      <ul>
        {hud.widgets.map((w) => (
          <li key={w.snapshot.id} data-testid={`widget-${w.snapshot.id}`}>
            {String((w.snapshot.data as Record<string, unknown> | null)?.["date"] ??
              (w.snapshot.data as Record<string, unknown> | null)?.["conditions"] ??
              "")}
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderWithStream(socket: CampaignSocket) {
  return render(
    <CampaignStreamContext.Provider value={{ socket, status: "open", campaignId: "c1" }}>
      <Probe campaignId="c1" />
    </CampaignStreamContext.Provider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useHud", () => {
  it("fetches the aggregate and hydrates widget state", async () => {
    vi.spyOn(hudApi, "aggregate").mockResolvedValue(aggregate());
    vi.spyOn(hudApi, "available").mockResolvedValue([
      widgetDescriptor("core.in-game-date", ["time_advanced"]),
      widgetDescriptor("core.weather", ["weather_changed", "time_advanced"]),
    ]);
    const { socket } = makeSocket();

    renderWithStream(socket);

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(screen.getByTestId("widget-core.in-game-date")).toHaveTextContent("1894-10-13");
    expect(screen.getByTestId("widget-core.weather")).toHaveTextContent("Rain");
  });

  it("refreshes only the widgets whose refresh_on matches the WS event", async () => {
    vi.spyOn(hudApi, "aggregate").mockResolvedValue(aggregate());
    vi.spyOn(hudApi, "available").mockResolvedValue([
      widgetDescriptor("core.in-game-date", ["time_advanced"]),
      widgetDescriptor("core.weather", ["weather_changed"]),
    ]);
    const widgetSpy = vi
      .spyOn(hudApi, "widget")
      .mockResolvedValue({
        id: "core.weather",
        status: "ok",
        data: { conditions: "Snow" },
        error: null,
        stale: false,
        title: "Weather",
        render_hint: "row",
      });

    const { socket, emit } = makeSocket();
    renderWithStream(socket);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));

    await act(async () => {
      emit({ type: "weather_changed" });
    });

    await waitFor(() =>
      expect(screen.getByTestId("widget-core.weather")).toHaveTextContent("Snow"),
    );
    expect(widgetSpy).toHaveBeenCalledWith("c1", "core.weather", undefined, undefined);
    expect(widgetSpy).not.toHaveBeenCalledWith("c1", "core.in-game-date", undefined, undefined);
  });

  it("surfaces an error when the aggregate fetch fails", async () => {
    vi.spyOn(hudApi, "aggregate").mockRejectedValue(new Error("boom"));
    vi.spyOn(hudApi, "available").mockResolvedValue([]);
    const { socket } = makeSocket();
    renderWithStream(socket);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("error:boom"));
  });
});
