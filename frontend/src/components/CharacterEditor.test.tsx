import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { CharacterEditor } from "./CharacterEditor";

vi.mock("../api/client", () => ({
  api: {
    listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
    updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
    deleteCharacter: vi.fn(), importCharacter: vi.fn(), localizeImages: vi.fn(),
    putImage: vi.fn(), deleteImage: vi.fn(), promoteImage: vi.fn(), importCharacterBook: vi.fn(),
    importCharacterFromChub: vi.fn(),
    setCharacterBirthdate: vi.fn(),
    setCharacterChubSource: vi.fn(), clearCharacterChubSource: vi.fn(),
    downloadCharacterChubGallery: vi.fn(), downloadCharacterChubLorebooks: vi.fn(),
    findChubUnlinked: vi.fn(),
    getCharacterTagline: vi.fn(), setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
  },
}));
import { api } from "../api/client";

const CARD = {
  spec: "chara_card_v3", spec_version: "3.0",
  data: {
    name: "Seraphine", description: "keeper", alternate_greetings: ["hi"], extensions: {},
    character_book: { entries: [{ keys: ["pact"], content: "x" }] },
  },
};
const DETAIL = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
  versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"] }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true, versions: [] }]);
  (api.readCharacter as any).mockResolvedValue(DETAIL);
  (api.createCharacter as any).mockResolvedValue({ character: "rook", version: "default" });
  (api.updateVersion as any).mockResolvedValue({ ok: true });
  (api.importCharacter as any).mockResolvedValue({ character: "imp", version: "default" });
  (api.localizeImages as any).mockImplementation((_w: string, _c: string, _v: string, cb: (e: any) => void) => {
    cb?.({ summary: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false } });
    return Promise.resolve();
  });
  (api.putImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.deleteImage as any).mockResolvedValue({ ok: true });
  (api.promoteImage as any).mockResolvedValue({ ok: true });
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.importCharacterBook as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  (api.setCharacterBirthdate as any).mockResolvedValue({ ok: true });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
});

// reach the edit form: grid -> click a card's Edit button -> form
async function openEditForm() {
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  await screen.findByLabelText("Description");
}

test("grid cards show the tagline under the name", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false,
      tagline: "Keeper of the salt ledgers.", versions: [] },
  ]);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Keeper of the salt ledgers.");
});

test("detail shows the Images shelf with avatar tile, gallery promote, and add tile", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar", "gallery_1"] }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images");
  expect(screen.getByText("avatar")).toBeInTheDocument();               // shelf caption
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith("w", "seraphine", "default", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("detail without avatar shows the dashed placeholder tile", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("no avatar");
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("detail view shows the character tagline", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "A silent snowleopardgirl." });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("A silent snowleopardgirl.");
});

test("edit view saves an edited tagline via PUT", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "old" });
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Tagline");
  fireEvent.change(box, { target: { value: "A new tagline." } });
  fireEvent.click(screen.getByText("Save tagline"));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith("w", "seraphine", "A new tagline."));
});

test("imports an embedded character_book and shows the result", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /import .* lore/i }));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByText(/imported 1/i);
});

test("editing the birthdate persists it on the character", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Birthdate"), { target: { value: "1985-03-14" } });
  await waitFor(() => expect(api.setCharacterBirthdate).toHaveBeenCalledWith("w", "seraphine", "1985-03-14"));
});

test("uploads an avatar for the selected version", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Upload avatar");
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith("w", "seraphine", "default", "avatar", expect.any(File)));
});

test("creating a character prompts and posts the name", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /new character/i }));
  await waitFor(() => expect(api.createCharacter).toHaveBeenCalledWith("w", { name: "Rook" }));
});

test("editing description + alternate greetings (repeatable) saves a rebuilt card", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "cold keeper" } });
  // the seed card has one greeting "hi"; add a second and edit both
  fireEvent.click(screen.getByRole("button", { name: /add greeting/i }));
  const areas = screen.getAllByLabelText(/greeting \d+/i);
  fireEvent.change(areas[0], { target: { value: "line one\nstill one" } });
  fireEvent.change(areas[1], { target: { value: "two" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.description).toBe("cold keeper");
    expect(card.data.alternate_greetings).toEqual(["line one\nstill one", "two"]);
    expect(card.spec).toBe("chara_card_v3"); // preserved
  });
});

test("editing creator and tags saves them", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Creator"), { target: { value: "anon" } });
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "fantasy, oc " } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.creator).toBe("anon");
    expect(card.data.tags).toEqual(["fantasy", "oc"]);
  });
});

test("clicking a card shows read-only details, then Edit opens the form", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine")); // card main -> detail
  await screen.findByRole("heading", { name: "Seraphine" });
  expect(screen.getByText("keeper")).toBeInTheDocument(); // description shown read-only
  expect(screen.queryByLabelText("Description")).toBeNull(); // not the edit form yet
  expect(screen.queryByRole("button", { name: /save version/i })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i })); // detail's Edit
  await screen.findByLabelText("Description"); // now the form
});

test("clicking a character or returning to the list scrolls to the top", async () => {
  const scrollSpy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  render(<CharacterEditor wid="w" />);

  fireEvent.click(await screen.findByText("Seraphine")); // grid -> detail
  await screen.findByRole("heading", { name: "Seraphine" });
  expect(scrollSpy).toHaveBeenCalledWith(0, 0);

  scrollSpy.mockClear();
  fireEvent.click(screen.getByRole("button", { name: /all characters/i })); // detail -> grid
  await screen.findByRole("button", { name: /new character/i });
  expect(scrollSpy).toHaveBeenCalledWith(0, 0);

  scrollSpy.mockClear();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i })); // grid card's Edit -> form
  await screen.findByLabelText("Description");
  expect(scrollSpy).toHaveBeenCalledWith(0, 0);
});

test("a card's Delete button deletes the character", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith("w", "seraphine"));
});

test("bumping resetSignal returns from the editor to the grid", async () => {
  const { rerender } = render(<CharacterEditor wid="w" resetSignal={0} />);
  await openEditForm(); // in the edit form
  rerender(<CharacterEditor wid="w" resetSignal={1} />);
  await screen.findByRole("button", { name: /new character/i }); // back at the grid
  expect(screen.queryByLabelText("Description")).toBeNull();
});

test("importing a .json posts multipart with json format", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["{}"], "c.json")] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json"));
});

test("single import shows the tagline popup with the character's real name", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["{}"], "c.json")] } });
  // openDetail refetches the detail (name "Seraphine"), so the popup uses the real
  // name — not the slugified id returned by the import ("imp").
  await screen.findByText("Tagline for Seraphine");
});

test("saving the import popup refreshes the detail-view tagline", async () => {
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.change(screen.getByLabelText("Import character card"),
    { target: { files: [new File(["{}"], "c.json")] } });
  const box = await screen.findByLabelText("Tagline"); // the popup textarea
  fireEvent.change(box, { target: { value: "A fresh tagline." } });
  fireEvent.click(screen.getByText("Save"));
  // popup closes and the detail view shows the saved tagline (onSaved -> parent state)
  await screen.findByText("A fresh tagline.");
});

test("importing a .png posts multipart with png format", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["x"], "fay.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "png"));
});

test("import card accepts multiple files and imports each", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [
    new File(["{}"], "a.json"),
    new File(["x"], "b.png", { type: "image/png" }),
  ] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledTimes(2));
  expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json");
  expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "png");
});

test("bulk import localizes each imported card", async () => {
  (api.importCharacter as any)
    .mockResolvedValueOnce({ character: "a", version: "default" })
    .mockResolvedValueOnce({ character: "b", version: "default" });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [
    new File(["{}"], "a.json"),
    new File(["x"], "b.png", { type: "image/png" }),
  ] } });
  await waitFor(() => expect(api.localizeImages).toHaveBeenCalledTimes(2));
  expect(api.localizeImages).toHaveBeenCalledWith("w", "a", "default", expect.any(Function));
  expect(api.localizeImages).toHaveBeenCalledWith("w", "b", "default", expect.any(Function));
});

test("focus prop opens that character at the given version", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "rook", name: "Rook", default_version: "v1" },
    versions: [
      { id: "v1", name: "v1", card: CARD, images: [] },
      { id: "v2", name: "v2", card: CARD, images: [] },
    ],
  });
  render(<CharacterEditor wid="w" focus={{ cid: "rook", vid: "v2" }} />);
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith("w", "rook"));
  const active = await screen.findByRole("button", { name: "v2", pressed: true });
  expect(active).toBeInTheDocument();
});

test("import version posts importCharacter into the current character", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Import version");
  fireEvent.change(input, { target: { files: [new File(["{}"], "v.json")] } });
  await waitFor(() =>
    expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json", "seraphine"));
});

test("downloading from a URL runs the full pipeline and shows the summary", async () => {
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default", updated: false,
    gallery: { attempted: 2, stored: 2 },
    lore: { lorebooks_found: 1, created: [{ kind: "lore", id: "x" }] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "https://chub.ai/characters/creator/imp" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "https://chub.ai/characters/creator/imp"));
  // pipeline: localize + embedded lorebook run against the new character
  await waitFor(() => expect(api.localizeImages).toHaveBeenCalledWith("w", "imp", "default", expect.any(Function)));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp", "default"));
  // summary: 1 related entry + 1 embedded entry = 2; localize mock reports 1 localized
  await screen.findByText(/added 1\/1 character · 2 gallery images · 1 image localized · 2 lore entries imported/i);
  // single URL: detail opens (readCharacter mock -> Seraphine) and its tagline prompt queues
  await screen.findByText("Tagline for Seraphine");
});

test("cancelling the URL modal makes no API call", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
  expect(api.importCharacterFromChub).not.toHaveBeenCalled();
});

test("bulk URL import pipelines every URL and queues tagline prompts", async () => {
  (api.importCharacterFromChub as any)
    .mockResolvedValueOnce({ character: "imp1", version: "default", updated: false,
      gallery: { attempted: 1, stored: 1 }, lore: { lorebooks_found: 0, created: [] } })
    .mockResolvedValueOnce({ character: "imp2", version: "default", updated: false,
      gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  (api.readCharacter as any).mockImplementation((_w: string, cid: string) => Promise.resolve({
    meta: { id: cid, name: cid === "imp1" ? "Imp One" : "Imp Two", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  }));
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "creator/one\n\ncreator/two\n" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.importCharacterFromChub).toHaveBeenCalledTimes(2));
  expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/one");
  expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/two");
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp1", "default"));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp2", "default"));
  // tagline prompts drain one at a time; Skip advances to the next character
  await screen.findByText("Tagline for Imp One");
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText("Tagline for Imp Two");
});

test("a failing URL is reported in the summary and the rest still import", async () => {
  (api.importCharacterFromChub as any)
    .mockRejectedValueOnce({ detail: "could not fetch a character card from that URL" })
    .mockResolvedValueOnce({ character: "imp2", version: "default", updated: false,
      gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: "bad/url\ncreator/two" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await screen.findByText(/added 1\/2 characters.*failed — bad\/url: could not fetch/i);
  // the good URL still went through the pipeline
  expect(api.localizeImages).toHaveBeenCalledWith("w", "imp2", "default", expect.any(Function));
});

test("a mid-pipeline failure still finishes the character's remaining steps", async () => {
  (api.localizeImages as any).mockRejectedValueOnce({ detail: "boom" });
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default", updated: false,
    gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"), { target: { value: "creator/imp" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "imp", "default"));
  await screen.findByText(/added 1\/1 character.*failed — .*localize failed/i);
});

test("checking chub.ai links lists unlinked versions and jumps to one on click", async () => {
  (api.findChubUnlinked as any).mockResolvedValue({
    versions: [
      { character: "seraphine", character_name: "Seraphine", version: "futa", version_name: "Seraphine (futa)" },
    ],
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /check chub\.ai links/i }));
  await waitFor(() => expect(api.findChubUnlinked).toHaveBeenCalledWith("w"));
  await screen.findByText(/1 version not linked to chub\.ai/i);

  fireEvent.click(screen.getByRole("button", { name: /seraphine \(futa\)/i }));
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith("w", "seraphine"));
  await screen.findByRole("heading", { name: "Seraphine" }); // jumped to detail
});

test("checking chub.ai links with none unlinked says so", async () => {
  (api.findChubUnlinked as any).mockResolvedValue({ versions: [] });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /check chub\.ai links/i }));
  await screen.findByText(/^all versions are linked to chub\.ai$/i);
});

test("downloading a version from a URL targets the open character and version", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/imp-variant");
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "seraphine", version: "variant", updated: false,
    gallery: { attempted: 0, stored: 0 },
    lore: { lorebooks_found: 0, created: [] },
  });
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /download version from url/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/imp-variant", "seraphine", "default"));
  await screen.findByText(/^downloaded from url$/i);
});

test("re-downloading an already-linked version updates it in place instead of creating a new one", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/imp");
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "seraphine", version: "default", updated: true,
    gallery: { attempted: 0, stored: 0 },
    lore: { lorebooks_found: 0, created: [] },
  });
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /download version from url/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/imp", "seraphine", "default"));
  await screen.findByText(/^updated this version from url$/i);
});

test("linking a character to a URL from the detail page shows a clickable link and allows unlinking", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/imp");
  (api.setCharacterChubSource as any).mockResolvedValue({ chub_source: "creator/imp" });
  (api.clearCharacterChubSource as any).mockResolvedValue({ chub_source: "" });
  (api.readCharacter as any)
    .mockResolvedValueOnce(DETAIL) // initial detail open
    .mockResolvedValueOnce({
      ...DETAIL,
      versions: [{ ...DETAIL.versions[0], chub_source: "creator/imp" }],
    }); // after linking

  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine")); // card main -> detail (read-only)
  await screen.findByRole("heading", { name: "Seraphine" });
  expect(screen.queryByRole("link", { name: /creator\/imp/i })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /^link to url$/i }));
  await waitFor(() =>
    expect(api.setCharacterChubSource).toHaveBeenCalledWith("w", "seraphine", "default", "creator/imp"));
  const link = await screen.findByRole("link", { name: /creator\/imp/i });
  expect(link).toHaveAttribute("href", "https://chub.ai/characters/creator/imp");

  (api.readCharacter as any).mockResolvedValueOnce(DETAIL); // after unlinking, reverts
  fireEvent.click(screen.getByRole("button", { name: /^unlink$/i }));
  await waitFor(() => expect(api.clearCharacterChubSource).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByRole("button", { name: /^link to url$/i });
});

test("linking to a direct (non-chub) URL uses it as the href directly", async () => {
  (api.setCharacterChubSource as any).mockResolvedValue({ chub_source: "https://example.com/card.png" });
  vi.spyOn(window, "prompt").mockReturnValue("https://example.com/card.png");
  (api.readCharacter as any)
    .mockResolvedValueOnce(DETAIL)
    .mockResolvedValueOnce({
      ...DETAIL,
      versions: [{ ...DETAIL.versions[0], chub_source: "https://example.com/card.png", is_chub: false }],
    });

  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /^link to url$/i }));
  const link = await screen.findByRole("link", { name: /example\.com\/card\.png/i });
  expect(link).toHaveAttribute("href", "https://example.com/card.png"); // used as-is, not prefixed with chub.ai
  // not a chub.ai link, so the chub-only actions don't show
  expect(screen.queryByRole("button", { name: /download gallery/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /download linked lorebooks/i })).toBeNull();
});

test("a sibling version doesn't show another version's chub.ai link", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "default", card: CARD, images: [], chub_source: "creator/main" },
      { id: "variant", name: "variant", card: CARD, images: [] },
    ],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("link", { name: /creator\/main/i });

  fireEvent.click(screen.getByRole("button", { name: "variant" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: /creator\/main/i })).toBeNull());
  await screen.findByRole("button", { name: /^link to url$/i });
});

test("download gallery/lorebooks buttons only appear once a version is linked", async () => {
  render(<CharacterEditor wid="w" />); // DETAIL's only version has no chub_source
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("button", { name: /^link to url$/i });
  expect(screen.queryByRole("button", { name: /download gallery/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /download linked lorebooks/i })).toBeNull();
});

test("downloading the gallery for a linked version shows per-image progress then the result", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [], chub_source: "creator/imp", is_chub: true }],
  });
  let emit: (e: any) => void = () => {};
  let resolveDownload: () => void = () => {};
  (api.downloadCharacterChubGallery as any).mockImplementation(
    (_w: string, _c: string, _v: string, cb: (e: any) => void) => {
      emit = cb;
      return new Promise<void>((resolve) => { resolveDownload = resolve; });
    },
  );
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /download gallery/i }));
  await waitFor(() =>
    expect(api.downloadCharacterChubGallery).toHaveBeenCalledWith("w", "seraphine", "default", expect.any(Function)));

  act(() => emit({ total: 3 }));
  act(() => emit({ done: 1, total: 3 }));
  await screen.findByText("1/3");
  act(() => emit({ done: 2, total: 3 }));
  await screen.findByText("2/3");

  act(() => emit({ summary: { attempted: 3, stored: 2 } }));
  await act(async () => resolveDownload());
  await screen.findByText(/^2\/3 gallery images downloaded$/i);
  expect(screen.queryByText("2/3")).toBeNull(); // progress bar gone once finished
});

test("gallery images render as thumbnails, sorted numerically, opening full-size in a new tab", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{
      id: "default", name: "default", card: CARD,
      images: ["avatar", "gallery_10", "gallery_2", "gallery_0"], chub_source: "creator/imp",
    }],
  });
  const { container } = render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images");

  const thumbs = Array.from(
    container.querySelectorAll<HTMLImageElement>(".images-shelf .shelf-tile:not(.avatar-tile) img"));
  expect(thumbs).toHaveLength(3);
  // numeric order, not lexicographic ("gallery_10" must not sort before "gallery_2")
  expect(thumbs.map((t) => t.src)).toEqual([
    "http://localhost:3000/img/w/seraphine/default/gallery_0?v=0",
    "http://localhost:3000/img/w/seraphine/default/gallery_2?v=0",
    "http://localhost:3000/img/w/seraphine/default/gallery_10?v=0",
  ]);
  const links = thumbs.map((t) => t.closest("a"));
  expect(links.every((a) => a?.getAttribute("target") === "_blank")).toBe(true);
  expect(links[0]).toHaveAttribute("href", "/img/w/seraphine/default/gallery_0?v=0");
});

test("no gallery section when a version has no gallery images", async () => {
  render(<CharacterEditor wid="w" />); // DETAIL's only version has images: ["avatar"]
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("heading", { name: "Seraphine" });
  expect(screen.queryByText("Gallery")).toBeNull();
});

test("gallery images downloaded while viewing a character appear without navigating away", async () => {
  (api.readCharacter as any)
    .mockResolvedValueOnce({
      ...DETAIL, versions: [{ ...DETAIL.versions[0], chub_source: "creator/imp", is_chub: true }],
    })
    .mockResolvedValueOnce({
      ...DETAIL,
      versions: [{ ...DETAIL.versions[0], chub_source: "creator/imp", is_chub: true, images: ["gallery_0"] }],
    });
  (api.downloadCharacterChubGallery as any).mockImplementation(
    (_w: string, _c: string, _v: string, cb: (e: any) => void) => {
      cb({ summary: { attempted: 1, stored: 1 } });
      return Promise.resolve();
    },
  );
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images");
  expect(screen.queryByRole("button", { name: /set as avatar/i })).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: /^download gallery$/i }));
  await screen.findByRole("button", { name: /set as avatar/i }); // gallery_0 tile appeared
});

test("downloading linked lorebooks for a version with none shows a clear empty result", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [], chub_source: "creator/imp", is_chub: true }],
  });
  (api.downloadCharacterChubLorebooks as any).mockResolvedValue({ lorebooks_found: 0, created: [] });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /download linked lorebooks/i }));
  await waitFor(() =>
    expect(api.downloadCharacterChubLorebooks).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByText(/^no linked lorebooks found on chub\.ai$/i);
});

test("the edit form no longer shows a link control (moved to the detail page)", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  expect(screen.queryByRole("button", { name: /^link to url$/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /^unlink$/i })).toBeNull();
});
