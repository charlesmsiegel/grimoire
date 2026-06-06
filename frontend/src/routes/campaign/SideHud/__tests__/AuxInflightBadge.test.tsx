/**
 * AuxInflightBadge — verifies the header indicator:
 *   - renders nothing when /auxiliary/in-flight is empty
 *   - shows the count when results exist
 *   - refetches on aux_complete WS events
 *   - discards via auxiliaryApi.discard and refreshes
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { auxiliaryApi, type AuxiliaryResult } from "../../../../api/auxiliary";
import { CampaignStreamContext } from "../../../../state/campaignStreamContext";
import { CampaignSocket, type WSListener, type WSStatusListener } from "../../../../ws/client";
import { AuxInflightBadge } from "../AuxInflightBadge";

function makeSocket(): { socket: CampaignSocket; emit: (msg: { type: string }) => void } {
  const socket = Object.create(CampaignSocket.prototype) as CampaignSocket;
  const listeners = new Set<WSListener>();
  const statusListeners = new Set<WSStatusListener>();
  (socket as unknown as { listeners: Set<WSListener> }).listeners = listeners;
  (socket as unknown as { statusListeners: Set<WSStatusListener> }).statusListeners =
    statusListeners;
  (socket as unknown as { onMessage: (fn: WSListener) => () => boolean }).onMessage = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  (socket as unknown as { onStatus: (fn: WSStatusListener) => () => boolean }).onStatus = (fn) => {
    statusListeners.add(fn);
    return () => statusListeners.delete(fn);
  };
  const emit = (msg: { type: string }) => {
    for (const l of listeners) l(msg as never);
  };
  return { socket, emit };
}

function renderWith(socket: CampaignSocket) {
  return render(
    <CampaignStreamContext.Provider value={{ socket, status: "open", campaignId: "c1" }}>
      <AuxInflightBadge campaignId="c1" />
    </CampaignStreamContext.Provider>,
  );
}

function makeResult(id: string, kind: AuxiliaryResult["kind"] = "brainstorm"): AuxiliaryResult {
  return {
    id,
    kind,
    text: "...",
    model_used: "test",
    tokens: 1,
    pending_commit_action: "copy",
    warnings: [],
    completed_at: "2026-05-20T00:00:00Z",
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AuxInflightBadge", () => {
  it("renders nothing when there are no in-flight results", async () => {
    const spy = vi.spyOn(auxiliaryApi, "inFlight").mockResolvedValue([]);
    const { socket } = makeSocket();
    const { container } = renderWith(socket);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("renders the count when results are present", async () => {
    vi.spyOn(auxiliaryApi, "inFlight").mockResolvedValue([
      makeResult("ar_1"),
      makeResult("ar_2", "rewrite_post"),
    ]);
    const { socket } = makeSocket();
    renderWith(socket);
    await waitFor(() => expect(screen.getByLabelText(/2 auxiliary tasks/i)).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("refetches when aux_complete fires over the WS", async () => {
    const spy = vi
      .spyOn(auxiliaryApi, "inFlight")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([makeResult("ar_3")]);
    const { socket, emit } = makeSocket();
    renderWith(socket);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    await act(async () => {
      emit({ type: "aux_complete" });
    });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("1")).toBeInTheDocument();
  });

  it("expands the list on click and discards a result", async () => {
    vi.spyOn(auxiliaryApi, "inFlight")
      .mockResolvedValueOnce([makeResult("ar_9", "brainstorm")])
      .mockResolvedValueOnce([]);
    const discardSpy = vi
      .spyOn(auxiliaryApi, "discard")
      .mockResolvedValue({ discarded: true, result_id: "ar_9" });
    const { socket } = makeSocket();
    renderWith(socket);

    const pill = await screen.findByLabelText(/1 auxiliary task/i);
    fireEvent.click(pill);

    const discardBtn = await screen.findByRole("button", { name: /discard/i });
    fireEvent.click(discardBtn);

    await waitFor(() => expect(discardSpy).toHaveBeenCalledWith("c1", "ar_9"));
  });
});
