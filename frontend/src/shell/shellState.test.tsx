import { render, screen, act, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({ api: { getShell: vi.fn() } }));

import { api } from "../api/client";
import { shellChanged } from "../appEvents";
import { useOpenCampaign } from "./useOpenCampaign";
import { useShellPayload } from "./useShellPayload";

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
});
