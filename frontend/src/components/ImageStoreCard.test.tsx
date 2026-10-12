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

// A row too new is dated by the clock time it becomes collectable: the window is
// an hour, so the day never needs saying.
const DUE = Date.UTC(2026, 10, 14, 12, 34) / 1000;
const DUE_TEXT = new Date(DUE * 1000).toLocaleTimeString();
const BIN = { ok: true, code: null, message: null };

const scan = (over: Record<string, unknown> = {}) => ({
  kind: "image-gc", mode: "scan", run_id: "g".repeat(32), state: "complete",
  grace_hours: 1,
  counts: { placements: 20, objects: 9, blobs: 9, unreferenced: 5, collectable: 3,
            collectable_blobs: 0, protected: 2 },
  collectable: [{ id: "a", blob: "x", bytes: 1024 }, { id: "b", blob: "y", bytes: 2048 },
                { id: "c", blob: "z", bytes: 4096 }],
  collectable_blobs: [],
  protected: [{ id: "d", blob: "w", why: "grace", collectable_at: DUE },
              { id: "e", blob: "v", why: "grace", collectable_at: DUE }],
  reclaimable_bytes: 7168, blocking: [], unreadable_sidecars: [], clock_skew: [],
  token: "tok-123", token_expires_at: Date.now() / 1000 + 3600,
  deleted: { objects: [], bytes: 0 }, recycled: [], released: [], outbox: [],
  foreign_outboxes: 0, unverified: [], recycle_bin: BIN, skipped: [],
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

test("find unused images shows the time objects become collectable", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/3 unused images can be sent to the recycle bin now/))
    .toBeInTheDocument();
  expect(api.startImageGc).toHaveBeenCalledWith({ dry_run: true }, expect.any(String));
  // The two that are not yet collectable, and the time they become so: a time,
  // not a date, because the wait is an hour.
  expect(screen.getByText(new RegExp(`2 more .*${DUE_TEXT.replace(/[/.:]/g, "\\$&")}`)))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send 3 unused images to the recycle bin" }))
    .toBeEnabled();
  // "Two scans a grace period apart" is gone: unused and an hour old is the rule.
  expect(screen.queryByText(/two scans/)).toBeNull();
  expect(screen.getByText(/at least an hour old/)).toBeInTheDocument();
});

test("nothing collectable offers no delete", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    collectable: [], counts: { ...scan().counts, collectable: 0 },
    reclaimable_bytes: 0, token: null, token_expires_at: null,
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/No unused images can be sent to the recycle bin yet/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Send \d+ unused/ })).toBeNull();
});

test("sending carries the token, after a confirmation that says what happens", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));
  const del = await screen.findByRole("button", { name: "Send 3 unused images to the recycle bin" });

  (confirm as any).mockReturnValueOnce(false);
  fireEvent.click(del);
  expect(api.startImageGc).toHaveBeenCalledTimes(1);          // declined: nothing sent
  const asked = String((confirm.mock.calls as unknown[][])[0][0]);
  expect(asked).toMatch(/this computer's recycle bin/);
  expect(asked).toMatch(/descriptions and tags are not kept/);
  expect(asked).toMatch(/another device linked but has not synced yet/);

  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    mode: "collect", token: null, token_expires_at: null,
    deleted: { objects: [], bytes: 0 },
    recycled: ["x", "y", "z"].map((blob) => (
      { id: blob, blob, name: `grimoire - ${blob}.png`, bytes: 2389 })),
  }));
  fireEvent.click(del);
  await waitFor(() => expect(api.startImageGc).toHaveBeenCalledTimes(2));
  expect(api.startImageGc).toHaveBeenLastCalledWith(
    { dry_run: false, token: "tok-123" }, expect.any(String));
  expect(await screen.findByText(/Sent 3 images to the recycle bin/)).toBeInTheDocument();
  // The token was spent, so the button that carried it is gone.
  expect(screen.queryByRole("button", { name: /^Send \d+ unused/ })).toBeNull();
});

test("a live run is rediscovered on mount", async () => {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "o".repeat(32), state: "landed", kind: "gc" }),
    run({ id: "l".repeat(32), state: "running", kind: "migrate" }),
  ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), state: "running" }) });
  render(<ImageStoreCard />);

  expect(await screen.findByText(/An image-store run is in progress/)).toBeInTheDocument();
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

  expect(await screen.findByText(/3 unused images can be sent to the recycle bin now/)).toBeInTheDocument();
  expect(api.getImageMaintenanceReport).toHaveBeenCalledWith("l".repeat(32));
});

/** A finished collection rediscovered on mount, with the report's fields as given. */
function showCollection(over: Record<string, unknown>) {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "l".repeat(32), state: "running", kind: "gc" }) ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), kind: "gc" }) });
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    mode: "collect", token: null, token_expires_at: null, ...over }));
  render(<ImageStoreCard />);
}

const binned = (n: number, bytes = 1024) => Array.from({ length: n }, (_, i) => ({
  id: `id${i}`, blob: `b${i}`, name: `grimoire - Mara ${i} - ab12cd3${i}.png`, bytes,
}));

test("a collection reports what the bin took, and where to find it", async () => {
  showCollection({ recycled: binned(2, 1536) });

  expect(await screen.findByText(
    /Sent 2 images to the recycle bin \(3\.0 KB\)\. The space is freed when you empty the bin\./,
  )).toBeInTheDocument();
  expect(screen.getByText(/Their names start with grimoire - \./)).toBeInTheDocument();
  expect(screen.queryByText(/Deleted/)).toBeNull();
});

test("a drained outbox file counts as sent, though no object names it", async () => {
  showCollection({
    recycled: [...binned(1), { id: null, blob: null, name: "grimoire - ab12cd34.png", bytes: 512 }],
  });

  expect(await screen.findByText(/Sent 2 images to the recycle bin/)).toBeInTheDocument();
});

test("a file Windows did not confirm is not counted as safely binned", async () => {
  const recycled = binned(3);
  showCollection({
    state: "recycle-bin-unverified",
    recycled,
    unverified: [recycled[1].name, recycled[2].name],
  });

  // Three went through the bin's door, one of them confirmed.
  expect(await screen.findByText(/Sent 1 image to the recycle bin \(1\.0 KB\)/))
    .toBeInTheDocument();
  expect(screen.getByText(
    /Windows did not confirm these went to the Recycle Bin\. Check the bin before collecting again\./,
  )).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "Not confirmed" });
  expect(list).toHaveTextContent(recycled[1].name);
  expect(list).toHaveTextContent(recycled[2].name);
  expect(list).not.toHaveTextContent(recycled[0].name);
});

test("a bin that refused a file says how many wait, and that nothing more was collected", async () => {
  showCollection({
    state: "trash-unavailable", recycled: binned(1), error: "PermissionError",
    outbox: ["grimoire - Mara - ab12cd34.png", "grimoire - ef56ab78.png"],
  });

  expect(await screen.findByText(
    /The recycle bin refused a file; 2 are waiting to be sent\. Nothing more was collected\./,
  )).toBeInTheDocument();
  expect(screen.getByText(/Sent 1 image to the recycle bin/)).toBeInTheDocument();
});

test("waiting and foreign outbox files are listed with where they are", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    outbox: ["grimoire - Mara - ab12cd34.png"], foreign_outboxes: 2,
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  const waiting = await screen.findByRole("list", { name: "Waiting to be sent" });
  expect(waiting).toHaveTextContent("grimoire - Mara - ab12cd34.png");
  expect(screen.getAllByText(/\.cache\/image-store\/recycle\//).length).toBeGreaterThan(0);
  expect(screen.getByText(/2 files are waiting in another device's folder/)).toBeInTheDocument();
  // Three collectable plus one waiting file: the button counts both.
  expect(screen.getByRole("button", { name: "Send 4 unused images to the recycle bin" }))
    .toBeEnabled();
});

test("waiting files alone can be sent", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    collectable: [], counts: { ...scan().counts, collectable: 0 }, protected: [],
    outbox: ["grimoire - ab12cd34.png"],
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByRole("button", { name: "Send 1 unused image to the recycle bin" }))
    .toBeEnabled();
});

test("a scan that finds no recycle bin offers nothing and says why", async () => {
  const message = "This computer has no recycle bin Grimoire can use, so nothing is collected.";
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    recycle_bin: { ok: false, code: "not_installed", message },
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Send \d+ unused/ })).toBeNull();
  expect(screen.queryByText(/not_installed/)).toBeNull();
});

test("a bin that is not known either way does not hide the button as 'no bin'", async () => {
  // `ok: null` is what a report that ended before it asked carries.
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    recycle_bin: { ok: null, code: null, message: null },
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByRole("button", { name: "Send 3 unused images to the recycle bin" }))
    .toBeEnabled();
});

test("a 409 no_recycle_bin on start shows its sentence", async () => {
  const sentence = "This computer has no recycle bin Grimoire can use, so nothing is collected.";
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));
  const send = await screen.findByRole("button", { name: "Send 3 unused images to the recycle bin" });

  (api.startImageGc as any).mockRejectedValueOnce(new ApiError(409, sentence, "no_recycle_bin"));
  fireEvent.click(send);

  expect(await screen.findByText(sentence)).toBeInTheDocument();
});

test("a legacy report still reads: grace_days, deleted.blobs and no recycled", async () => {
  const { recycled, released, outbox, unverified, recycle_bin, foreign_outboxes, ...old } =
    scan({ mode: "collect", token: null, token_expires_at: null }) as Record<string, any>;
  void recycled; void released; void outbox; void unverified; void recycle_bin;
  void foreign_outboxes;
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "l".repeat(32), state: "running", kind: "gc" }) ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), kind: "gc" }) });
  (api.getImageMaintenanceReport as any).mockResolvedValue({
    ...old, grace_hours: undefined, grace_days: 30, kept_blobs: [],
    deleted: {
      objects: [{ id: "a", blob: "x", bytes: 100 }, { id: "b", blob: "y", bytes: 100 }],
      blobs: [{ id: "a", blob: "x", bytes: 1024 }, { id: null, blob: "z", bytes: 1024 }],
      bytes: 2248,
    },
  });
  render(<ImageStoreCard />);

  // Objects plus orphan blobs, not an object's own blob; and it says deleted,
  // because that is what the build that wrote it did.
  expect(await screen.findByText(/Deleted 3 unused images, freeing 2\.2 KB/)).toBeInTheDocument();
  expect(screen.queryByText(/Sent \d+ image/)).toBeNull();
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
  expect(screen.queryByRole("button", { name: /^Send \d+ unused/ })).toBeNull();

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
  expect(screen.queryByText(/Deleted|Sent \d+ image/)).toBeNull();
});

test("a collection refused for want of a bin shows the sentence, never the code", async () => {
  const message = "This computer has no recycle bin Grimoire can use, so nothing is collected.";
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan({
    mode: "collect", state: "refused", token: null, token_expires_at: null,
    error: "no_recycle_bin", collectable: [],
    recycle_bin: { ok: false, code: "not_installed", message },
  }));
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.queryByText(/no_recycle_bin/)).toBeNull();
  expect(screen.queryByText(/not_installed/)).toBeNull();
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

test("a delete refused as busy keeps the scan, its token and its button", async () => {
  (api.getImageMaintenanceReport as any).mockResolvedValue(scan());
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));
  const del = await screen.findByRole("button", { name: "Send 3 unused images to the recycle bin" });

  const busy = "a fork, delete, export or backup is using the store; try again when it finishes";
  (api.startImageGc as any).mockRejectedValueOnce(new ApiError(409, busy, "busy"));
  fireEvent.click(del);

  expect(await screen.findByText(busy)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send 3 unused images to the recycle bin" })).toBeEnabled();
  expect(screen.getByText(/3 unused images can be sent to the recycle bin now/)).toBeInTheDocument();
});

test("a start that finds a run already going adopts it and drops the refusal", async () => {
  const msg = "image-store maintenance is already running";
  (api.startImageMigration as any).mockRejectedValue(new ApiError(409, msg, "run_in_flight"));
  (api.listGlobalRuns as any)
    .mockResolvedValueOnce({ runs: [] })
    .mockResolvedValue({ runs: [run({ id: "l".repeat(32), state: "running" })] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32) }) });
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Check" }));

  expect(await screen.findByText(/12 legacy files/)).toBeInTheDocument();
  expect(screen.queryByText(msg)).toBeNull();
});

test("a rediscovered collection is not described as a search", async () => {
  (api.listGlobalRuns as any).mockResolvedValue({ runs: [
    run({ id: "l".repeat(32), state: "running", kind: "gc" }) ] });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ id: "l".repeat(32), state: "running", kind: "gc" }) });
  render(<ImageStoreCard />);

  expect(await screen.findByText(/An image-store run is in progress/)).toBeInTheDocument();
  expect(screen.queryByText(/Looking for unused images/)).toBeNull();
});

test("a run this card started names its own mode", async () => {
  (api.startImageGc as any).mockResolvedValue({ run: run({ state: "running", kind: "gc" }) });
  (api.getGlobalRun as any).mockResolvedValue({ run: run({ state: "running", kind: "gc" }) });
  render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

  expect(await screen.findByText(/Looking for unused images/)).toBeInTheDocument();
});

test("a minimal failure report renders rather than crashing the card", async () => {
  // What a pass that raised before writing its own report used to leave: no
  // lists and no counts. The card shows the failure and treats the rest as empty.
  (api.getImageMaintenanceReport as any).mockResolvedValue({
    kind: "image-migration", dry_run: false, outcome: "failed", state: "failed",
    error: "OSError",
  });
  const { unmount } = render(<ImageStoreCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Check" }));

  expect(await screen.findByText(/Stopped \(failed\)\. OSError/)).toBeInTheDocument();
  expect(screen.getByText(/0 legacy files found/)).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Left alone" })).not.toBeInTheDocument();
  unmount();

  for (const mode of ["scan", "collect"]) {
    (api.getImageMaintenanceReport as any).mockResolvedValue({
      kind: "image-gc", mode, state: "failed", error: "OSError",
    });
    const view = render(<ImageStoreCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Find unused images" }));

    expect(await screen.findByText(/The run failed\. OSError/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Send \d+ unused/ })).not.toBeInTheDocument();
    view.unmount();
  }
});
