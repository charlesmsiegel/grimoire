import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, parsePath, useLocation, useNavigate } from "react-router-dom";
import PCPage from "./PCPage";
import { ArtNavigationProvider } from "../components/art/ArtNavigationProvider";
import { artImage, artRead } from "../testkit/recordArt";
import { artChanged } from "../appEvents";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    readRecordArt: vi.fn(), appendRecordArt: vi.fn(), promoteRecordArt: vi.fn(), removeRecordArt: vi.fn(),
    setRecordArtFocus: vi.fn(), setRecordArtDescription: vi.fn(), draftRecordArtDescription: vi.fn(), getImageUsage: vi.fn(),
    readPC: vi.fn(), updatePCVersion: vi.fn(), updatePC: vi.fn(),
    listPCImages: vi.fn(), listAppearances: vi.fn(), listEntities: vi.fn(),
    putPCImage: vi.fn(), promotePCImage: vi.fn(), deletePCImage: vi.fn(),
    setPCImageDescription: vi.fn(), draftPCImageDescription: vi.fn(), setPCAvatarFocus: vi.fn(),
    listTags: vi.fn(), listModules: vi.fn(), getWorldSheetsIndex: vi.fn(),
    getCampaign: vi.fn(), getCampaignModule: vi.fn(), libraryStatus: vi.fn(),
    getCalendarMonths: vi.fn(), getCalendarConfig: vi.fn(),
    listPCRevisions: vi.fn(), readPCRevision: vi.fn(), restorePCRevision: vi.fn(),
    actorImageUrl: (scope: { id: string }, _kind: string, id: string,
                    version: string, name: string) => `/img/${scope.id}/${id}/${version}/${name}`,
  } };
});
import { ApiError, api } from "../api/client";

const DETAIL = { meta: { id: "mara", name: "Mara", tags: [], default_version: "default" },
  versions: [{ id: "default", name: "default", persona: {
    name: "Mara", pronouns: "she/her", summary: "traveler", description: "At Saltmarch.", birthdate: "",
  }, images: [] }] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, []));
  vi.mocked(api.appendRecordArt).mockResolvedValue({ name: "avatar", ext: "png", v: "new", image_id: "new" });

  (api.readPC as any).mockResolvedValue(DETAIL);
  (api.listPCImages as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.listModules as any).mockResolvedValue([]);
  (api.getWorldSheetsIndex as any).mockResolvedValue({ default: "", modules: [] });
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch", world: "realm" } });
  (api.getCampaignModule as any).mockResolvedValue({ resolved: null });
  (api.listAppearances as any).mockResolvedValue([]);
  (api.libraryStatus as any).mockResolvedValue(
    { in_library: true, diverged: false, can_promote: false, can_push: false });
  (api.updatePCVersion as any).mockResolvedValue({ ok: true });
  (api.putPCImage as any).mockResolvedValue({ ok: true });
  (api.getCalendarConfig as any).mockResolvedValue({ primary: { provider: "gregorian" } });
  (api.getCalendarMonths as any).mockResolvedValue({ months: [] });
  (api.listPCRevisions as any).mockResolvedValue([]);
  (api.restorePCRevision as any).mockResolvedValue({ ok: true });
});

let lastPCPath = "";
let lastPCKey = "";
let movePC: ReturnType<typeof useNavigate>;
function Spy() { movePC = useNavigate(); const location = useLocation(); lastPCPath = location.pathname + location.search; lastPCKey = location.key; return null; }
function renderPC(url = "/worlds/realm/pcs/mara", state?: object, artNavigation = false) {
  const content = <><Spy /><Routes>
    <Route path="/worlds/:wid/pcs/:pid" element={<PCPage />} />
    <Route path="/campaigns/:cid/world/pcs/:pid" element={<PCPage campaign />} />
  </Routes></>;
  return render(<MemoryRouter initialEntries={[{ ...parsePath(url), state }]}>
    {artNavigation ? <ArtNavigationProvider>{content}</ArtNavigationProvider> : content}
  </MemoryRouter>);
}

test("a PC owns a page with a read-only persona and one context column", async () => {
  renderPC();
  expect(await screen.findByRole("heading", { name: "Mara", level: 1 })).toBeInTheDocument();
  expect(document.querySelectorAll(".context-column")).toHaveLength(1);
  expect(within(screen.getByRole("main")).getByText("At Saltmarch.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Description" })).not.toBeInTheDocument();
});

test("a newly created PC opens in edit mode but a direct link does not", async () => {
  renderPC("/worlds/realm/pcs/mara", { newPC: true });
  expect(await screen.findByRole("textbox", { name: "Description" })).toBeInTheDocument();
});

test("a campaign PC uses its campaign scope", async () => {
  renderPC("/campaigns/saltmarch/world/pcs/mara");
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  expect(api.readPC).toHaveBeenCalledWith({ kind: "campaign", id: "saltmarch" }, "mara");
});

test("a missing PC reports the read failure", async () => {
  (api.readPC as any).mockRejectedValue(new Error("not found"));
  renderPC();
  expect(await screen.findByText(/not found/i)).toBeInTheDocument();
});

test("editing a PC saves its persona and returns to read mode", async () => {
  renderPC();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }),
    { target: { value: "At Realm." } });
  fireEvent.click(screen.getByRole("button", { name: "Save persona" }));
  await waitFor(() => expect(api.updatePCVersion).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default",
    expect.objectContaining({ description: "At Realm." })));
});

test("goals and narrator notes are edited in the form and shown read-only in the column", async () => {
  (api.readPC as any).mockResolvedValue({ ...DETAIL, versions: [{ ...DETAIL.versions[0], persona: {
    ...DETAIL.versions[0].persona, goals: "find the archive", player_notes: "never speak for her",
  } }] });
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  const column = document.querySelector(".context-column") as HTMLElement;
  expect(within(column).getByText("find the archive")).toBeInTheDocument();
  expect(within(column).getByText("never speak for her")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Goals" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Goals" }), { target: { value: "reach the coast" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Notes for the narrator" }),
    { target: { value: "keep her guarded" } });
  fireEvent.click(screen.getByRole("button", { name: "Save persona" }));
  await waitFor(() => expect(api.updatePCVersion).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default",
    expect.objectContaining({ goals: "reach the coast", player_notes: "keep her guarded" })));
});

test("history lists earlier texts, previews one read-only, and restores it", async () => {
  (api.listPCRevisions as any).mockResolvedValue([
    { id: "r2", saved: "2026-10-05T12:00:00Z", name: "Mara" },
    { id: "r1", saved: "2026-10-04T12:00:00Z", name: "Mara Vey" },
  ]);
  (api.readPCRevision as any).mockResolvedValue({
    name: "Mara Vey", pronouns: "", summary: "", description: "An older telling.", goals: "go home",
  });
  // a fresh object per read, as the network gives: the history list refreshes
  // when a re-read lands, and an identical reference is no re-read at all
  (api.readPC as any).mockImplementation(async () => structuredClone(DETAIL));
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  const column = document.querySelector(".context-column") as HTMLElement;
  const rows = await within(column).findAllByRole("button", { name: /2026|\d/ });
  expect(rows.filter((r) => r.classList.contains("history-row"))).toHaveLength(2);
  // a row whose text held another name says so
  fireEvent.click(within(column).getByRole("button", { name: /Mara Vey/ }));
  const main = screen.getByRole("main");
  expect(await within(main).findByText("An older telling.")).toBeInTheDocument();
  expect(within(main).getByText("go home")).toBeInTheDocument();
  expect(within(main).queryByText("At Saltmarch.")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(api.readPCRevision).toHaveBeenCalledWith({ kind: "world", id: "realm" }, "mara", "default", "r1");

  fireEvent.click(screen.getByRole("button", { name: "Restore this text" }));
  await waitFor(() => expect(api.restorePCRevision).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default", "r1"));
  // back on the current text, re-read, and history asked again
  expect(await within(main).findByText("At Saltmarch.")).toBeInTheDocument();
  await waitFor(() => expect((api.listPCRevisions as any).mock.calls.length).toBeGreaterThan(1));
});

test("Back to current leaves a preview without restoring", async () => {
  (api.listPCRevisions as any).mockResolvedValue([{ id: "r1", saved: "2026-10-04T12:00:00Z", name: "Mara" }]);
  (api.readPCRevision as any).mockResolvedValue({ ...DETAIL.versions[0].persona, description: "Before." });
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  const column = document.querySelector(".context-column") as HTMLElement;
  fireEvent.click((await within(column).findAllByRole("button")).find((b) => b.classList.contains("history-row"))!);
  expect(await screen.findByText("Before.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Back to current" }));
  expect(await screen.findByText("At Saltmarch.")).toBeInTheDocument();
  expect(api.restorePCRevision).not.toHaveBeenCalled();
});

test("history cannot be opened over an unsaved edit", async () => {
  (api.listPCRevisions as any).mockResolvedValue([{ id: "r1", saved: "2026-10-04T12:00:00Z", name: "Mara" }]);
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const row = (await screen.findAllByRole("button")).find((b) => b.classList.contains("history-row"))!;
  expect(row).toBeDisabled();
});

test("switching version drops the other version's history rows and its preview", async () => {
  const two = { ...DETAIL, versions: [DETAIL.versions[0],
    { id: "older", name: "older", persona: { ...DETAIL.versions[0].persona, description: "Older one." }, images: [] }] };
  (api.readPC as any).mockResolvedValue(two);
  let releaseB: (v: unknown) => void = () => {};
  (api.listPCRevisions as any).mockImplementation((_s: unknown, _p: string, vid: string) =>
    vid === "default" ? Promise.resolve([{ id: "rA", saved: "2026-10-04T12:00:00Z", name: "Mara" }])
      : new Promise((r) => { releaseB = r; }));
  (api.readPCRevision as any).mockResolvedValue({ ...DETAIL.versions[0].persona, description: "A's past." });
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  const column = document.querySelector(".context-column") as HTMLElement;
  fireEvent.click((await within(column).findAllByRole("button")).find((b) => b.classList.contains("history-row"))!);
  expect(await screen.findByText("A's past.")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Version" }), { target: { value: "older" } });
  // B's list has not arrived: A's rows are gone rather than clickable, and so is A's preview
  await waitFor(() => expect(document.querySelectorAll(".history-row")).toHaveLength(0));
  expect(screen.queryByText("A's past.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Restore this text" })).not.toBeInTheDocument();
  releaseB([]);
  expect(await screen.findByText("No earlier revisions.")).toBeInTheDocument();
});

test("a slow answer for an earlier history click cannot replace the later one", async () => {
  (api.listPCRevisions as any).mockResolvedValue([
    { id: "r2", saved: "2026-10-05T12:00:00Z", name: "Mara" },
    { id: "r1", saved: "2026-10-04T12:00:00Z", name: "Mara" },
  ]);
  let releaseFirst: (v: unknown) => void = () => {};
  (api.readPCRevision as any).mockImplementation((_s: unknown, _p: string, _v: string, rid: string) =>
    rid === "r2" ? new Promise((r) => { releaseFirst = r; })
      : Promise.resolve({ ...DETAIL.versions[0].persona, description: "The later click." }));
  renderPC();
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  await waitFor(() => expect(document.querySelectorAll(".history-row")).toHaveLength(2));
  const rows = document.querySelectorAll<HTMLButtonElement>(".history-row");
  fireEvent.click(rows[0]);          // r2: slow
  fireEvent.click(rows[1]);          // r1: answers at once
  expect(await screen.findByText("The later click.")).toBeInTheDocument();
  releaseFirst({ ...DETAIL.versions[0].persona, description: "The earlier click." });
  await waitFor(() => expect(api.readPCRevision).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("The earlier click.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Restore this text" }));
  await waitFor(() => expect(api.restorePCRevision).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default", "r1"));
});

test("a PC with no history says so", async () => {
  renderPC();
  expect(await screen.findByText("No earlier revisions.")).toBeInTheDocument();
});

test("PC art has contained thumbnails and opens the original only when requested", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  expect(screen.getByRole("button", { name: "View Primary image" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "View Gallery image 1" }));
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", "/api/art/gallery_1?v=1");
  expect(api.listPCImages).not.toHaveBeenCalled();
});
test("campaign uploads use the resolved PC version and server allocation", async () => {
  renderPC("/campaigns/saltmarch/world/pcs/mara?tab=art&v=default");
  await screen.findByText("No images yet");
  const file = new File(["image"], "mara.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Image files"), { target: { files: [file] } });
  await waitFor(() => expect(api.appendRecordArt).toHaveBeenCalledWith(
    { scope: { kind: "campaign", id: "saltmarch" }, kind: "pcs", record_id: "mara", version: "default" }, file));
  expect(api.putPCImage).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "Mara", level: 1 })).toBeInTheDocument();
});
test("PC mutations capture image and primary bytes, and preserve a failed description draft", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  vi.mocked(api.promoteRecordArt).mockResolvedValue({ ok: true });
  vi.mocked(api.removeRecordArt).mockResolvedValue({ ok: true });
  vi.mocked(api.setRecordArtDescription).mockRejectedValue(new Error("save failed"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Make gallery_1 primary" }));
  const target = { scope: { kind: "world" as const, id: "realm" }, kind: "pcs" as const, record_id: "mara", version: "default" };
  await waitFor(() => expect(api.promoteRecordArt).toHaveBeenCalledWith(target, artImage(target, "gallery_1"), "avatar-1"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Remove gallery_1" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Remove gallery_1" }));
  await waitFor(() => expect(api.removeRecordArt).toHaveBeenCalledWith(target, artImage(target, "gallery_1")));
  await waitFor(() => expect(screen.getByRole("button", { name: "Description of gallery_1" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of gallery_1" }), { target: { value: "Mara at Saltmarch." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/save failed/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Description of gallery_1" })).toHaveValue("Mara at Saltmarch.");
  confirm.mockRestore();
});
test("a failed PC art read reports failure and unknown count while persona remains readable", async () => {
  vi.mocked(api.readRecordArt).mockRejectedValue(new Error("image list failed"));
  renderPC();
  await screen.findByText("At Saltmarch.");
  fireEvent.click(screen.getByRole("tab", { name: "Art" }));
  expect(await screen.findByText(/Art could not be read:.*image list failed/)).toBeInTheDocument();
  expect(screen.queryByText("No images yet")).not.toBeInTheDocument();
});
test("a slow previous-version art read cannot replace the selected PC version", async () => {
  let resolveOld!: (value: ReturnType<typeof artRead>) => void;
  vi.mocked(api.readPC).mockResolvedValue({ ...DETAIL, versions: [
    DETAIL.versions[0], { id: "second", name: "second", persona: DETAIL.versions[0].persona, images: [] },
  ] });
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => target.version === "default"
    ? new Promise((resolve) => { resolveOld = resolve; }) : artRead(target, [artImage(target, "gallery_1")]));
  renderPC();
  const selector = await screen.findByRole("combobox", { name: "Version" });
  await waitFor(() => expect(api.readRecordArt).toHaveBeenCalled());
  fireEvent.change(selector, { target: { value: "second" } });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
  resolveOld(artRead({ scope: { kind: "world", id: "realm" }, kind: "pcs", record_id: "mara", version: "default" }));
  expect(screen.getByRole("button", { name: "View Gallery image 1" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "View Primary image" })).not.toBeInTheDocument();
  expect(lastPCPath).toContain("v=second");
});

test("a late record refresh cannot restore a version after the reader switches versions", async () => {
  let resolveRefresh!: (value: typeof DETAIL) => void;
  const second = { id: "second", name: "second", persona: {
    ...DETAIL.versions[0].persona, description: "At Realm." }, images: [] };
  const twoVersions = { ...DETAIL, versions: [DETAIL.versions[0], second] };
  (api.readPC as any).mockResolvedValueOnce(twoVersions)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));
  renderPC();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Version" }),
    { target: { value: "second" } });
  resolveRefresh(twoVersions);
  await waitFor(() => expect(screen.getByText("At Realm.")).toBeInTheDocument());
  expect(screen.getByRole("combobox", { name: "Version" })).toHaveValue("second");
});

test("the phone main area opens Art even when this PC has no image", async () => {
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 375, configurable: true });
  try {
    renderPC();
    await screen.findByRole("heading", { name: "Mara", level: 1 });
    expect(document.querySelector(".shell.phone .mobile-art-entry")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View art" }));
    expect(screen.getByRole("tab", { name: "Art 0" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("No images yet")).toBeInTheDocument();
  } finally { Object.defineProperty(window, "innerWidth", { value: width, configurable: true }); }
});

test("the phone Art shortcut opens the common PC shelf", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target)]));
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 375, configurable: true });
  try {
    renderPC();
    await screen.findByRole("heading", { name: "Mara", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View art" }));
    expect(await screen.findByRole("button", { name: "View Primary image" })).toBeInTheDocument();
  } finally { Object.defineProperty(window, "innerWidth", { value: width, configurable: true }); }
});

test("the PC Art deep link selects its version and phone action", async () => {
  renderPC("/worlds/realm/pcs/mara?tab=art&v=default");
  await screen.findByRole("heading", { name: "Mara", level: 1 });
  expect(screen.getByRole("tab", { name: /^Art/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("button", { name: "View art" })).toBeInTheDocument();
  expect(await screen.findByText("No images yet")).toBeInTheDocument();
});
test("a PC identity preview opens the viewer and crop belongs to Manage", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target)]));
  renderPC();
  fireEvent.click((await screen.findAllByRole("button", { name: "View Mara art" }))[0]);
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: /crop/i })).not.toBeInTheDocument();
});

test("refusing an Art discard keeps the PC version and tab unchanged", async () => {
  vi.mocked(api.readPC).mockResolvedValue({ ...DETAIL, versions: [DETAIL.versions[0],
    { id: "veiled", name: "veiled", persona: { ...DETAIL.versions[0].persona, description: "At Realm." }, images: [] }] });
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  renderPC("/worlds/realm/pcs/mara?tab=art&v=default");
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of avatar" }), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.change(screen.getByRole("combobox", { name: "Version" }), { target: { value: "veiled" } });
  fireEvent.click(screen.getByRole("tab", { name: "Persona" }));
  expect(screen.getByRole("combobox", { name: "Version" })).toHaveValue("default");
  expect(lastPCPath).toContain("tab=art&v=default");
  expect(screen.getByRole("textbox", { name: "Description of avatar" })).toHaveValue("Unsaved");
  confirm.mockRestore();
});

test("an addressed PC version change closes viewing and resets its Art management", async () => {
  vi.mocked(api.readPC).mockResolvedValue({ ...DETAIL, versions: [DETAIL.versions[0],
    { id: "veiled", name: "veiled", persona: DETAIL.versions[0].persona, images: [] }] });
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, target.version === "default" ? [artImage(target)] : []));
  renderPC("/worlds/realm/pcs/mara?tab=art&v=default");
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "View Primary image" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  act(() => movePC("/worlds/realm/pcs/mara?tab=art&v=veiled"));
  await screen.findByText("No images yet");
  expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Manage art" })).toBeInTheDocument();
});

test("removing an addressed PC version restores the same default as a direct bare URL", async () => {
  vi.mocked(api.readPC).mockResolvedValue({ ...DETAIL, versions: [DETAIL.versions[0],
    { id: "veiled", name: "veiled", persona: DETAIL.versions[0].persona, images: [] }] });
  renderPC("/worlds/realm/pcs/mara?tab=art&v=veiled");
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Version" })).toHaveValue("veiled"));
  act(() => movePC("/worlds/realm/pcs/mara?tab=art"));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Version" })).toHaveValue("default"));
  expect(api.readRecordArt).toHaveBeenLastCalledWith(expect.objectContaining({ version: "default" }), expect.anything());
});

test("phone removed-tile focus returns to the visible main record heading", async () => {
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 375, configurable: true });
  try {
    vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, [artImage(target, "gallery_1")]));
    renderPC("/worlds/realm/pcs/mara?tab=art");
    await screen.findByRole("heading", { name: "Mara", level: 1 });
    fireEvent.click(await screen.findByRole("button", { name: "View Gallery image 1" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, []));
    act(() => artChanged());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Mara", level: 1 })).toHaveFocus();
  } finally { Object.defineProperty(window, "innerWidth", { value: width, configurable: true }); }
});


test.each((["world", "campaign"] as const).flatMap(scope => (["ok", "partial"] as const).map(status => [scope, status] as const)))(
  "%s PC %s Art save refreshes without replacing its accepted route", async (scope, status) => {
    vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, [artImage(target)]));
    if (status === "ok") vi.mocked(api.setRecordArtDescription).mockResolvedValue({ ok: true });
    else vi.mocked(api.setRecordArtDescription).mockRejectedValue(new ApiError(500, "Cleanup failed", "description_cleanup_incomplete", {
      committed: true, confirmed_text: "A map",
      recovery: { available: true, description_rev: "recovery-1",
        effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
        write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] } },
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const prefix = scope === "world" ? "/worlds/realm" : "/campaigns/saltmarch/world";
    renderPC(prefix + "/pcs/mara?tab=art&v=default", undefined, true);
    fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
    const before = lastPCKey;
    fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Description of avatar" }), { target: { value: "A map" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.readPC).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument());
    expect(lastPCKey).toBe(before);
    expect(confirm).not.toHaveBeenCalled();
    if (status === "partial") {
      expect(screen.getByRole("textbox", { name: "Description of avatar" })).toHaveValue("A map");
      expect(screen.getByRole("button", { name: "Finish applying saved description" })).toBeEnabled();
    } else expect(screen.getByText("Saved")).toBeInTheDocument();
    confirm.mockRestore();
  });
