import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CharacterEditor } from "./CharacterEditor";

vi.mock("../api/client", () => ({
  api: {
    listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
    updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
    deleteCharacter: vi.fn(), importCharacter: vi.fn(), localizeImages: vi.fn(),
    putImage: vi.fn(), deleteImage: vi.fn(), importCharacterBook: vi.fn(),
    importCharacterFromChub: vi.fn(),
    setCharacterBirthdate: vi.fn(),
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
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.importCharacterBook as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  (api.setCharacterBirthdate as any).mockResolvedValue({ ok: true });
});

// reach the edit form: grid -> click a card's Edit button -> form
async function openEditForm() {
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  await screen.findByLabelText("Description");
}

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
  const version = await screen.findByLabelText("Version") as HTMLSelectElement;
  expect(version.value).toBe("v2");
});

test("import version posts importCharacter into the current character", async () => {
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Import version");
  fireEvent.change(input, { target: { files: [new File(["{}"], "v.json")] } });
  await waitFor(() =>
    expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json", "seraphine"));
});

test("downloading from chub.ai creates a character and shows the result", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("https://chub.ai/characters/creator/imp");
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default",
    gallery: { attempted: 2, stored: 2 },
    lore: { lorebooks_found: 1, created: [{ kind: "lore", id: "x" }] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /download from chub\.ai/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "https://chub.ai/characters/creator/imp"));
  await screen.findByText(/downloaded from chub\.ai.*2\/2 gallery images.*1 lorebook \(1 entry\) added to world lore/i);
});

test("an empty chub.ai prompt makes no API call", async () => {
  vi.spyOn(window, "prompt").mockReturnValue(null);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /download from chub\.ai/i }));
  expect(api.importCharacterFromChub).not.toHaveBeenCalled();
});
