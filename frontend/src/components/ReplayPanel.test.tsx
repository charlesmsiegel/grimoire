import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ReplayPanel } from "./ReplayPanel";
import { RunRegistryProvider, useRunRegistry, type RunRegistry }
  from "../runs/RunRegistryProvider";

vi.mock("../api/client", () => ({
  api: {
    getReplay: vi.fn(), replayPreview: vi.fn(), startReplay: vi.fn(), replayTurn: vi.fn(),
    acceptReplay: vi.fn(), cancelReplay: vi.fn(), forkCampaign: vi.fn(), regenerate: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string) { super(detail); }
  },
}));
import { api } from "../api/client";

const SESSION = {
  scene: "s1", cut: 3, done: 0, steps: 4, turns_left: 2,
  next: "generation" as const, staged: false, created: "", gone: false,
  pending: false,
};
/** The same session once a replayed reply is in the transcript, unaccepted —
 *  what the server reports after a turn has run. */
const PENDING = { ...SESSION, pending: true };
const PREVIEW = { posts: 3, turns: 2, threshold: 10, fork: false, blocked: "" };

beforeEach(() => {
  vi.clearAllMocks();
  (api.getReplay as any).mockResolvedValue(null);
  (api.replayPreview as any).mockResolvedValue(PREVIEW);
  (api.startReplay as any).mockResolvedValue({ cut: 3, cascade: {} });
  (api.replayTurn as any).mockResolvedValue(undefined);
  (api.acceptReplay as any).mockResolvedValue(SESSION);
  (api.cancelReplay as any).mockResolvedValue({ scene: "s1", restored: 3, dropped: 0 });
  (api.regenerate as any).mockResolvedValue(undefined);
});

const noop = () => {};

async function renderPanel(props: Partial<Parameters<typeof ReplayPanel>[0]> = {}) {
  const all = {
    cid: "c1", sid: "s1", startAt: null, onStartHandled: noop, onChanged: noop,
    onForked: noop, ...props,
  };
  // Routed: a turn the model could not be reached for links to Connections
  // (#210), and a `Link` outside a router throws.
  render(<MemoryRouter><ReplayPanel {...all} /></MemoryRouter>);
  await act(async () => {});
}

test("renders nothing when no replay is running and none was asked for", async () => {
  const { container } = render(
    <ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                 onChanged={noop} onForked={noop} />);
  await act(async () => {});
  expect(container.querySelector(".replay-panel")).toBeNull();
});

test("prices a replay before anything is cut", async () => {
  await renderPanel({ startAt: 3 });
  expect(api.replayPreview).toHaveBeenCalledWith("c1", "s1", 3);
  expect(screen.getByText("Replay 2 turns")).toBeTruthy();
  expect(api.startReplay).not.toHaveBeenCalled();
});

test("starting the replay cuts at the asked-for post and refreshes the caller", async () => {
  const onChanged = vi.fn();
  await renderPanel({ startAt: 3, onChanged });
  fireEvent.click(screen.getByText("Replay in place"));
  await waitFor(() => expect(api.startReplay).toHaveBeenCalledWith("c1", "s1", 3));
  expect(onChanged).toHaveBeenCalled();
});

test("a blocked span says why instead of offering the button", async () => {
  (api.replayPreview as any).mockResolvedValue({ ...PREVIEW, blocked: "this scene moves" });
  await renderPanel({ startAt: 3 });
  expect(screen.getByText("this scene moves")).toBeTruthy();
  expect(screen.queryByText("Replay in place")).toBeNull();
});

test("a long replay nudges a fork first (#80)", async () => {
  (api.replayPreview as any).mockResolvedValue({ ...PREVIEW, turns: 14, fork: true, threshold: 10 });
  await renderPanel({ startAt: 3 });
  expect(screen.getByText(/more than 10 turns/)).toBeTruthy();
  // Both are still offered: the nudge is a recommendation, not a gate.
  expect(screen.getByText("Replay in place")).toBeTruthy();
  expect(screen.getByText("Fork first…")).toBeTruthy();
});

test("forking hands the new campaign back rather than replaying in it", async () => {
  (api.forkCampaign as any).mockResolvedValue({ id: "c2", name: "Run (retcon)" });
  const onForked = vi.fn();
  vi.spyOn(window, "prompt").mockReturnValue("Run (retcon)");
  await renderPanel({ startAt: 3, onForked });
  fireEvent.click(screen.getByText("Fork first…"));
  await waitFor(() => expect(onForked).toHaveBeenCalledWith("c2"));
  expect(api.startReplay).not.toHaveBeenCalled();
});

test("shows the walk's position while a replay is running", async () => {
  (api.getReplay as any).mockResolvedValue(SESSION);
  await renderPanel();
  expect(screen.getByText("Replaying — 2 turns left")).toBeTruthy();
  expect(screen.getByText("Replay next turn")).toBeTruthy();
  expect(screen.queryByText("Accept")).toBeNull();
});

test("accept and try again appear only once a turn has been run", async () => {
  (api.getReplay as any).mockResolvedValue(SESSION);
  await renderPanel();
  // The server is what says a reply is waiting, so that is what the turn moves.
  (api.replayTurn as any).mockImplementation(async () => {
    (api.getReplay as any).mockResolvedValue(PENDING);
  });
  fireEvent.click(screen.getByText("Replay next turn"));
  await waitFor(() => expect(screen.getByText("Accept")).toBeTruthy());
  expect(api.replayTurn).toHaveBeenCalled();

  fireEvent.click(screen.getByText("Try again"));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());

  (api.acceptReplay as any).mockImplementation(async () => {
    (api.getReplay as any).mockResolvedValue(SESSION);
    return SESSION;
  });
  fireEvent.click(screen.getByText("Accept"));
  await waitFor(() => expect(api.acceptReplay).toHaveBeenCalledWith("c1", "s1"));
  await waitFor(() => expect(screen.getByText("Replay next turn")).toBeTruthy());
});

test("a reply left waiting is still waiting after a reload", async () => {
  // The regression this pins: the verdict used to be local state, so a reload
  // offered "Replay next turn" for a turn already run — and the second
  // generation landed beside the first.
  (api.getReplay as any).mockResolvedValue(PENDING);
  await renderPanel();
  expect(screen.getByText("Accept")).toBeTruthy();
  expect(screen.getByText("Try again")).toBeTruthy();
  expect(screen.queryByText("Replay next turn")).toBeNull();
});

test("a turn holds the transcript's write latch for the whole request", async () => {
  // A replayed turn is a generation into the scene on screen; while one runs,
  // the composer, the gutter and Retry must not offer to start a second.
  (api.getReplay as any).mockResolvedValue(SESSION);
  let held = 0;
  const latch = () => { held += 1; return () => { held -= 1; }; };
  let land: () => void = () => {};
  (api.replayTurn as any).mockReturnValue(new Promise<void>((res) => { land = res; }));
  render(<ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                      onChanged={noop} onForked={noop} latch={latch} />);
  await act(async () => {});

  fireEvent.click(screen.getByText("Replay next turn"));
  await waitFor(() => expect(held).toBe(1));
  await act(async () => { land(); });
  await waitFor(() => expect(held).toBe(0));
});

test("a turn that fails mid-stream is reported, not counted as run", async () => {
  // `streamPost` resolves normally on an error frame — that is how a failed
  // generation reports itself — so ignoring frames reads failure as success and
  // offers Accept for a reply that was never written.
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockImplementation(async (_c: string, _s: string, on: any) => {
    on({ error: { detail: "the model refused", kind: "provider" } });
  });
  await renderPanel();
  fireEvent.click(screen.getByText("Replay next turn"));
  await waitFor(() => expect(screen.getByText("the model refused")).toBeTruthy());
  expect(screen.queryByText("Accept")).toBeNull();
});

test("a replay turn the model could not be reached for offers the recovery", async () => {
  // A replay re-runs model turns, so it goes dark offline exactly like the
  // composer does -- and used to say so with a bare socket error (#210).
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockImplementation(async (_c: string, _s: string, on: any) => {
    on({ error: { detail: "connection refused", kind: "network" } });
  });
  await renderPanel();
  fireEvent.click(screen.getByText("Replay next turn"));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
});

test("stopping asks whether to put the rest of the scene back", async () => {
  (api.getReplay as any).mockResolvedValue(SESSION);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await renderPanel();
  fireEvent.click(screen.getByText("Stop"));
  await waitFor(() => expect(api.cancelReplay).toHaveBeenCalledWith("c1", "s1", true));
});

test("declining the restore still stops the replay, keeping what was replayed", async () => {
  (api.getReplay as any).mockResolvedValue(SESSION);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  await renderPanel();
  fireEvent.click(screen.getByText("Stop"));
  await waitFor(() => expect(api.cancelReplay).toHaveBeenCalledWith("c1", "s1", false));
});

test("a session whose scene is gone offers only to discard it", async () => {
  (api.getReplay as any).mockResolvedValue({ ...SESSION, gone: true });
  await renderPanel();
  expect(screen.getByText(/scene this replay belongs to is gone/)).toBeTruthy();
  expect(screen.queryByText("Replay next turn")).toBeNull();
  fireEvent.click(screen.getByText("Discard"));
  await waitFor(() => expect(api.cancelReplay).toHaveBeenCalledWith("c1", "s1", false));
});

test("a refused step is reported in the panel rather than thrown away", async () => {
  const { ApiError } = await import("../api/client");
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockRejectedValue(new ApiError(409, "this replay has no model turn left"));
  await renderPanel();
  fireEvent.click(screen.getByText("Replay next turn"));
  await waitFor(() => expect(screen.getByText("this replay has no model turn left")).toBeTruthy());
});

test("the price is not re-fetched when the caller re-renders", async () => {
  const { rerender } = render(
    <ReplayPanel cid="c1" sid="s1" startAt={3} onStartHandled={() => {}}
                 onChanged={() => {}} onForked={() => {}} />);
  await act(async () => {});
  expect(api.replayPreview).toHaveBeenCalledTimes(1);
  // A new inline callback every render is what the transcript actually passes,
  // and it must not re-price the post.
  rerender(<ReplayPanel cid="c1" sid="s1" startAt={3} onStartHandled={() => {}}
                        onChanged={() => {}} onForked={() => {}} />);
  await act(async () => {});
  expect(api.replayPreview).toHaveBeenCalledTimes(1);
});

test("a price taken for one post is not shown for another", async () => {
  const { rerender } = render(
    <ReplayPanel cid="c1" sid="s1" startAt={3} onStartHandled={noop}
                 onChanged={noop} onForked={noop} />);
  await act(async () => {});
  expect(screen.getByText("Replay 2 turns")).toBeTruthy();

  (api.replayPreview as any).mockReturnValue(new Promise(() => {}));   // still in flight
  rerender(<ReplayPanel cid="c1" sid="s1" startAt={9} onStartHandled={noop}
                        onChanged={noop} onForked={noop} />);
  await act(async () => {});
  expect(screen.queryByText("Replay 2 turns")).toBeNull();
});

test("the confirm and prompt do not run while the transcript is latched", async () => {
  // Both are modal and synchronous: asked inside the guard, the write latch —
  // which greys out the composer and the gutter for the whole app — would be
  // held for as long as the reader looks at the dialog.
  (api.getReplay as any).mockResolvedValue(SESSION);
  let heldWhenAsked: number | null = null;
  let held = 0;
  const latch = () => { held += 1; return () => { held -= 1; }; };
  vi.spyOn(window, "confirm").mockImplementation(() => { heldWhenAsked = held; return true; });
  render(<ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                      onChanged={noop} onForked={noop} latch={latch} />);
  await act(async () => {});

  fireEvent.click(screen.getByText("Stop"));
  await waitFor(() => expect(api.cancelReplay).toHaveBeenCalled());
  expect(heldWhenAsked).toBe(0);
  expect(held).toBe(0);
});

test("a request that outlives the panel tells nobody its transcript moved", async () => {
  // The reader switched scene mid-turn. `onChanged` refreshes the scene THIS
  // panel was for, which by then is not the one they are looking at.
  (api.getReplay as any).mockResolvedValue(SESSION);
  const onChanged = vi.fn();
  let land: () => void = () => {};
  (api.replayTurn as any).mockReturnValue(new Promise<void>((res) => { land = res; }));
  const { unmount } = render(
    <ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                 onChanged={onChanged} onForked={noop} />);
  await act(async () => {});

  fireEvent.click(screen.getByText("Replay next turn"));
  await waitFor(() => expect(api.replayTurn).toHaveBeenCalled());
  unmount();
  await act(async () => { land(); });
  expect(onChanged).not.toHaveBeenCalled();
});

test("a replayed turn is recorded in the run registry before it is sent", async () => {
  // `/replay/turn` and `/regenerate` are detached server-side, so a WebView
  // suspended mid-walk leaves the backend generating while `guard` releases
  // this panel's latch: nothing can cancel that run, nothing reattaches when
  // the tab comes back, `refresh()` is never reached, and the panel shows
  // replay state that is stale until a manual reload (codex, P2).
  //
  // Registered BEFORE the request, like every other producer: the window worth
  // recovering from is the one where the server accepted the work and no frame
  // ever came back.
  const seen: { attempt: string | null } = { attempt: null };
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockImplementation(
    async (_c: string, _s: string, _on: unknown, _sig: unknown, attempt: string) => {
      // Asked DURING the request, which is the whole point: a record written
      // after the stream returns is written after the window it is for.
      seen.attempt = registry.pending("c1", "s1")?.attempt ?? null;
      expect(attempt).toBe(seen.attempt);
    });

  let registry!: RunRegistry;
  function Probe() { registry = useRunRegistry(); return null; }
  render(
    <RunRegistryProvider>
      <Probe />
      <MemoryRouter>
        <ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                     onChanged={noop} onForked={noop} />
      </MemoryRouter>
    </RunRegistryProvider>);
  await act(async () => {});
  fireEvent.click(screen.getByText("Replay next turn"));

  await waitFor(() => expect(api.replayTurn).toHaveBeenCalled());
  expect(seen.attempt, "no attempt was recorded before the request").not.toBeNull();
});

test("a replay stream that ends with no answer hands the run to recovery", async () => {
  // `guard` releases this panel's latch and busy state the moment the request
  // settles -- but a stream that ended with neither `done` nor `error` leaves a
  // run that may still be generating and still holding the scene. Replay,
  // Accept and the composer all came back, and every one of them was then
  // refused, until some later mount or `visibilitychange` noticed.
  //
  // The parent already knows how to adopt such a run. This is telling it now.
  const unanswered = vi.fn();
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockImplementation(
    async (_c: string, _s: string, on: any) => {
      on({ run: { id: "r-1", attempt_id: "a", state: "running", next_index: 1 } });
      on({ delta: "The lamps " });        // and then the socket simply stops
    });

  render(
    <RunRegistryProvider>
      <MemoryRouter>
        <ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                     onChanged={noop} onForked={noop} onUnanswered={unanswered} />
      </MemoryRouter>
    </RunRegistryProvider>);
  await act(async () => {});
  fireEvent.click(screen.getByText("Replay next turn"));

  await waitFor(() => expect(unanswered).toHaveBeenCalled());
});

test("a replay stream that finished properly does not", async () => {
  // The counterweight: an answered stream is resolved, and asking the parent to
  // adopt it would have the recovery pass re-litigate a turn that is over.
  const unanswered = vi.fn();
  (api.getReplay as any).mockResolvedValue(SESSION);
  (api.replayTurn as any).mockImplementation(
    async (_c: string, _s: string, on: any) => {
      on({ run: { id: "r-1", attempt_id: "a", state: "running", next_index: 1 } });
      on({ done: true });
    });

  render(
    <RunRegistryProvider>
      <MemoryRouter>
        <ReplayPanel cid="c1" sid="s1" startAt={null} onStartHandled={noop}
                     onChanged={noop} onForked={noop} onUnanswered={unanswered} />
      </MemoryRouter>
    </RunRegistryProvider>);
  await act(async () => {});
  fireEvent.click(screen.getByText("Replay next turn"));

  await waitFor(() => expect(api.replayTurn).toHaveBeenCalled());
  await act(async () => {});
  expect(unanswered).not.toHaveBeenCalled();
});

test("a reroll tells the caller its follow-ups are already scheduled", async () => {
  // `Try again` goes through `/regenerate`, an ordinary turn producer, which
  // schedules the rolling-summary fold and the scene-break question itself
  // (#397). A caller that asked again would put a second scene-break question
  // at the provider — that route has no in-flight coalescing, so both are
  // billed and the later answer is discarded as superseded.
  //
  // Every other action here still reports `asked` falsy: `/replay/turn`
  // deliberately schedules nothing, and a cut, an accept or a cancel is no turn
  // at all.
  (api.getReplay as any).mockResolvedValue(PENDING);
  const onChanged = vi.fn();
  await renderPanel({ onChanged });

  fireEvent.click(screen.getByText("Try again"));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalledWith(true);

  onChanged.mockClear();
  (api.acceptReplay as any).mockResolvedValue(SESSION);
  fireEvent.click(screen.getByText("Accept"));
  await waitFor(() => expect(api.acceptReplay).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalled();
  expect(onChanged.mock.calls.every(([asked]: any[]) => !asked)).toBe(true);
});
