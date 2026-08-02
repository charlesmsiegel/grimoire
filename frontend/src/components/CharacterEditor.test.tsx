import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { CharacterEditor } from "./CharacterEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
      actorImageUrl: (sc: { id: string }, c: string, v: string, n: string) => `/img/${sc.id}/${c}/${v}/${n}`,
      listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
      updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
      deleteCharacter: vi.fn(), importCharacter: vi.fn(), localizeImages: vi.fn(),
      putImage: vi.fn(), deleteImage: vi.fn(), promoteImage: vi.fn(), setAvatarFocus: vi.fn(),
      importCharacterBook: vi.fn(),
      importCharacterFromChub: vi.fn(),
      setCharacterBirthdate: vi.fn(), getCalendarMonths: vi.fn(),
      setCharacterChubSource: vi.fn(), clearCharacterChubSource: vi.fn(),
      downloadCharacterChubGallery: vi.fn(), downloadCharacterChubLorebooks: vi.fn(),
      findChubUnlinked: vi.fn(),
      getCharacterTagline: vi.fn(), setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
      getCharacterVoiceAnchor: vi.fn(), setCharacterVoiceAnchor: vi.fn(),
      generateCharacterVoiceAnchor: vi.fn(),
      listImageAppearances: vi.fn(), copyGreetingImage: vi.fn(), listGreetings: vi.fn(),
      imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
      putSheetCreation: vi.fn(),
    },
  };
});
import { api } from "../api/client";

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
  (api.setAvatarFocus as any).mockResolvedValue({ ok: true });
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.importCharacterBook as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  (api.setCharacterBirthdate as any).mockResolvedValue({ ok: true });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "" });
  (api.listImageAppearances as any).mockResolvedValue([]);
  (api.copyGreetingImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.listGreetings as any).mockResolvedValue([]);
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Keeper of the salt ledgers.");
});

test("detail shows the Images shelf with avatar tile, gallery promote, and add tile", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar", "gallery_1"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images");
  expect(screen.getByText("avatar")).toBeInTheDocument();               // shelf caption
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("detail without avatar shows the dashed placeholder tile", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("no avatar");
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("detail view shows the character tagline", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "A silent snowleopardgirl." });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("A silent snowleopardgirl.");
});

test("detail view shows the suggested image prompt when set", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ id: "default", name: "default",
      card: { ...CARD, data: { ...CARD.data, extensions: { sd_prompt: "an old innkeeper, weathered face" } } },
      images: ["avatar"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Image prompt");
  expect(screen.getByText("an old innkeeper, weathered face")).toBeInTheDocument();
});

test("detail view omits the image prompt section when unset", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />); // DETAIL's CARD has extensions: {}
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images"); // wait for the detail view to settle
  expect(screen.queryByText("Image prompt")).toBeNull();
});

test("edit view saves an edited tagline via PUT", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "old" });
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Tagline");
  fireEvent.change(box, { target: { value: "A new tagline." } });
  fireEvent.click(screen.getByText("Save tagline"));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith("w", "seraphine", "A new tagline."));
});

test("imports an embedded character_book and shows the result", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /import .* lore/i }));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByText(/imported 1/i);
});

test("editing the birthdate persists it on the character", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(await screen.findByLabelText("Birthdate year"), { target: { value: "1990" } });
  const monthSelect = await screen.findByLabelText("Birthdate month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "06" } });
  const daySelect = screen.getByLabelText("Birthdate day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "29" } });
  await waitFor(() => expect(api.setCharacterBirthdate).toHaveBeenCalledWith("w", "seraphine", "1990-06-29"));
  // intermediate picker states (year-only, month change clearing the day) must
  // never blank the stored birthdate
  expect(api.setCharacterBirthdate).not.toHaveBeenCalledWith("w", "seraphine", "");
});

test("Clear removes a stored birthdate", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL, meta: { ...DETAIL.meta, birthdate: "1985-03-14" },
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(await screen.findByRole("button", { name: "Clear" }));
  await waitFor(() => expect(api.setCharacterBirthdate).toHaveBeenCalledWith("w", "seraphine", ""));
  expect(api.setCharacterBirthdate).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Birthdate year")).toHaveValue(null);      // fields emptied
  expect(screen.getByLabelText("Birthdate month")).toHaveValue("");
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();     // nothing left to clear
});

test("uploads an avatar for the selected version", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Upload avatar");
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", "avatar", expect.any(File)));
});

test("creating a character prompts and posts the name", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /new character/i }));
  await waitFor(() => expect(api.createCharacter).toHaveBeenCalledWith("w", { name: "Rook" }));
});

test("editing description + alternate greetings (repeatable) saves a rebuilt card", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith("w", "seraphine"));
});

test("bumping resetSignal returns from the editor to the grid", async () => {
  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" resetSignal={0} />);
  await openEditForm(); // in the edit form
  rerender(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" resetSignal={1} />);
  await screen.findByRole("button", { name: /new character/i }); // back at the grid
  expect(screen.queryByLabelText("Description")).toBeNull();
});

test("importing a .json posts multipart with json format", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["{}"], "c.json")] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json"));
});

test("single import shows the tagline popup with the character's real name", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["{}"], "c.json")] } });
  // openDetail refetches the detail (name "Seraphine"), so the popup uses the real
  // name — not the slugified id returned by the import ("imp").
  await screen.findByText("Tagline for Seraphine");
});

test("saving the import popup refreshes the detail-view tagline", async () => {
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character card");
  fireEvent.change(input, { target: { files: [new File(["x"], "fay.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "png"));
});

test("import card accepts multiple files and imports each", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" focus={{ cid: "rook", vid: "v2" }} />);
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith({ kind: "world", id: "w" }, "rook"));
  const active = await screen.findByRole("button", { name: "v2", pressed: true });
  expect(active).toBeInTheDocument();
});

test("import version posts importCharacter into the current character", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  fireEvent.change(screen.getByLabelText("Tagline"), { target: { value: "typed for Imp One" } });
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText("Tagline for Imp Two");
  // the box starts empty for each character — no leftover text from the previous one
  expect(screen.getByLabelText("Tagline")).toHaveValue("");
});

test("a failing URL is reported in the summary and the rest still import", async () => {
  (api.importCharacterFromChub as any)
    .mockRejectedValueOnce({ detail: "could not fetch a character card from that URL" })
    .mockResolvedValueOnce({ character: "imp2", version: "default", updated: false,
      gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /check chub\.ai links/i }));
  await waitFor(() => expect(api.findChubUnlinked).toHaveBeenCalledWith("w"));
  await screen.findByText(/1 version not linked to chub\.ai/i);

  fireEvent.click(screen.getByRole("button", { name: /seraphine \(futa\)/i }));
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine"));
  await screen.findByRole("heading", { name: "Seraphine" }); // jumped to detail
});

test("checking chub.ai links with none unlinked says so", async () => {
  (api.findChubUnlinked as any).mockResolvedValue({ versions: [] });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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

  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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

  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("link", { name: /creator\/main/i });

  fireEvent.click(screen.getByRole("button", { name: "variant" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: /creator\/main/i })).toBeNull());
  await screen.findByRole("button", { name: /^link to url$/i });
});

test("download gallery/lorebooks buttons only appear once a version is linked", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />); // DETAIL's only version has no chub_source
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  const { container } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />); // DETAIL's only version has images: ["avatar"]
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /download linked lorebooks/i }));
  await waitFor(() =>
    expect(api.downloadCharacterChubLorebooks).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByText(/^no linked lorebooks found on chub\.ai$/i);
});

test("the edit form no longer shows a link control (moved to the detail page)", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  expect(screen.queryByRole("button", { name: /^link to url$/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /^unlink$/i })).toBeNull();
});


test("grid cards show gallery/localized/greeting badges only when nonzero", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "a", name: "Aya", default_version: "default", has_avatar: true,
      gallery_count: 3, localized_count: 0, greeting_count: 1, versions: [] },
    { id: "b", name: "Bea", default_version: "default", has_avatar: true,
      gallery_count: 0, localized_count: 2, greeting_count: 18, versions: [] },
    { id: "c", name: "Cyn", default_version: "default", has_avatar: true,
      gallery_count: 0, localized_count: 0, greeting_count: 0, versions: [] },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("3 gallery");
  await screen.findByText("2 localized");
  await screen.findByText("1 greeting");
  await screen.findByText("18 greetings");
  expect(screen.queryByText("0 gallery")).toBeNull();
  expect(screen.queryByText("0 localized")).toBeNull();
  expect(screen.queryByText(/0 greeting/)).toBeNull();
});


test("Re-download uses the stored chub link without prompting", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"],
                 chub_source: "https://chub.ai/characters/creator/seraphine", is_chub: true }],
  });
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "seraphine", version: "default", updated: true,
    gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] },
  });
  const promptSpy = vi.spyOn(window, "prompt");
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /re-download/i }));
  await waitFor(() => expect(api.importCharacterFromChub).toHaveBeenCalledWith(
    "w", "https://chub.ai/characters/creator/seraphine", "seraphine", "default"));
  expect(promptSpy).not.toHaveBeenCalled();
});


test("greeting scene labels are demoted and single newlines keep line breaks", async () => {
  const card = {
    ...CARD,
    data: {
      ...CARD.data,
      first_mes: "#Rooftop Setting#\n\nFirst line\nSecond line",
      alternate_greetings: ["#Alt Scene#\n\nalt body"],
    },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: ["avatar"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  // `#Scene Label#` lines become a small scene label (trailing # stripped), not an h1
  await screen.findByText("Rooftop Setting");
  expect(screen.queryByRole("heading", { name: /rooftop setting/i })).toBeNull();
  expect(screen.getByText("Alt Scene")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /alt scene/i })).toBeNull();
  // a single \n inside a paragraph renders as a line break, not a collapsed space
  expect(document.querySelector(".detail-rendered br")).not.toBeNull();
});


test("first message and alternate greetings render markdown images; other fields stay plain", async () => {
  const card = {
    ...CARD,
    data: {
      ...CARD.data,
      first_mes: "hello ![scene](/img/w/seraphine/default/embed-abc)",
      alternate_greetings: ["alt ![alt-pic](/img/w/seraphine/default/embed-def)"],
      description: "plain **stars** stay literal",
    },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: ["avatar"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("img", { name: "scene" });
  await screen.findByRole("img", { name: "alt-pic" });
  expect(screen.getByText("plain **stars** stay literal")).toBeInTheDocument();
});


test("clicking the profile avatar opens the crop picker and saves the focus", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], avatar_focus: null }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /adjust avatar crop/i }));
  const slider = await screen.findByLabelText("Crop position");
  fireEvent.change(slider, { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", 80));
});

test("stored focus is applied as object-position on detail and grid avatars", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true,
      avatar_focus: 25, versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], avatar_focus: 25 }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const cardImg = document.querySelector(".char-card-avatar") as HTMLElement;
  expect(cardImg.style.objectPosition).toBe("25% 25%");
  fireEvent.click(screen.getByText("Seraphine"));
  await screen.findByRole("button", { name: /adjust avatar crop/i });
  const detailImg = document.querySelector(".detail-avatar") as HTMLElement;
  expect(detailImg.style.objectPosition).toBe("25% 25%");
});


test("creator notes render inside a sandboxed iframe", async () => {
  const card = {
    ...CARD,
    data: { ...CARD.data, creator_notes: "<style>body{color:red}</style><b>fancy</b> note" },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: [] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  const frame = await screen.findByTitle("Creator notes");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
  expect(frame.getAttribute("srcdoc")).toContain("<b>fancy</b> note");
});

test("plain-text creator notes keep line breaks via pre-wrap", async () => {
  const card = { ...CARD, data: { ...CARD.data, creator_notes: "line one\nline two" } };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: [] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  const frame = await screen.findByTitle("Creator notes");
  expect(frame.getAttribute("srcdoc")).toContain("white-space:pre-wrap");
  expect(frame.getAttribute("srcdoc")).toContain("line one\nline two");
});

test("appears-in gallery copies to avatar and world greetings link with primary star", async () => {
  (api.listImageAppearances as any).mockResolvedValue([
    { gid: "sol-1", greeting_name: "SoL 1", name: "embed-a", url: "/api/worlds/w/greetings/sol-1/images/embed-a" },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-1", name: "SoL 1", character: "seraphine", version: "main", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["seraphine", "other"], requires_tags: [], predecessor_join: "all" },
    { id: "sol-3", name: "SoL 3", character: "other", version: "main", present: ["other"], requires_tags: [], predecessor_join: "all" },
  ]);
  const onOpenGreeting = vi.fn();
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" onOpenGreeting={onOpenGreeting} />);
  fireEvent.click(await screen.findByText("Seraphine"));

  // appears-in strip: copy to avatar
  const label = await screen.findByText("Appears in");
  const strip = label.parentElement as HTMLElement;
  fireEvent.click(within(strip).getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.copyGreetingImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", { gid: "sol-1", name: "embed-a", slot: "avatar" }));
  expect(within(strip).getByRole("button", { name: /add to gallery/i })).toBeInTheDocument();

  // world greetings: present-only listed, primary starred, absent one missing
  const wg = screen.getByText("World greetings").parentElement as HTMLElement;
  expect(within(wg).getByText(/★\s*SoL 1/)).toBeInTheDocument();
  expect(within(wg).getByText("SoL 2")).toBeInTheDocument();
  expect(within(wg).queryByText(/SoL 3/)).toBeNull();
  fireEvent.click(within(wg).getByText("SoL 2"));
  expect(onOpenGreeting).toHaveBeenCalledWith("sol-2");
});


test("campaign scope: hides world-only tooling and uses campaign image URLs", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", has_avatar: true, versions: [] },
  ]);
  const { container } = render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByRole("button", { name: "Import card" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Download from URL" })).toBeNull();
  expect(screen.queryByRole("button", { name: "+ New character" })).toBeNull();
  const img = container.querySelector("img.char-card-avatar")!;
  expect(img.getAttribute("src")).toContain("/img/run/mara/");
});

test("campaign scope: picking a version calls pickVersion", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockImplementation(async (scope: any) => ({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [
      { id: "young", name: "Young", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
      { id: "veteran", name: "Veteran", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
    ],
  }));
  (api.listAppearances as any).mockResolvedValue([]);
  (api.pickVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  fireEvent.click(await screen.findByRole("button", { name: "Pick this version" }));
  await waitFor(() => expect(api.pickVersion).toHaveBeenCalledWith("run", "characters", "mara", "young"));
});


test("campaign scope: the avatar crop control mutates the campaign's own copy", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: ["avatar"], avatar_focus: null,
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  fireEvent.click(await screen.findByRole("button", { name: /adjust avatar crop/i }));
  const slider = await screen.findByLabelText("Crop position");
  fireEvent.change(slider, { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", 80));
});

test("campaign scope: uploading an avatar calls the scope-aware endpoint", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Upload avatar");
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", "avatar", expect.any(File)));
});

test("campaign scope: gallery shelf allows adding an image and promoting to avatar", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: ["avatar", "gallery_1"],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  await screen.findByText("Images");
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("appears-in tiles render the thumbnail and link to the full image", async () => {
  (api.listImageAppearances as any).mockResolvedValue([
    { gid: "sol-1", greeting_name: "SoL 1", name: "embed-a",
      url: "/api/worlds/w/greetings/sol-1/images/embed-a?v=abc",
      thumb: "/api/worlds/w/greetings/sol-1/images/embed-a?w=320&v=abc" },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  const img = await screen.findByAltText("SoL 1 art");
  expect(img.getAttribute("src")).toBe("/api/worlds/w/greetings/sol-1/images/embed-a?w=320&v=abc");
  expect(img.closest("a")!.getAttribute("href")).toBe("/api/worlds/w/greetings/sol-1/images/embed-a?v=abc");
});

it("shows a wizard trigger when the module has a characters sheet type", async () => {
  (api.listCharacters as any).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<CharacterEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  await screen.findByText("+ New character with sheet…");
});

it("wires the wizard's deleteRecord to api.deleteCharacter (always wid-scoped) so a failed sheet write rolls back", async () => {
  (api.listCharacters as any).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  (api.createCharacter as any).mockResolvedValue({ character: "rook", version: "default" });
  (api.putSheetCreation as any).mockRejectedValue({ detail: "nope" });
  render(<CharacterEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New character with sheet…"));
  await screen.findByText("New character (with sheet)");

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Rook" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));

  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith("w1", "rook"));
});

it("a wizard opened at world scope closes (not just its trigger) when the same instance's scope changes to campaign", async () => {
  // Regression for a Codex finding: the button-level gate on "+ New character with
  // sheet..." isn't enough on its own -- if a parent reuses this component instance
  // across a scope change (no remount) instead of opening it fresh at campaign scope,
  // a wizard already open from world scope must not survive the transition (it would
  // otherwise keep the campaign scope, sending a sheet write to the wrong endpoint for
  // a world-level character id). Proves both the render-path gate
  // (wizardOpen && module && worldScope) and the scope-change reset effect, using
  // rerender (not a fresh render) so the instance genuinely persists.
  (api.listCharacters as any).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "w1" }} wid="w1" module={module} />);
  fireEvent.click(await screen.findByText("+ New character with sheet…"));
  expect(await screen.findByText("New character (with sheet)")).toBeInTheDocument();     // wizard open

  rerender(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w1" module={module} />);
  await waitFor(() => expect(screen.queryByText("New character (with sheet)")).toBeNull()); // wizard closed
  expect(screen.getByText("No characters yet. Create one or import a card.")).toBeInTheDocument(); // plain view instead
});

test("edit view loads and saves a voice anchor via PUT (#59)", async () => {
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  (api.setCharacterVoiceAnchor as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Voice anchor");
  await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe("Clipped."));
  fireEvent.change(box, { target: { value: "Never uses contractions." } });
  fireEvent.click(screen.getByText("Save voice anchor"));
  await waitFor(() => expect(api.setCharacterVoiceAnchor)
    .toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "Never uses contractions."));
});

test("clearing the voice anchor opts the character out of drift detection (#59)", async () => {
  // A blank PUT is the documented way to remove the anchor, and removing it is
  // what stops absorb from judging this character at all.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  (api.setCharacterVoiceAnchor as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Voice anchor");
  fireEvent.change(box, { target: { value: "   " } });
  fireEvent.click(screen.getByText("Save voice anchor"));
  await waitFor(() => expect(api.setCharacterVoiceAnchor).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", ""));
});

test("Generate previews a voice anchor without persisting it (#59)", async () => {
  (api.generateCharacterVoiceAnchor as any)
    .mockResolvedValue({ voice_anchor: "Clipped. Never uses contractions." });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByLabelText("Voice anchor");
  const actions = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));
  await waitFor(() => expect((screen.getByLabelText("Voice anchor") as HTMLTextAreaElement).value)
    .toBe("Clipped. Never uses contractions."));
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();
});

test("saving the card version does not discard an anchor draft (#59)", async () => {
  // `select()` is the refresh every other save runs, and none of them touch the
  // anchor -- so reloading it unconditionally threw away a draft whenever the
  // user edited the card and the anchor in one sitting and saved the card first.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "The stored one." });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByLabelText("Voice anchor");

  fireEvent.change(screen.getByLabelText("Voice anchor"),
                   { target: { value: "A draft I have not saved." } });
  fireEvent.click(screen.getByText("Save version"));

  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect((screen.getByLabelText("Voice anchor") as HTMLTextAreaElement).value)
    .toBe("A draft I have not saved.");
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();   // still unsaved, not written
});

test("a draft typed DURING the card save survives it too (#59)", async () => {
  // The draft-preserving check above ran on values closed over when the click
  // handler was created. A draft typed while `updateVersion` is in flight is
  // not in that snapshot, so the guard read the pre-save text, found it equal to
  // the loaded anchor, and reloaded over the draft anyway -- the same bug one
  // keystroke later.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "The stored one." });
  let finishSave!: () => void;
  (api.updateVersion as any).mockReturnValue(new Promise((res) => { finishSave = () => res({ ok: true }); }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByLabelText("Voice anchor");

  fireEvent.click(screen.getByText("Save version"));            // anchor still pristine here
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Voice anchor"),
                   { target: { value: "Typed while the card was saving." } });
  await act(async () => { finishSave(); });

  expect((screen.getByLabelText("Voice anchor") as HTMLTextAreaElement).value)
    .toBe("Typed while the card was saving.");
});

test("a pending anchor load cannot be saved as a blank opt-out (#59)", async () => {
  // A blank anchor PUT DELETES it. "" is also the placeholder shown while the
  // GET is in flight, so an enabled Save button would let a fast click wipe a
  // stored anchor the user never saw.
  let release!: (v: { voice_anchor: string }) => void;
  (api.getCharacterVoiceAnchor as any).mockReturnValue(
    new Promise((res) => { release = res; }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const save = await screen.findByText("Save voice anchor");
  expect((save as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(save);
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();
  await act(async () => { release({ voice_anchor: "Clipped." }); });
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
});

test("a failed anchor load disables saving instead of offering a blank (#59)", async () => {
  (api.getCharacterVoiceAnchor as any).mockRejectedValue({ detail: "boom" });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const save = await screen.findByText("Save voice anchor");
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(true));
  expect(screen.getByText(/Could not load the voice anchor/)).toBeTruthy();
  fireEvent.click(save);
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();
});

test("a scope change closes the open character rather than re-aiming it (#59)", async () => {
  // `detail.meta.id` is combined with the CURRENT scope on every write, so a
  // character left open across a scope change addresses the new world by the
  // old one's id -- and where that id also exists there, Save lands on the
  // wrong record.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "World A's anchor." });
  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "a" }} wid="a" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  expect((await screen.findByLabelText("Voice anchor") as HTMLTextAreaElement).value)
    .toBe("World A's anchor.");

  rerender(<CharacterEditor scope={{ kind: "world", id: "b" }} wid="b" />);
  await waitFor(() => expect(screen.queryByLabelText("Voice anchor")).toBeNull());
  expect(screen.queryByText("Save voice anchor")).toBeNull();
});

test("a character read still in flight at a scope change does not reopen it (#59)", async () => {
  // The scope effect clears the open character so a stale id cannot be combined
  // with the new scope on a write. A read that was ALREADY in flight puts it
  // straight back: the continuation installs world A's record while the editor
  // renders under world B, and the next save -- the anchor PUT included --
  // addresses B by A's id. Closing the editor is not enough; the late reply has
  // to be dropped.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "World A's anchor." });
  let release!: (v: typeof DETAIL) => void;
  (api.readCharacter as any).mockReturnValue(new Promise((res) => { release = res; }));
  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "a" }} wid="a" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  rerender(<CharacterEditor scope={{ kind: "world", id: "b" }} wid="b" />);
  await act(async () => { release(DETAIL); });

  expect(screen.queryByLabelText("Voice anchor")).toBeNull();
  expect(screen.queryByLabelText("Description")).toBeNull();
});

test("an anchor save abandoned by navigation cannot unblock a later one (#59)", async () => {
  // The single-flight check is not enough on its own. Leaving the character
  // clears `anchorSaving`, so the next save is free to start while the first
  // PUT is still open -- and the first one's `finally` would then clear the
  // flag out from under it, letting a THIRD start. Two writes for the same
  // character overlap, the slower wins the file, and the editor shows the
  // newer text.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
    { id: "mara", name: "Mara", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "A" });
  const releases: (() => void)[] = [];
  (api.setCharacterVoiceAnchor as any).mockImplementation(
    () => new Promise<void>((res) => { releases.push(() => res()); }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  await screen.findByLabelText("Voice anchor");
  fireEvent.click(screen.getByText("Save voice anchor"));      // save #1, left in flight

  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  fireEvent.click(screen.getByText("‹ All characters"));
  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[1]);
  await screen.findByLabelText("Voice anchor");

  fireEvent.change(screen.getByLabelText("Voice anchor"), { target: { value: "Mara's voice." } });
  fireEvent.click(screen.getByText("Save voice anchor"));      // save #2, for Mara
  await waitFor(() => expect(api.setCharacterVoiceAnchor).toHaveBeenCalledTimes(2));
  expect((screen.getByText("Saving…") as HTMLButtonElement).disabled).toBe(true);

  await act(async () => { releases[0](); });                   // save #1 lands, late
  expect(screen.queryByText("Save voice anchor")).toBeNull();  // #2 still holds the lock
  expect((screen.getByText("Saving…") as HTMLButtonElement).disabled).toBe(true);

  await act(async () => { releases[1](); });                   // ...and only #2 releases it
  await waitFor(() => expect(screen.getByText("Save voice anchor")).toBeTruthy());
});

test("a second anchor save cannot start while the first is in flight (#59)", async () => {
  // Two overlapping PUTs race on the server and the SLOWER one wins the file,
  // so an edit made between them can be discarded while the editor still shows
  // it. The writes are whole-value, so blocking the second click is enough.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "A" });
  let release!: () => void;
  (api.setCharacterVoiceAnchor as any).mockReturnValue(
    new Promise<void>((res) => { release = res; }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  await screen.findByLabelText("Voice anchor");
  const save = screen.getByText("Save voice anchor") as HTMLButtonElement;
  fireEvent.click(save);

  // edit to B and try again while A's PUT is still open
  await waitFor(() => expect(screen.getByText("Saving…")).toBeTruthy());
  fireEvent.change(screen.getByLabelText("Voice anchor"), { target: { value: "B" } });
  fireEvent.click(screen.getByText("Saving…"));
  expect(api.setCharacterVoiceAnchor).toHaveBeenCalledTimes(1);

  await act(async () => { release(); });
  await waitFor(() => expect(screen.getByText("Save voice anchor")).toBeTruthy());
});

test("Save is disabled while an anchor generation is in flight (#59)", async () => {
  // A save that lands mid-generation persists the OLD text, and the completion
  // then swaps the textarea for a fresh draft -- so the save the user watched
  // succeed covers a value that is no longer on screen.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "The loaded one." });
  let release!: (v: { voice_anchor: string }) => void;
  (api.generateCharacterVoiceAnchor as any).mockReturnValue(
    new Promise((res) => { release = res; }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  await screen.findByLabelText("Voice anchor");
  const save = screen.getByText("Save voice anchor") as HTMLButtonElement;
  expect(save.disabled).toBe(false);

  const actions = save.closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));
  await waitFor(() => expect(save.disabled).toBe(true));
  fireEvent.click(save);
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();

  await act(async () => { release({ voice_anchor: "The fresh draft." }); });
  await waitFor(() => expect(save.disabled).toBe(false));
});

test("a generated anchor for a character you navigated away from is dropped (#59)", async () => {
  // Otherwise A's draft lands in B's textarea, and Save writes it under B --
  // saving reads the CURRENT detail id, not the one generation started on.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
    { id: "mara", name: "Mara", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "The loaded one." });
  let release!: (v: { voice_anchor: string }) => void;
  (api.generateCharacterVoiceAnchor as any).mockReturnValue(
    new Promise((res) => { release = res; }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

  // open Seraphine's edit form and start a generation
  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  await screen.findByLabelText("Voice anchor");
  const actions = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));

  // navigate away while the draft is still in flight
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  fireEvent.click(screen.getByText("‹ All characters"));
  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[1]);
  await screen.findByLabelText("Voice anchor");

  await act(async () => { release({ voice_anchor: "The stale draft." }); });
  const box = await screen.findByLabelText("Voice anchor");
  expect((box as HTMLTextAreaElement).value).toBe("The loaded one.");
});

test("an abandoned generation does not wedge Generate for later characters (#59)", async () => {
  // The orphaned call's `finally` no longer matches the token, so it can never
  // clear the busy flag itself — the new load has to.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: false, versions: [] },
    { id: "mara", name: "Mara", default_version: "default", has_avatar: false, versions: [] },
  ]);
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  (api.generateCharacterVoiceAnchor as any).mockReturnValue(new Promise(() => {}));  // never settles
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);

  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[0]);
  await screen.findByLabelText("Voice anchor");
  const actions = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));
  await waitFor(() => expect(screen.getByText("Generating…")).toBeTruthy());

  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  fireEvent.click(screen.getByText("‹ All characters"));
  fireEvent.click((await screen.findAllByRole("button", { name: /^edit$/i }))[1]);
  await screen.findByLabelText("Voice anchor");

  const next = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  const gen = within(next).getByText("Generate") as HTMLButtonElement;
  await waitFor(() => expect(gen.disabled).toBe(false));
});

test("an empty generated anchor is a failure, not a draft (#59)", async () => {
  // Installing "" would arm the destructive save with a blank the user never
  // wrote — one click would then delete the anchor generation failed to replace.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  (api.generateCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "   " });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Voice anchor");
  await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe("Clipped."));
  const actions = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));
  await screen.findByText(/returned an empty voice anchor/);
  expect((screen.getByLabelText("Voice anchor") as HTMLTextAreaElement).value).toBe("Clipped.");
});

test("a campaign-local character gets the anchor controls too (#59)", async () => {
  // An NPC accepted from an absorb `new_character` proposal exists only
  // campaign-side, so world-only controls would leave it unable to ever have an
  // anchor — and absorb would skip its voice check forever.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  (api.setCharacterVoiceAnchor as any).mockResolvedValue({ ok: true });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openEditForm();
  const box = await screen.findByLabelText("Voice anchor");
  await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe("Clipped."));
  expect(api.getCharacterVoiceAnchor).toHaveBeenCalledWith({ kind: "campaign", id: "run" }, "seraphine");
  fireEvent.change(box, { target: { value: "Campaign-local voice." } });
  fireEvent.click(screen.getByText("Save voice anchor"));
  await waitFor(() => expect(api.setCharacterVoiceAnchor).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "seraphine", "Campaign-local voice."));
});
