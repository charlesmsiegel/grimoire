import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { CastChangePrompt } from "./CastChangePrompt";
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
      value={{ socket: socket as unknown as never, status: "open", campaignId: "c1" }}
    >
      {children}
    </CampaignStreamContext.Provider>
  );
}

const PENDING = [
  {
    id: "cc-1",
    campaign_id: "c1",
    scene_id: "s1",
    character_ref: "library:worlds/w/characters/reyes",
    change: "enter",
    is_pc: false,
    evidence: "strides in",
    confidence: 0.9,
    turn_id: "t_42",
    status: "pending",
    created_at: "2026-05-28T00:00:00+00:00",
  },
];

describe("CastChangePrompt", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // GET (list on mount) returns an array; POST (confirm/dismiss) returns ok.
    fetchSpy = vi.fn(async (_url: string, init?: RequestInit) => {
      const isPost = (init?.method ?? "GET").toUpperCase() === "POST";
      const body = isPost ? { ok: true } : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing until a turn_complete carries pending cast changes", () => {
    const socket = new FakeSocket();
    const { container } = render(
      withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" />),
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a pending change and confirms it", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" />));
    socket.emit({
      type: "turn_complete",
      turn_id: "t_42",
      pending_cast_changes: PENDING,
    } as WSMessage);

    await screen.findByText(/reyes enters the scene/i);
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url).includes("/cast-changes/cc-1/confirm")),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText(/reyes enters the scene/i)).toBeNull());
  });

  it("calls onApplied after a confirm so the scene can refresh", async () => {
    const socket = new FakeSocket();
    const onApplied = vi.fn();
    render(
      withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" onApplied={onApplied} />),
    );
    socket.emit({
      type: "turn_complete",
      turn_id: "t_42",
      pending_cast_changes: PENDING,
    } as WSMessage);

    await screen.findByText(/reyes enters the scene/i);
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
  });

  it("does not call onApplied on dismiss", async () => {
    const socket = new FakeSocket();
    const onApplied = vi.fn();
    render(
      withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" onApplied={onApplied} />),
    );
    socket.emit({
      type: "turn_complete",
      turn_id: "t_42",
      pending_cast_changes: PENDING,
    } as WSMessage);

    await screen.findByText(/reyes enters the scene/i);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() => expect(screen.queryByText(/reyes enters the scene/i)).toBeNull());
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("dismisses a pending change", async () => {
    const socket = new FakeSocket();
    render(withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" />));
    socket.emit({
      type: "turn_complete",
      turn_id: "t_42",
      pending_cast_changes: PENDING,
    } as WSMessage);

    await screen.findByText(/reyes enters the scene/i);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url).includes("/cast-changes/cc-1/dismiss")),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText(/reyes enters the scene/i)).toBeNull());
  });

  it("ignores a turn_complete aimed at a different scene", async () => {
    const socket = new FakeSocket();
    const { container } = render(
      withSocket(socket, <CastChangePrompt campaignId="c1" sceneId="s1" />),
    );
    // A turn completing in another open scene must not populate this prompt.
    socket.emit({
      type: "turn_complete",
      turn_id: "t_99",
      scene_id: "s2",
      pending_cast_changes: PENDING,
    } as WSMessage);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText(/reyes enters the scene/i)).toBeNull();

    // The same payload scoped to this scene still renders.
    socket.emit({
      type: "turn_complete",
      turn_id: "t_100",
      scene_id: "s1",
      pending_cast_changes: PENDING,
    } as WSMessage);
    await screen.findByText(/reyes enters the scene/i);
  });
});
