import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ImageStoreCard } from "./ImageStoreCard";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string, public kind?: string) {
      super(detail);
    }
  },
  api: {
    listGlobalRuns: vi.fn(), getGlobalRun: vi.fn(), cancelGlobalRun: vi.fn(),
    startImageMigration: vi.fn(), startImageGc: vi.fn(),
    getImageMaintenanceReport: vi.fn(),
  },
}));
import { ApiError, api } from "../api/client";

const run = (over: Record<string, unknown> = {}) => ({
  id: "r".repeat(32), attempt_id: "a1", state: "landed", next_index: 0,
  cls: "maintenance", kind: "migrate", ...over,
});

const migration = (over: Record<string, unknown> = {}) => ({
  kind: "image-migration", dry_run: true, outcome: "done", error: null,
  legacy_files: 12, unique_images: 7, exact_duplicates: 4, pixel_variants: 1,
  bytes_before: 5_242_880, bytes_after: 2_097_152, bytes_reclaimed: 3_145_728,
  description_conflicts: 0, placed: 0, already_placed: 0, legacy_deleted: 0,
  untouched: [{ path: "worlds/realm/characters/mara/avatar.bmp", reason: "unsupported" }],
  skipped: [], errors: [], format1_collections: [], collections_converted: [],
  collections_kept: [], ...over,
});

// Mid-day, so the local date is the same date in every timezone a CI box can have.
const DUE = Date.UTC(2026, 10, 14, 12) / 1000;
const DUE_TEXT = new Date(DUE * 1000).toLocaleDateString();

const scan = (over: Record<string, unknown> = {}) => ({
  kind: "image-gc", mode: "scan", run_id: "g".repeat(32), state: "complete",
  grace_days: 30,
  counts: { placements: 20, objects: 9, blobs: 9, unreferenced: 5, collectable: 3,
            collectable_blobs: 0, protected: 2 },
  collectable: [{ id: "a", blob: "x", bytes: 1024 }, { id: "b", blob: "y", bytes: 2048 },
                { id: "c", blob: "z", bytes: 4096 }],
  collectable_blobs: [],
  protected: [{ id: "d", blob: "w", why: "first-sighting", collectable_at: DUE },
              { id: "e", blob: "v", why: "grace", collectable_at: DUE }],
  reclaimable_bytes: 7168, blocking: [], unreadable_sidecars: [], clock_skew: [],
  token: "tok-123", token_expires_at: Date.now() / 1000 + 3600,
  deleted: { objects: [], blobs: [], bytes: 0 }, skipped: [], kept_blobs: [],
  error: null, ...over,
});

const confirm = vi.fn(() => true);
beforeEach(() => {
  vi.clearAllMocks();
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run() });
  (api.cancelGlobalRun as any).mockResolvedValue({ run: run({ state: "cancelled" }) });
  (api.startImageMigration as any).mockResolvedValue({ run: run() });
  (api.startImageGc as any).mockResolvedValue({ run: run({ kind: "gc" }) });
  (api.getImageMaintenanceReport as any).mockResolvedValue(migration());
  confirm.mockReset();
  confirm.mockReturnValue(true);
  vi.stubGlobal("confirm", confirm);
});
afterEach(() => { vi.unstubAllGlobals(); });

test("check shows the dry-run report", async () => {
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Check" }));

  expect(await screen.findByText(/12 legacy files/)).toBeInTheDocument();
  // A dry run: asked for as one, and no confirm is needed to look.
  expect(api.startImageMigration).toHaveBeenCalledWith(true, expect.any(String));
  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByText(/7 unique images/)).toBeInTheDocument();
  expect(screen.getByText(/3\.0 MB/)).toBeInTheDocument();
  // What it would leave alone is named, with the reason.
  expect(screen.getByText(/worlds\/realm\/characters\/mara\/avatar\.bmp/)).toBeInTheDocument();
  expect(screen.getByText(/unsupported/)).toBeInTheDocument();
  expect(api.getImageMaintenanceReport).toHaveBeenCalledWith("r".repeat(32));
});

test("migrate asks to confirm first", async () => {
  (confirm as any).mockReturnValueOnce(false);
  render(<ImageStoreCard />);
  const migrate = await screen.findByRole("button", { name: "Migrate" });

  fireEvent.click(migrate);
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(api.startImageMigration).not.toHaveBeenCalled();

  fireEvent.click(migrate);
  await waitFor(() => expect(api.startImageMigration).toHaveBeenCalledTimes(1));
  expect(api.startImageMigration).toHaveBeenCalledWith(false, expect.any(String));
  expect(await screen.findByText(/12 legacy files/)).toBeInTheDocument();
});

test("find unused images shows when objects become collectable", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/3 unused images can be deleted now/)).toBeInTheDocument();
  expect(api.startImageGc).toHaveBeenCalledWith({ dry_run: true }, expect.any(String));
  // The two that are not yet collectable, and the day they become so.
  expect(screen.getByText(new RegExp(`2 more .*${DUE_TEXT.replace(/[/.]/g, "\\$&")}`)))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete 3 unused images" })).toBeEnabled();
});

test("nothing collectable offers no delete", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    collectable: [], counts: { ...scan().counts, collectable: 0 },
    reclaimable_bytes: 0, token: null, token_expires_at: null,
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/No unused images can be deleted yet/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
});

test("delete carries the token", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));
  const del = await screen.findByRole("button", { name: "Delete 3 unused images" });

  (confirm as any).mockReturnValueOnce(false);
  fireEvent.click(del);
  expect(api.startImageGc).toHaveBeenCalledTimes(1);          // declined: nothing sent

  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    mode: "collect", token: null, token_expires_at: null,
    deleted: { objects: ["a", "b", "c"], blobs: ["x", "y", "z"], bytes: 7168 },
  }));
  fireEvent.click(del);
  await waitFor(() => expect(api.startImageGc).toHaveBeenCalledTimes(2));
  expect(api.startImageGc).toHaveBeenLastCalledWith(
    { dry_run: false, token: "tok-123" }, expect.any(String));
  expect(await screen.findByText(/Deleted 3 unused images/)).toBeInTheDocument();
  // The token was spent, so the button that carried it is gone.
  expect(screen.queryByRole("button", { name: /^Delete \d+ unused/ })).toBeNull();
});

test("a live run is rediscovered on mount", async () => {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "o".repeat(32), state: "landed", kind: "gc" }),
    run({ id: "l".repeat(32), state: "running", kind: "migrate" }),
  ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), state: "running" }) });
  render(<ImageStoreCard />);

  expect(await screen.findByText(/Migrating legacy images/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  // Nothing can be started over it.
  expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Find unused images" })).toBeDisabled();
});

test("a rediscovered run that finishes shows its report", async () => {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "l".repeat(32), state: "running", kind: "gc" }) ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), kind: "gc" }) });
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);

  expect(await screen.findByText(/3 unused images can be deleted now/)).toBeInTheDocument();
  expect(api.getImageMaintenanceReport).toHaveBeenCalledWith("l".repeat(32));
});

test("cancel calls the global cancel route", async () => {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "l".repeat(32), state: "running", kind: "migrate" }) ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), state: "running" }) });
  (api.getImageMaintenanceReport as any).mockResolvedValue(
    migration({ dry_run: false, outcome: "cancelled", placed: 2 }));
  render(<ImageStoreCard />);

  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(api.cancelGlobalRun).toHaveBeenCalledWith("l".repeat(32)));
  expect(await screen.findByText(/Cancelled/)).toBeInTheDocument();
  // The partial report is shown, and the controls are back.
  expect(screen.getByRole("button", { name: "Check" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});

test("blocking paths and maintenance elsewhere are shown", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    state: "blocked", token: null, token_expires_at: null, collectable: [],
    counts: { ...scan().counts, collectable: 0 }, protected: [],
    blocking: [{ path: "worlds/realm/image-refs/avatar.json", reason: "unparseable" },
               { path: ".world-staging/saltmarch", reason: "staging-too-young" }],
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/worlds\/realm\/image-refs\/avatar\.json/)).toBeInTheDocument();
  expect(screen.getByText(/unparseable/)).toBeInTheDocument();
  expect(screen.getByText(/\.world-staging\/saltmarch/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();

  const elsewhere = "image-store maintenance is running on another device that shares this library; wait for it to finish";
  (api.startImageMigration as any).mockRejectedValue(
    new ApiError(409, elsewhere, "maintenance_elsewhere"));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  expect(await screen.findByText(elsewhere)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Check" })).toBeEnabled();
});

test("a refusal because the store is busy is shown verbatim", async () => {
  const busy = "a fork, delete, export or backup is using the store; try again when it finishes";
  (api.startImageGc as any).mockRejectedValue(new ApiError(409, busy, "busy"));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(busy)).toBeInTheDocument();
});

test("a refused collection says why instead of reporting a deletion", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    mode: "collect", state: "refused", token: null, token_expires_at: null,
    error: "unknown or already used", collectable: [],
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/unknown or already used/)).toBeInTheDocument();
  expect(screen.queryByText(/Deleted/)).toBeNull();
});

test("a run that failed with no report shows its error", async () => {
  (api.startImageMigration as any).mockResolvedValue({ run: run({ state: "running" }) });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({
    state: "failed", error: { detail: "the pass stopped unexpectedly", kind: "run_failed" } }) });
  (api.getImageMaintenanceReport as any).mockRejectedValue(new ApiError(404, "no such report"));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Check" }));

  expect(await screen.findByText(/the pass stopped unexpectedly/)).toBeInTheDocument();
});
