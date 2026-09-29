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

test("Art shows the current version's large image and an original link", async () => {
  (api.listPCImages as any).mockResolvedValue([
    { name: "gallery_2", v: "b" }, { name: "avatar", v: "a" }, { name: "gallery_1", v: "c" },
  ]);
  renderPC();
  fireEvent.click(await screen.findByRole("tab", { name: "Art 3" }));
  expect(screen.getByRole("img", { name: "PC avatar" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open original PC avatar" }))
    .toHaveAttribute("href", "/img/realm/mara/default/avatar");
  fireEvent.click(screen.getByRole("button", { name: "View gallery_2" }));
  expect(screen.getByRole("img", { name: "PC gallery_2" })).toBeInTheDocument();
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
  await waitFor(() => expect(screen.getByRole("img", { name: "PC gallery_1" })).toBeInTheDocument());
  expect(screen.queryByRole("img", { name: "PC avatar" })).not.toBeInTheDocument();
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
