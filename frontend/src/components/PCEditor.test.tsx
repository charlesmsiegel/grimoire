import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      // The campaign-scope sidebar's LibraryPanel (#60). "already library
      // content, unedited" renders no button, so this suite is unchanged;
      // LibraryPanel.test.tsx owns the panel's own behaviour.
      libraryStatus: vi.fn().mockResolvedValue(
        { in_library: true, diverged: false, can_promote: false, can_push: false }),
      promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(), createCampaignPC: vi.fn(),
      listPCs: vi.fn(), listTags: vi.fn(), readPC: vi.fn(), createPC: vi.fn(),
      updatePC: vi.fn(), deletePC: vi.fn(), createPCVersion: vi.fn(), updatePCVersion: vi.fn(),
      getCalendarMonths: vi.fn(), putSheetCreation: vi.fn(), getSheet: vi.fn(),
      listPCImages: vi.fn(), putPCImage: vi.fn(), deletePCImage: vi.fn(),
      setPCImageDescription: vi.fn(),
      draftPCImageDescription: vi.fn(),
      promotePCImage: vi.fn(), setPCAvatarFocus: vi.fn(),
      actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string) =>
        `/img/${sc.id}/${k}/${a}/${v}/${n}`,
    },
  };
});
import { api, ApiError } from "../api/client";

const GREG_MONTHS = [
  { key: "01", name: "January", days: 31 },
  { key: "02", name: "February", days: 28 },
  { key: "03", name: "March", days: 31 },
  { key: "04", name: "April", days: 30 },
  { key: "05", name: "May", days: 31 },
  { key: "06", name: "June", days: 30 },
  { key: "07", name: "July", days: 31 },
  { key: "08", name: "August", days: 31 },
  { key: "09", name: "September", days: 30 },
  { key: "10", name: "October", days: 31 },
  { key: "11", name: "November", days: 30 },
  { key: "12", name: "December", days: 31 },
];

const DETAIL = {
  meta: { id: "elara", name: "Elara", tags: ["student"], default_version: "default" },
  versions: [{ id: "default", name: "default", persona: { name: "Elara", pronouns: "she/her", summary: "scholar", birthdate: "", description: "a wanderer" } }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listTags as any).mockResolvedValue({ student: "Student" });
  (api.readPC as any).mockResolvedValue(DETAIL);
  (api.createPC as any).mockResolvedValue({ pc: "rook", version: "default" });
  (api.updatePC as any).mockResolvedValue({ ok: true });
  (api.updatePCVersion as any).mockResolvedValue({ ok: true });
  (api.createPCVersion as any).mockResolvedValue({ version: "young" });
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.listPCImages as any).mockResolvedValue([]);
  (api.setPCImageDescription as any).mockResolvedValue({ ok: true });
  (api.putPCImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.deletePCImage as any).mockResolvedValue({ ok: true });
  (api.promotePCImage as any).mockResolvedValue({ ok: true });
  (api.setPCAvatarFocus as any).mockResolvedValue({ ok: true });
});

test("clicking a PC shows a read-only view; Edit reveals the form", async () => {
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("a wanderer");                         // rendered description
  expect(container.querySelector("textarea")).toBeNull();        // read-only
  expect(screen.getByText("she/her")).toBeInTheDocument();       // sidebar metadata
  expect(screen.getByText("scholar")).toBeInTheDocument();
  expect(screen.getByText("Student")).toBeInTheDocument();       // tag chip

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(container.querySelector("textarea")).not.toBeNull();    // form revealed
});

test("saving the persona returns to the read-only view", async () => {
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() => expect(container.querySelector("textarea")).toBeNull());
});

test("creating a PC prompts for a name and opens the form directly", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Elara");
  fireEvent.click(screen.getByRole("button", { name: /new pc/i }));
  await waitFor(() => expect(api.createPC).toHaveBeenCalledWith("w", { name: "Rook" }));
  await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull()); // straight to the form
});

test("editing persona saves the selected version", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "a sage" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", "default",
      expect.objectContaining({ description: "a sage" })),
  );
});

test("editing the birthdate saves it on the persona", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Birthdate year"), { target: { value: "1990" } });
  const monthSelect = await screen.findByLabelText("Birthdate month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "06" } });
  const daySelect = screen.getByLabelText("Birthdate day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "29" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", "default",
      expect.objectContaining({ birthdate: "1990-06-29" })),
  );
});

test("toggling a tag chip in the form updates the PC tags", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(await screen.findByRole("button", { name: "Student" }));
  await waitFor(() => expect(api.updatePC).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", { tags: [] }));
});

test("adding a version prompts and posts the current persona", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Young");
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.click(screen.getByRole("button", { name: /\+ version/i }));
  await waitFor(() =>
    expect(api.createPCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara",
      expect.objectContaining({ name: "Young" })),
  );
});


const PC_DETAIL = {
  meta: { id: "elara", name: "Elara", tags: [], default_version: "young" },
  versions: [
    { id: "young", name: "Young", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "d" } },
    { id: "older", name: "Older", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "d2" } },
  ],
};

test("campaign scope: picking a version confirms and calls pickVersion", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  (api.readPC as any).mockResolvedValue(PC_DETAIL);
  (api.listAppearances as any).mockResolvedValue([]);          // unlocked
  (api.pickVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "Elara" }));
  fireEvent.click(await screen.findByRole("button", { name: "Pick this version" }));
  await waitFor(() => expect(api.pickVersion).toHaveBeenCalledWith("run", "pcs", "elara", "young"));
});

test("campaign scope: a locked PC offers import from world", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  (api.readPC as any).mockImplementation(async (scope: any) =>
    scope.kind === "campaign" ? { ...PC_DETAIL, versions: [PC_DETAIL.versions[0]] } : PC_DETAIL);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "pcs", id: "elara", version: "young", role: "player", scenes: [] },
  ]);
  (api.importVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "Elara" }));
  expect(await screen.findByText(/locked to/i)).toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText("Import version"), { target: { value: "older" } });
  fireEvent.click(screen.getByRole("button", { name: "Import from world" }));
  await waitFor(() => expect(api.importVersion).toHaveBeenCalledWith("run", "pcs", "elara", "older"));
});

it("wizard trigger opens the wizard, finds the characters sheet type, and creates a PC sheet", async () => {
  (api.listPCs as any).mockResolvedValue([]);
  (api.createPC as any).mockResolvedValue({ pc: "elara" });
  (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
  (api.readPC as any).mockResolvedValue({
    meta: { id: "elara", name: "Elara", tags: [], default_version: "default" },
    versions: [{ id: "default", name: "default", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "" } }],
  });
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<PCEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New PC with sheet…"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
  fireEvent.click(screen.getByText("Next"));
  const select = await screen.findByLabelText("Sheet type");
  expect(within(select).getByText("Hero")).toBeInTheDocument();  // proves typeKind("pcs") -> "characters" found it
  fireEvent.change(select, { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
    { kind: "world", id: "w1" }, "testmod", "pcs", "elara", { sheet_type: "hero", spends: {}, expected: null }));
});

it("world scope: wires the wizard's deleteRecord to api.deletePC so a failed sheet write rolls back", async () => {
  (api.listPCs as any).mockResolvedValue([]);
  (api.createPC as any).mockResolvedValue({ pc: "elara" });
  (api.putSheetCreation as any).mockRejectedValue({ detail: "nope" });
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<PCEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New PC with sheet…"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.deletePC).toHaveBeenCalledWith({ kind: "world", id: "w1" }, "elara"));
});

it("campaign scope: hides the sheet-creation wizard trigger (no campaign-scoped PC-delete API exists to roll back a failed write) but keeps plain + New PC", async () => {
  (api.listPCs as any).mockResolvedValue([]);
  (api.createCampaignPC as any).mockResolvedValue({ pc: "elara" });
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w1" module={module} />);
  await screen.findByRole("button", { name: "+ New PC" });
  expect(screen.queryByText("+ New PC with sheet…")).toBeNull();
});

it("a wizard opened at world scope closes (not just its trigger) when the same instance's scope changes to campaign", async () => {
  // Regression for a Codex finding: the button-level gate on "+ New PC with sheet..."
  // isn't enough on its own -- if a parent reuses this component instance across a
  // scope change (no remount) instead of opening it fresh at campaign scope, a wizard
  // already open from world scope must not survive the transition. Proves both the
  // render-path gate (wizardOpen && module && worldScope) and the scope-change reset
  // effect, using rerender (not a fresh render) so the instance genuinely persists.
  (api.listPCs as any).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  const { rerender } = render(<PCEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New PC with sheet…"));
  expect(await screen.findByText("New pc (with sheet)")).toBeInTheDocument();     // wizard open

  rerender(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w1" module={module} />);
  await waitFor(() => expect(screen.queryByText("New pc (with sheet)")).toBeNull()); // wizard closed
  expect(screen.getByText("Select or create a PC.")).toBeInTheDocument();        // plain view instead
});

// ---- per-version images (#219) ----
const FILE = new File([new Uint8Array([1, 2, 3])], "art.png", { type: "image/png" });

it("a PC with no images shows the initials fallback and an empty shelf", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("a wanderer");
  expect(screen.getByText("no avatar")).toBeInTheDocument();
  expect(screen.queryByLabelText("Adjust avatar crop")).toBeNull();
});

it("the shelf renders the avatar and gallery, cache-busted by the listing's token", async () => {
  (api.listPCImages as any).mockResolvedValue([
    { name: "avatar", ext: "png", v: "a1" },
    { name: "gallery_10", ext: "png", v: "g10" },
    { name: "gallery_2", ext: "png", v: "g2" },
  ]);
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));

  const avatar = await screen.findByAltText("avatar");
  expect(avatar.getAttribute("src")).toBe("/img/w/pcs/elara/default/avatar?v=a1");
  // numeric order, not lexicographic ("gallery_10" must not sort before "gallery_2")
  const gallery = [screen.getByAltText("gallery_2"), screen.getByAltText("gallery_10")];
  expect(gallery.map((g) => g.getAttribute("src"))).toEqual([
    "/img/w/pcs/elara/default/gallery_2?v=g2",
    "/img/w/pcs/elara/default/gallery_10?v=g10",
  ]);
});

it("the first upload becomes the avatar; the next queues into the gallery", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("no avatar");

  fireEvent.change(screen.getByLabelText("Add image"), { target: { files: [FILE] } });
  await waitFor(() => expect(api.putPCImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "avatar", FILE));

  (api.listPCImages as any).mockResolvedValue([
    { name: "avatar", ext: "png", v: "a1" }, { name: "gallery_3", ext: "png", v: "g3" },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Elara" }));   // re-select: shelf reloads
  await screen.findByAltText("gallery_3");
  fireEvent.change(screen.getByLabelText("Add image"), { target: { files: [FILE] } });
  // next free slot is one past the HIGHEST gallery_N, not the count
  await waitFor(() => expect(api.putPCImage).toHaveBeenLastCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "gallery_4", FILE));
});

it("a gallery image can be promoted to avatar, and either can be removed", async () => {
  (api.listPCImages as any).mockResolvedValue([
    { name: "avatar", ext: "png", v: "a1" }, { name: "gallery_1", ext: "png", v: "g1" },
  ]);
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));

  fireEvent.click(await screen.findByRole("button", { name: "Set as avatar" }));
  await waitFor(() => expect(api.promotePCImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "gallery_1"));

  // Scoped to each tile rather than picked out of a flat list by index: both
  // tiles carry a button reading "Remove", so a positional query would keep
  // passing if the shelf's order changed under it.
  const tile = (sel: string) => within(container.querySelector(sel) as HTMLElement);
  fireEvent.click(tile(".avatar-tile").getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(api.deletePCImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "avatar"));

  const gallery = [...container.querySelectorAll(".shelf-tile")]
    .find((t) => t.querySelector('img[alt="gallery_1"]')) as HTMLElement;
  fireEvent.click(within(gallery).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(api.deletePCImage).toHaveBeenLastCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "gallery_1"));
});

it("a failed image write is reported in the banner, not swallowed", async () => {
  // Every WRITE on this shelf reports; only the listing is allowed to fail
  // quietly. `errorText` is what turns the rejection into the sentence shown.
  (api.putPCImage as any).mockRejectedValue(new ApiError(400, "unsupported image type"));
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("no avatar");

  fireEvent.change(screen.getByLabelText("Add image"), { target: { files: [FILE] } });
  expect(await screen.findByText("unsupported image type")).toBeInTheDocument();
});

it("a listing that fails leaves the persona readable instead of a banner", async () => {
  (api.listPCImages as any).mockRejectedValue(new ApiError(500, "disk gone"));
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("a wanderer");            // the record still reads
  expect(screen.getByText("no avatar")).toBeInTheDocument();
  expect(screen.queryByText("disk gone")).toBeNull();
});

it("clicking the portrait opens the crop picker and saves a focus", async () => {
  (api.listPCImages as any).mockResolvedValue([{ name: "avatar", ext: "png", v: "a1" }]);
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));

  fireEvent.click(await screen.findByLabelText("Adjust avatar crop"));
  const slider = await screen.findByLabelText("Crop position");
  fireEvent.change(slider, { target: { value: "70" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setPCAvatarFocus).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default", 70));
});

it("images follow the viewed version, not the PC", async () => {
  (api.readPC as any).mockResolvedValue({
    meta: { id: "elara", name: "Elara", tags: [], default_version: "default" },
    versions: [
      { id: "default", name: "default", persona: DETAIL.versions[0].persona },
      { id: "older", name: "older", persona: { ...DETAIL.versions[0].persona, description: "grey now" } },
    ],
  });
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await waitFor(() => expect(api.listPCImages).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default"));

  fireEvent.change(screen.getAllByLabelText("Version")[0], { target: { value: "older" } });
  await waitFor(() => expect(api.listPCImages).toHaveBeenLastCalledWith(
    { kind: "world", id: "w" }, "elara", "older"));
});

it("the rail draws each PC's portrait from its summary, at its own crop", async () => {
  (api.listPCs as any).mockResolvedValue([
    { id: "elara", name: "Elara", tags: [], default_version: "v2", has_avatar: true,
      avatar_focus: 20, versions: [] },
    { id: "rook", name: "Rook", tags: [], default_version: "default", has_avatar: false,
      avatar_focus: null, versions: [] },
  ]);
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Rook");
  const thumb = container.querySelector(".pc-row-portrait img") as HTMLImageElement;
  expect(thumb.getAttribute("src")).toBe("/img/w/pcs/elara/v2/avatar");
  expect(thumb.style.objectPosition).toBe("20% 20%");
  // the one with no avatar falls back to initials rather than a broken img
  expect(container.querySelectorAll(".pc-row-portrait img")).toHaveLength(1);
  expect(container.querySelectorAll(".pc-row-portrait .portrait-initials")).toHaveLength(1);
});

it("campaign scope addresses the campaign's own copy of the art", async () => {
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listPCImages as any).mockResolvedValue([{ name: "avatar", ext: "png", v: "a1" }]);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  const avatar = await screen.findByAltText("avatar");
  expect(avatar.getAttribute("src")).toBe("/img/run/pcs/elara/default/avatar?v=a1");
  fireEvent.change(screen.getByLabelText("Add image"), { target: { files: [FILE] } });
  await waitFor(() => expect(api.putPCImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "elara", "default", "gallery_1", FILE));
});


test("a PC's art carries its description, and saving one names that image", async () => {
  (api.readPC as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0],
                 images: ["avatar", "gallery_1"],
                 // avatar described, gallery_1 never reviewed
                 image_descriptions: { avatar: "Elara in travelling clothes." } }],
  });
  (api.listPCImages as any).mockResolvedValue([
    { name: "avatar", v: "1" }, { name: "gallery_1", v: "1" },
  ]);
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "Elara" }));
  await screen.findByText("Images");

  expect(screen.getByRole("button", { name: /Description of avatar/ }))
    .toHaveTextContent("Elara in travelling clothes.");
  expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
    .toHaveTextContent("Describe…");

  fireEvent.click(screen.getByRole("button", { name: /Description of gallery_1/ }));
  const box = await screen.findByRole("textbox", { name: /Description of gallery_1/ });
  fireEvent.change(box, { target: { value: "On the road north." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setPCImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "elara", "default", "gallery_1", "On the road north."));
});

test("a focus prop opens that PC on arrival", async () => {
  // A `pcs:` chip — a lore owner, or an item's holder / a group's leader
  // (#222) — has to land on the record, not merely on the PC section.
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" focus="elara" focusNonce={1} />);
  await screen.findByText("a wanderer");
  expect(api.readPC).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara");
});

test("re-following the same chip re-opens it, via the nonce", async () => {
  // The id alone does not change, so a reader who wandered off to another PC
  // and came back would otherwise be sent nowhere.
  const { rerender } = render(
    <PCEditor scope={{ kind: "world", id: "w" }} wid="w" focus="elara" focusNonce={1} />);
  await screen.findByText("a wanderer");
  (api.readPC as any).mockClear();
  rerender(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" focus="elara" focusNonce={2} />);
  await waitFor(() => expect(api.readPC).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara"));
});

test("no focus prop selects nothing", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Elara");            // the rail is populated
  expect(api.readPC).not.toHaveBeenCalled();   // ...but nothing is open
});

test("a scope change closes the open PC rather than leaving it under the new scope", async () => {
  // Reloading only the rail left the previous scope's PC on screen, where its
  // Save and Delete act on the NEW scope — writing to whatever unrelated PC
  // shares the id there, or to nothing.
  const { rerender } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("a wanderer");                       // the record is open
  rerender(<PCEditor scope={{ kind: "world", id: "w2" }} wid="w2" />);
  await waitFor(() => expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "w2" }));
  expect(screen.queryByText("a wanderer")).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("a read still in flight when the scope changes does not land under the new scope", async () => {
  // Clearing `detail` on the scope change is not enough on its own: the read
  // that was already out returns afterwards and sets it straight back, putting
  // the previous scope's PC under the new one — where Edit and Delete act on
  // the NEW scope.
  let resolveRead!: (v: unknown) => void;
  (api.readPC as any).mockReturnValue(new Promise((r) => { resolveRead = r; }));
  const { rerender } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  rerender(<PCEditor scope={{ kind: "world", id: "w2" }} wid="w2" />);
  await waitFor(() => expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "w2" }));
  resolveRead(DETAIL);                       // the old scope's read finally lands
  await waitFor(() => expect(api.listPCs).toHaveBeenCalled());
  expect(screen.queryByText("a wanderer")).toBeNull();
});
