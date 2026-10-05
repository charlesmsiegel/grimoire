import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PCPage from "./PCPage";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
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
import { api } from "../api/client";

const DETAIL = { meta: { id: "mara", name: "Mara", tags: [], default_version: "default" },
  versions: [{ id: "default", name: "default", persona: {
    name: "Mara", pronouns: "she/her", summary: "traveler", description: "At Saltmarch.", birthdate: "",
  }, images: [] }] };

beforeEach(() => {
  vi.clearAllMocks();
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

function renderPC(url = "/worlds/realm/pcs/mara", state?: object) {
  return render(<MemoryRouter initialEntries={[{ pathname: url, state }]}>
    <Routes>
      <Route path="/worlds/:wid/pcs/:pid" element={<PCPage />} />
      <Route path="/campaigns/:cid/world/pcs/:pid" element={<PCPage campaign />} />
    </Routes>
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

test("a PC with no history says so", async () => {
  renderPC();
  expect(await screen.findByText("No earlier revisions.")).toBeInTheDocument();
});

test("Art shows linked thumbnails for every image without a large viewer", async () => {
  (api.listPCImages as any).mockResolvedValue([
    { name: "gallery_2", v: "b" }, { name: "avatar", v: "a" }, { name: "gallery_1", v: "c" },
  ]);
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 3" }));
  expect(screen.getByRole("link", { name: "avatar" }))
    .toHaveAttribute("href", "/img/realm/mara/default/avatar");
  expect(screen.getByRole("link", { name: "gallery_2" }))
    .toHaveAttribute("href", "/img/realm/mara/default/gallery_2");
  expect(document.querySelector(".art-viewer")).not.toBeInTheDocument();
});

test("a campaign art upload writes to the campaign and stays on the selected PC", async () => {
  renderPC("/campaigns/saltmarch/world/pcs/mara");
  fireEvent.click(await screen.findByRole("tab", { name: "Art 0" }));
  fireEvent.change(screen.getByLabelText("Add image"), { target: {
    files: [new File(["image"], "mara.png", { type: "image/png" })],
  } });
  await waitFor(() => expect(api.putPCImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "saltmarch" }, "mara", "default", "avatar", expect.any(File)));
  expect(screen.getByRole("heading", { name: "Mara", level: 1 })).toBeInTheDocument();
});

test("PC art controls promote, remove, and report a failed description save", async () => {
  (api.listPCImages as any).mockResolvedValue([{ name: "avatar", v: "a" }, { name: "gallery_1", v: "g" }]);
  (api.promotePCImage as any).mockResolvedValue({ ok: true });
  (api.deletePCImage as any).mockResolvedValue({ ok: true });
  (api.setPCImageDescription as any).mockRejectedValue(new Error("save failed"));
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Set as avatar" }));
  await waitFor(() => expect(api.promotePCImage).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default", "gallery_1"));
  fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
  await waitFor(() => expect(api.deletePCImage).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "mara", "default", "gallery_1"));
  fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of gallery_1" }),
    { target: { value: "Mara at Saltmarch." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/save failed/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Description of gallery_1" })).toHaveValue("Mara at Saltmarch.");
});

test("a failed image listing reports the failure instead of saying the version has no art", async () => {
  (api.listPCImages as any).mockRejectedValue(new Error("image list failed"));
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 0" }));
  expect(await screen.findByText(/Could not load art:.*image list failed/)).toBeInTheDocument();
  expect(screen.queryByText("No art for this version yet.")).not.toBeInTheDocument();
});

test("a slower image list for another version cannot replace the current version", async () => {
  let resolveOld!: (value: { name: string; v: string }[]) => void;
  (api.readPC as any).mockResolvedValue({ ...DETAIL, versions: [
    DETAIL.versions[0], { id: "second", name: "second", persona: DETAIL.versions[0].persona, images: [] },
  ] });
  (api.listPCImages as any).mockImplementation((_scope: unknown, _pid: string, version: string) =>
    version === "default" ? new Promise((resolve) => { resolveOld = resolve; })
      : Promise.resolve([{ name: "gallery_1", v: "new" }]));
  renderPC();
  const selector = await screen.findByRole("combobox", { name: "Version" });
  fireEvent.change(selector, { target: { value: "second" } });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
  resolveOld([{ name: "avatar", v: "old" }]);
  await waitFor(() => expect(screen.getByRole("link", { name: "gallery_1" })).toBeInTheDocument());
  expect(screen.queryByRole("link", { name: "avatar" })).not.toBeInTheDocument();
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
    expect(screen.getByText("No art for this version yet.")).toBeInTheDocument();
  } finally { Object.defineProperty(window, "innerWidth", { value: width, configurable: true }); }
});

test("the phone Art shortcut reaches linked PC thumbnails", async () => {
  (api.listPCImages as any).mockResolvedValue([{ name: "avatar", v: "a" }]);
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 375, configurable: true });
  try {
    renderPC();
    await screen.findByRole("heading", { name: "Mara", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "View art" }));
    expect(screen.getByRole("link", { name: "avatar" }))
      .toHaveAttribute("href", "/img/realm/mara/default/avatar");
    expect(screen.getByRole("link", { name: "avatar" }))
      .toHaveAttribute("target", "_blank");
  } finally { Object.defineProperty(window, "innerWidth", { value: width, configurable: true }); }
});
