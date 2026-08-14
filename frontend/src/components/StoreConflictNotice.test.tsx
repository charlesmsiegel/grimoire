import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StoreConflictNotice } from "./StoreConflictNotice";

vi.mock("../api/client", () => ({
  // Same shape as the real one; the notice tells an ApiError's `detail` from a
  // bare throw. See EntityEditor.test.tsx on why the class lives in here.
  ApiError: class extends Error {
    constructor(public status: number, public detail: string) { super(detail); }
  },
  api: { getStoreConflicts: vi.fn() },
}));
import { ApiError, api } from "../api/client";

const conflict = (path: string, tool = "syncthing") =>
  ({ path, name: path.split("/").pop()!, tool, kind: "file" as const, size: 42,
     modified: "2026-01-01T00:00:00Z" });

beforeEach(() => {
  vi.clearAllMocks();
  (api.getStoreConflicts as any).mockResolvedValue({ conflicts: [], truncated: false });
});

test("a clean library says so, rather than rendering nothing", async () => {
  render(<StoreConflictNotice />);
  expect(await screen.findByText(/no conflicted copies/i)).toBeInTheDocument();
});

test("each conflict is listed with its path and the tool that made it", async () => {
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [conflict("worlds/realm/lore/pact.sync-conflict-1.md"),
                conflict("campaigns/saltmarch/locations/quay.md.orig", "merge")],
    truncated: false,
  });
  render(<StoreConflictNotice />);
  expect(await screen.findByText("worlds/realm/lore/pact.sync-conflict-1.md")).toBeInTheDocument();
  expect(screen.getByText("campaigns/saltmarch/locations/quay.md.orig")).toBeInTheDocument();
  expect(screen.getByText(/2 conflicted copies/i)).toBeInTheDocument();
  expect(screen.getByText(/merge/)).toBeInTheDocument();
});

test("nothing is offered that would change a conflicted file", async () => {
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [conflict("worlds/realm/lore/pact.sync-conflict-1.md")], truncated: false,
  });
  render(<StoreConflictNotice />);
  await screen.findByText("worlds/realm/lore/pact.sync-conflict-1.md");
  // Which side of a conflict to keep is the user's call; the panel only reports.
  for (const b of screen.getAllByRole("button")) {
    expect(b).toHaveTextContent(/scan again/i);
  }
});

test("a truncated scan says the list is not the whole story", async () => {
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [conflict("worlds/realm/lore/pact.sync-conflict-1.md")], truncated: true,
  });
  render(<StoreConflictNotice />);
  expect(await screen.findByText(/the list stops here/i)).toBeInTheDocument();
});

test("a scan that failed reports the failure instead of a clean bill", async () => {
  (api.getStoreConflicts as any).mockRejectedValue(
    new (ApiError as any)(500, "could not scan the store: denied"));
  render(<StoreConflictNotice />);
  expect(await screen.findByText(/could not scan the store: denied/)).toBeInTheDocument();
  expect(screen.queryByText(/no conflicted copies/i)).toBeNull();
});

test("Scan again re-reads the store", async () => {
  render(<StoreConflictNotice />);
  await screen.findByText(/no conflicted copies/i);
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [conflict("worlds/realm/lore/pact.sync-conflict-1.md")], truncated: false,
  });
  fireEvent.click(screen.getByRole("button", { name: /scan again/i }));
  await waitFor(() => expect(api.getStoreConflicts).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("worlds/realm/lore/pact.sync-conflict-1.md")).toBeInTheDocument();
});

test("a truncated count is reported as a floor, not a total", async () => {
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [conflict("worlds/realm/lore/pact.sync-conflict-1.md")], truncated: true,
  });
  render(<StoreConflictNotice />);
  expect(await screen.findByText(/at least 1 conflicted copy/i)).toBeInTheDocument();
});

test("a conflicted folder reads as a folder, not as zero bytes", async () => {
  (api.getStoreConflicts as any).mockResolvedValue({
    conflicts: [{ path: "worlds/Realm (conflicted copy 2026-01-01)",
                  name: "Realm (conflicted copy 2026-01-01)", tool: "dropbox",
                  kind: "directory", size: null, modified: "2026-01-01T00:00:00Z" }],
    truncated: false,
  });
  render(<StoreConflictNotice />);
  expect(await screen.findByText("worlds/Realm (conflicted copy 2026-01-01)")).toBeInTheDocument();
  expect(screen.getByText(/folder/)).toBeInTheDocument();
  expect(screen.queryByText(/bytes/)).toBeNull();
});
