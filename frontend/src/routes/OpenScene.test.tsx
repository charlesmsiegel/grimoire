/** The notification tap, which is the last hop of the whole feature.
 *
 *  A reply lands while the phone is locked, the shell posts a notification,
 *  and this is where tapping it arrives. It carries the scene's stable
 *  IDENTITY rather than its id because a notification can sit unread for a
 *  long time and an id moves the moment a scene is renamed.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { sceneByIdentity: vi.fn() },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string, public kind?: string) {
      super(detail);
    }
  },
}));

import { ApiError, api } from "../api/client";
import OpenScene from "./OpenScene";

function open(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/open" element={<OpenScene />} />
        <Route path="/campaigns/:cid" element={<span>campaign page</span>} />
        <Route path="/campaigns/:cid/scenes/:sid" element={<span>scene page</span>} />
        <Route path="/" element={<span>home</span>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("opening a scene from a notification", () => {
  it("resolves the identity to whatever the scene is called now", async () => {
    // The renamed case, which is the reason the intent carries an identity at
    // all: the notification was posted when the scene was `001--mara`, and by
    // the time it is tapped the scene has been renamed.
    (api.sceneByIdentity as any).mockResolvedValue({ id: "002--winifred" });
    open("/open?campaign=saltmarch&identity=" + "f".repeat(32));

    expect(await screen.findByText("scene page")).toBeInTheDocument();
    expect(api.sceneByIdentity).toHaveBeenCalledWith("saltmarch", "f".repeat(32));
  });

  it("falls back to the campaign when the scene is genuinely gone", async () => {
    // The reply is on disk either way, and a player who tapped a notification
    // wants to be somewhere useful rather than told their scene is missing.
    (api.sceneByIdentity as any).mockRejectedValue(new Error("scene_gone"));
    open("/open?campaign=saltmarch&identity=" + "f".repeat(32));

    expect(await screen.findByText("campaign page")).toBeInTheDocument();
  });

  it("goes home rather than nowhere when the link names no campaign", async () => {
    open("/open");
    await waitFor(() => expect(screen.getByText("home")).toBeInTheDocument());
    expect(api.sceneByIdentity).not.toHaveBeenCalled();
  });

  it("retries a busy lookup rather than treating it as a missing scene", async () => {
    // The identity route reports an unreadable header as a retryable `busy`
    // and a genuinely absent scene as `scene_gone`. Folding the two together
    // sent the tap to the campaign because a transient sharing violation
    // happened to land on that one read -- and the scene it was posted for was
    // sitting right there.
    (api.sceneByIdentity as any)
      .mockRejectedValueOnce(new ApiError(409, "the scene could not be read", "busy"))
      .mockResolvedValue({ id: "002--winifred" });
    open("/open?campaign=saltmarch&identity=" + "f".repeat(32));

    expect(await screen.findByText("scene page")).toBeInTheDocument();
    expect((api.sceneByIdentity as any).mock.calls).toHaveLength(2);
  });

  it("gives up on a lookup that keeps saying busy", async () => {
    // Bounded, because this is a blank screen the player is looking at. The
    // campaign is a useful place to be; a spinner that never resolves is not.
    (api.sceneByIdentity as any).mockRejectedValue(
      new ApiError(409, "the scene could not be read", "busy"));
    open("/open?campaign=saltmarch&identity=" + "f".repeat(32));

    expect(await screen.findByText("campaign page")).toBeInTheDocument();
  });
});
