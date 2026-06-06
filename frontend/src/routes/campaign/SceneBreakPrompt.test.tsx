import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { SceneBreakPrompt } from "./SceneBreakPrompt";
import { CampaignStreamContext } from "../../state/campaignStreamContext";
import type { WSMessage } from "../../ws/client";

class FakeSocket {
  private listeners = new Set<(m: WSMessage) => void>();

  onMessage(listener: (m: WSMessage) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(message: WSMessage): void {
    act(() => {
      for (const listener of this.listeners) listener(message);
    });
  }
}

function withSocket(socket: FakeSocket, children: ReactNode) {
  return (
    <CampaignStreamContext.Provider
      value={{
        socket: socket as unknown as never,
        status: "open",
        campaignId: "c1",
      }}
    >
      {children}
    </CampaignStreamContext.Provider>
  );
}

describe("SceneBreakPrompt", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn(
      async () =>
        new Response(JSON.stringify({ resolved: true, turn_id: "t_42", choice: "continue" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing until a scene_break_suggested event arrives", () => {
    const socket = new FakeSocket();
    const { container } = render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    expect(container.firstChild).toBeNull();
  });

  it("opens a modal on scene_break_suggested with the confidence and reason", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    socket.emit({
      type: "scene_break_suggested",
      turn_id: "t_42",
      scene_id: "scene_1",
      confidence: 0.65,
      reason: "tonal_shift",
    });
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: /start a new scene/i })).toBeInTheDocument(),
    );
    expect(screen.getByText(/65%/)).toBeInTheDocument();
    expect(screen.getByText(/tonal shift/i)).toBeInTheDocument();
  });

  it("POSTs continue when the player picks Continue here", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    socket.emit({
      type: "scene_break_suggested",
      turn_id: "t_42",
      scene_id: "scene_1",
      confidence: 0.6,
      reason: "tonal_shift",
    });
    await waitFor(() => screen.getByRole("button", { name: /continue here/i }));
    fireEvent.click(screen.getByRole("button", { name: /continue here/i }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/campaigns/c1/turns/t_42/resolve-scene-break");
    expect(JSON.parse((init.body as string) ?? "{}")).toEqual({ choice: "continue" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("POSTs new_scene when the player picks Start a new scene", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    socket.emit({
      type: "scene_break_suggested",
      turn_id: "t_42",
      scene_id: "scene_1",
      confidence: 0.7,
      reason: "location_change",
    });
    await waitFor(() => screen.getByRole("button", { name: /start a new scene/i }));
    fireEvent.click(screen.getByRole("button", { name: /start a new scene/i }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse((init.body as string) ?? "{}")).toEqual({ choice: "new_scene" });
  });

  it("closes silently if the backend has already moved on (404)", async () => {
    fetchSpy.mockImplementationOnce(
      async () =>
        new Response(JSON.stringify({ detail: "no scene-break prompt pending" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
    );
    const socket = new FakeSocket();
    render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    socket.emit({
      type: "scene_break_suggested",
      turn_id: "t_42",
      scene_id: "scene_1",
      confidence: 0.6,
      reason: "tonal_shift",
    });
    await waitFor(() => screen.getByRole("button", { name: /continue here/i }));
    fireEvent.click(screen.getByRole("button", { name: /continue here/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes when a turn lifecycle event matches the pending turn_id", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <SceneBreakPrompt campaignId="c1" />));
    socket.emit({
      type: "scene_break_suggested",
      turn_id: "t_42",
      scene_id: "scene_1",
      confidence: 0.6,
      reason: "tonal_shift",
    });
    await waitFor(() => screen.getByRole("dialog"));

    // Backend timed out and continued — emits turn_complete with same turn_id.
    socket.emit({ type: "turn_complete", turn_id: "t_42" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
