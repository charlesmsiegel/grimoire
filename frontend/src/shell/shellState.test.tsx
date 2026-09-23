import { StrictMode } from "react";
import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

vi.mock("../api/client", () => ({ api: { getShell: vi.fn() } }));

import { api } from "../api/client";
import { shellChanged } from "../appEvents";
import { useOpenCampaign } from "./useOpenCampaign";
import { useShellPayload } from "./useShellPayload";
import { ShellPayloadProvider, useCampaignShell } from "./ShellPayloadContext";

const EMPTY = { campaigns: 0, campaign: null, todo: null };
const withCampaign = (id: string) => ({
  campaigns: 1, todo: null,
  campaign: {
    id, name: "A Run", world_name: "Saltmarch", scenes: 1, open: [],
    ledger_open: 0, sheets: null, unreviewed: null, pending: [],
    images_undescribed: null,
  },
});

beforeEach(() => {
  localStorage.clear();
  (api.getShell as any).mockReset().mockResolvedValue(EMPTY);
});
// Storage is global to the jsdom instance, so a test that left a key behind
// would decide the next one's answer.
afterEach(() => localStorage.clear());

/** The two hooks wired the way `App` wires them, with the store root as a prop
 *  so a test can repoint it the way Configuration does. */
function Harness({ dataDir }: { dataDir: string }) {
  const { cid, reconcile } = useOpenCampaign(dataDir);
  const shell = useShellPayload(dataDir, cid, reconcile);
  return (
    <div>
      <span data-testid="cid">{cid ?? "-"}</span>
      <span data-testid="status">{shell.status}</span>
      <span data-testid="name">{shell.payload?.campaign?.name ?? "-"}</span>
    </div>
  );
}

function renderAt(path: string, dataDir = "/store/a") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="*" element={<Harness dataDir={dataDir} />} /></Routes>
    </MemoryRouter>);
}

const cid = () => screen.getByTestId("cid").textContent;

describe("which campaign is open", () => {
  test("a campaign route sets it, and leaving does not close it", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const { rerender } = renderAt("/campaigns/c1");
    await waitFor(() => expect(cid()).toBe("c1"));

    // Standing on Configuration must not empty the rail's second tier: seeing
    // what is waiting in a campaign is the reason the tier is there.
    rerender(
      <MemoryRouter initialEntries={["/config"]}>
        <Routes><Route path="*" element={<Harness dataDir="/store/a" />} /></Routes>
      </MemoryRouter>);
    await waitFor(() => expect(cid()).toBe("c1"));
  });

  test("the campaign wizard is not a campaign", async () => {
    // `/campaigns/new` matches `/campaigns/:cid` as a pattern, so without the
    // rail-less exclusion, starting and abandoning the wizard would leave the
    // literal "new" remembered — and the next successful read would then clear
    // it as unknown, losing the campaign that was actually open.
    renderAt("/campaigns/new");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(cid()).toBe("-");
  });

  test("it survives a remount", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const first = renderAt("/campaigns/c1");
    await waitFor(() => expect(cid()).toBe("c1"));
    first.unmount();

    renderAt("/config");
    await waitFor(() => expect(cid()).toBe("c1"));
  });

  test("a different store root gets its own answer", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const first = renderAt("/campaigns/c1", "/store/a");
    await waitFor(() => expect(cid()).toBe("c1"));
    first.unmount();

    // Repointing at another library must not inherit this one's campaign — the
    // id would name nothing there, or worse, something else.
    (api.getShell as any).mockResolvedValue(EMPTY);
    renderAt("/config", "/store/b");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(cid()).toBe("-");
  });

  test("a successful read that resolves nothing clears it", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const first = renderAt("/campaigns/c1");
    await waitFor(() => expect(cid()).toBe("c1"));
    first.unmount();

    // The campaign was deleted. The server says so by answering 200 with a null
    // campaign, and the memory goes with it.
    (api.getShell as any).mockResolvedValue(EMPTY);
    renderAt("/config");
    await waitFor(() => expect(cid()).toBe("-"));
  });

  test("a FAILED read does not clear it", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const first = renderAt("/campaigns/c1");
    await waitFor(() => expect(cid()).toBe("c1"));
    first.unmount();

    // A dropped connection is not a deleted campaign. Treating them alike is
    // how valid state gets erased — the rail would lose its second tier every
    // time the server hiccuped.
    (api.getShell as any).mockRejectedValue(new Error("offline"));
    renderAt("/config");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("failed"));
    expect(cid()).toBe("c1");
  });
});

describe("the payload's own state", () => {
  test("a failure keeps the last good payload for the same key", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    renderAt("/campaigns/c1");
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("A Run"));

    (api.getShell as any).mockRejectedValue(new Error("offline"));
    act(() => { shellChanged(); });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("failed"));
    // Stale but usable beats blank: the rail's first job is navigation.
    expect(screen.getByTestId("name")).toHaveTextContent("A Run");
  });

  test("a payload from the previous store root is dropped, not rendered", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    const { rerender } = renderAt("/config", "/store/a");
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("A Run"));

    // Repoint, and make the new library's read hang. The chrome must not go on
    // showing the previous library's campaign in the meantime — this is the
    // concrete bug the (data_dir, cid) key exists to prevent.
    (api.getShell as any).mockReturnValue(new Promise(() => {}));
    rerender(
      <MemoryRouter initialEntries={["/config"]}>
        <Routes><Route path="*" element={<Harness dataDir="/store/b" />} /></Routes>
      </MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("loading"));
    expect(screen.getByTestId("name")).toHaveTextContent("-");
  });

  test("a mutation refetches without a navigation", async () => {
    // Ending a scene, writing the ledger or creating a sheet changes what the
    // rail says and moves no URL — so navigation alone would leave the count
    // stale on screen indefinitely.
    renderAt("/config");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    const before = (api.getShell as any).mock.calls.length;
    act(() => { shellChanged(); });
    await waitFor(() =>
      expect((api.getShell as any).mock.calls.length).toBeGreaterThan(before));
  });

  test("a mutation during a read does not adopt the read it interrupted", async () => {
    // The read in flight was issued before the write, so its answer is the one
    // the notification is trying to replace. Sharing it would be exactly the
    // stale adoption `fresh` exists to stop in the api client.
    let settleFirst: (v: unknown) => void = () => {};
    (api.getShell as any)
      .mockReturnValueOnce(new Promise((r) => { settleFirst = r; }))
      .mockResolvedValue(withCampaign("c1"));
    renderAt("/campaigns/c1");
    await waitFor(() => expect(api.getShell).toHaveBeenCalledTimes(1));
    act(() => { shellChanged(); });
    await waitFor(() => expect(api.getShell).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("A Run"));
    // The pre-write answer lands last and is dropped; the post-write one stands.
    await act(async () => { settleFirst(EMPTY); });
    expect(screen.getByTestId("name")).toHaveTextContent("A Run");
  });

  test("the status a render reports is about the campaign that render asks for", async () => {
    // A failure for the campaign being left is not a failure for the one being
    // entered. Between the navigation and the new read starting there is a
    // render in which the id has moved and the status has not, and a page that
    // trusted it would say "could not be read" about a read nobody has made.
    // Every render is recorded, because the render that matters is one no
    // settled `await` can land on.
    const seen: string[] = [];
    function Recorder() {
      const { cid: asked, reconcile } = useOpenCampaign("/store/a");
      const shell = useShellPayload("/store/a", asked, reconcile);
      seen.push(`${asked}:${shell.status}`);
      const navigate = useNavigate();
      return <button onClick={() => navigate("/campaigns/c2")}>to-c2</button>;
    }
    (api.getShell as any).mockRejectedValue(new Error("offline"));
    render(<MemoryRouter initialEntries={["/campaigns/c1"]}><Recorder /></MemoryRouter>);
    await waitFor(() => expect(seen).toContain("c1:failed"));
    (api.getShell as any).mockReturnValue(new Promise(() => {}));
    fireEvent.click(screen.getByText("to-c2"));
    await waitFor(() => expect(seen).toContain("c2:loading"));
    expect(seen).not.toContain("c2:failed");
  });
});

/** The provider wired the way `App.Shell` wires it, with a page under it that
 *  draws from the rail's read the way the hub and the scenes list do. */
function ProvidedHarness({ dataDir }: { dataDir: string }) {
  const { cid, reconcile } = useOpenCampaign(dataDir);
  const shell = useShellPayload(dataDir, cid, reconcile);
  return (
    <ShellPayloadProvider value={{ ...shell, cid }}>
      <Routes>
        <Route path="/campaigns/:cid" element={<Page />} />
        <Route path="*" element={<Jump />} />
      </Routes>
    </ShellPayloadProvider>
  );
}

function Page() {
  const shell = useCampaignShell("c1");
  return (
    <div>
      <span data-testid="page-name">{shell.payload?.campaign?.name ?? "-"}</span>
      <span data-testid="page-failed">{shell.failed ? "failed" : "-"}</span>
    </div>
  );
}

/** Moves the router without remounting the provider above it. */
function Jump() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/campaigns/c1")}>jump</button>;
}

function renderProvided(path: string, dataDir = "/store/a") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ProvidedHarness dataDir={dataDir} />
    </MemoryRouter>);
}

describe("one read per arrival", () => {
  // `GET /api/shell` is the most expensive read the chrome makes, and it scales
  // with the library. Two of them in flight convoy on the server, so each of
  // these used to cost the page a second read's worth of waiting.

  test("a cold load on a campaign route asks about that campaign, once", async () => {
    // A different campaign remembered for this root is exactly the stale id
    // the first read used to be issued with, before the route's own id landed.
    localStorage.setItem("grimoire.openCampaign:/store/a", "c-old");
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    renderAt("/campaigns/c1/scenes/s1");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(api.getShell).toHaveBeenCalledTimes(1);
    expect(api.getShell).toHaveBeenCalledWith("c1");
  });

  test("...and a browser that remembers nothing does the same", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    renderAt("/campaigns/c1/scenes/s1");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(api.getShell).toHaveBeenCalledTimes(1);
    expect(api.getShell).toHaveBeenCalledWith("c1");
  });

  test("a page that asks for a read on arrival shares the one the rail is making", async () => {
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    renderProvided("/campaigns/c1");
    await waitFor(() => expect(screen.getByTestId("page-name")).toHaveTextContent("A Run"));
    expect(api.getShell).toHaveBeenCalledTimes(1);
  });

  test("StrictMode's rehearsed mount is not a second arrival", async () => {
    // Dev builds mount every effect twice. A read per effect run would double
    // the most expensive request in the chrome on every cold load of the build
    // the app is developed against.
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/campaigns/c1"]}>
          <ProvidedHarness dataDir="/store/a" />
        </MemoryRouter>
      </StrictMode>);
    await waitFor(() => expect(screen.getByTestId("page-name")).toHaveTextContent("A Run"));
    expect(api.getShell).toHaveBeenCalledTimes(1);
  });

  test("arriving at a page of the campaign already open still reads it afresh", async () => {
    // The rail does not re-read on a navigation that keeps its campaign, and a
    // turn played since its last read moved the money and the turn counts. The
    // page's arrival is what asks, so it never draws only what the rail heard
    // before the reader went off to play.
    (api.getShell as any).mockResolvedValue(withCampaign("c1"));
    renderProvided("/campaigns/c1/ledger");
    await waitFor(() => expect(api.getShell).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText("jump"));
    await waitFor(() => expect(screen.getByTestId("page-name")).toHaveTextContent("A Run"));
    expect(api.getShell).toHaveBeenCalledTimes(2);
  });

  test("a page never draws a payload about another campaign", async () => {
    // The rail's read can be about a different campaign than the page beside
    // it -- the one the server resolved instead, or the one the rail
    // remembered before the route's own id reached it.
    (api.getShell as any).mockResolvedValue(withCampaign("c2"));
    renderProvided("/campaigns/c1");
    await waitFor(() => expect(api.getShell).toHaveBeenCalled());
    expect(screen.getByTestId("page-name")).toHaveTextContent("-");
  });

  test("a failed read is reported to the page asking about that campaign", async () => {
    (api.getShell as any).mockRejectedValue(new Error("offline"));
    renderProvided("/campaigns/c1");
    await waitFor(() => expect(screen.getByTestId("page-failed")).toHaveTextContent("failed"));
    expect(screen.getByTestId("page-name")).toHaveTextContent("-");
  });
});
