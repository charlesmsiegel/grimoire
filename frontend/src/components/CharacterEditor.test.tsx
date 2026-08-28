import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CharacterEditor } from "./CharacterEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
      actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string) =>
        `/img/${sc.id}/${k}/${a}/${v}/${n}`,
      listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
      // The campaign-scope sidebar's LibraryPanel (#60). Resolved rather than
      // left undefined so every campaign-scope test here renders the pane it
      // always did; LibraryPanel.test.tsx owns the panel's own behaviour.
      libraryStatus: vi.fn().mockResolvedValue(
        { in_library: true, diverged: false, can_promote: false, can_push: false }),
      promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
      updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
      deleteCharacter: vi.fn(), importCharacter: vi.fn(), localizeImages: vi.fn(),
      putImage: vi.fn(), deleteImage: vi.fn(), promoteImage: vi.fn(), setAvatarFocus: vi.fn(),
      setCharacterImageDescription: vi.fn(),
      draftCharacterImageDescription: vi.fn(),
      listUndescribedImages: vi.fn(),
      importCharacterBook: vi.fn(),
      lorebookParse: vi.fn(), lorebookImport: vi.fn(), entityKinds: vi.fn(),
      importCharacterFromChub: vi.fn(),
      setCharacterName: vi.fn(), setCharacterBirthdate: vi.fn(), getCalendarMonths: vi.fn(),
      setCharacterChubSource: vi.fn(), clearCharacterChubSource: vi.fn(),
      downloadCharacterChubGallery: vi.fn(), downloadCharacterChubLorebooks: vi.fn(),
      findChubUnlinked: vi.fn(),
      getCharacterTagline: vi.fn(), setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
      generateWorldTaglines: vi.fn(),
      getConfig: vi.fn().mockResolvedValue({}),
      getCharacterVoiceAnchor: vi.fn(), setCharacterVoiceAnchor: vi.fn(),
      generateCharacterVoiceAnchor: vi.fn(),
      listImageAppearances: vi.fn(), copyGreetingImage: vi.fn(), listGreetings: vi.fn(),
      // The detail view's right pane: what one campaign has made of her.
      getCampaign: vi.fn(), getCasefile: vi.fn(), listEntities: vi.fn(),
      imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
      exportUrl: (w: string, c: string, v: string, f: string) => `/export/${w}/${c}/${v}/${f}`,
      putSheetCreation: vi.fn(),
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

const CARD = {
  spec: "chara_card_v3", spec_version: "3.0",
  data: {
    name: "Seraphine", description: "keeper", alternate_greetings: ["hi"], extensions: {},
    character_book: { entries: [{ keys: ["pact"], content: "x" }] },
  },
};
const DETAIL = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
  // `importable_lore` is the server's count of what the embedded-lore import
  // would actually commit -- CARD's one entry is enabled and non-blank, so 1.
  versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], importable_lore: 1 }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true, versions: [] }]);
  (api.readCharacter as any).mockResolvedValue(DETAIL);
  (api.createCharacter as any).mockResolvedValue({ character: "rook", version: "default" });
  (api.updateVersion as any).mockResolvedValue({ ok: true });
  (api.setCharacterName as any).mockResolvedValue({ ok: true });
  (api.importCharacter as any).mockResolvedValue({ character: "imp", version: "default" });
  (api.localizeImages as any).mockImplementation((_w: string, _c: string, _v: string, cb: (e: any) => void) => {
    cb?.({ summary: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false } });
    return Promise.resolve();
  });
  (api.putImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.deleteImage as any).mockResolvedValue({ ok: true });
  (api.promoteImage as any).mockResolvedValue({ ok: true });
  (api.setCharacterImageDescription as any).mockResolvedValue({ ok: true });
  (api.listUndescribedImages as any).mockResolvedValue([]);
  (api.setAvatarFocus as any).mockResolvedValue({ ok: true });
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.importCharacterBook as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  (api.lorebookParse as any).mockResolvedValue(
    { entries: [{ name: "pact", keys: ["pact"], body: "x", category: "lore" }] });
  (api.lorebookImport as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  (api.entityKinds as any).mockResolvedValue({ kinds: ["locations", "lore", "items", "groups", "creatures"] });
  (api.setCharacterBirthdate as any).mockResolvedValue({ ok: true });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "" });
  (api.listImageAppearances as any).mockResolvedValue([]);
  (api.copyGreetingImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.listGreetings as any).mockResolvedValue([]);
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
  // Campaign scope reads the roster to drive the appeared/all grid filter.
  // World scope never calls it, so this default is inert there.
  (api.listAppearances as any).mockResolvedValue([]);
  // The detail view's campaign-local pane. Campaign scope only: the world route
  // has no campaign to read, and asserts that it says so instead.
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "The Long Tide" } });
  (api.getCasefile as any).mockResolvedValue(CASEFILE);
  (api.listEntities as any).mockResolvedValue([]);
});

/** What a campaign has decided about Seraphine, as `GET .../casefile` answers.
 *  Only the fields 4f's right pane renders — the endpoint carries feelings and
 *  standing facts too, which belong to the play view's dossier column, where
 *  there is a scene for them to be relative to. */
const CASEFILE = {
  kind: "characters", id: "seraphine", name: "Seraphine", version: "default", role: "npc",
  scenes: ["001--the-tide-gate", "004--the-priory-door"],
  last_seen: "004--the-priory-door",
  standing: "Guarded. Will not be alone with the Reeve.",
  knows: "The priory's debt.", suspects: "",
  dossier: "A novice who counts the tide instead of the hours.",
  tagline: "", feels_toward: [], standing_facts: [],
};

/** Open a character from the grid and wait for the three-pane detail view. */
async function openDetail(name = "Seraphine") {
  fireEvent.click(await screen.findByText(name));
  await screen.findByRole("heading", { name });
}

/** The detail view's middle pane is tabbed; the card is what opens. */
async function openTab(label: RegExp) {
  fireEvent.click(await screen.findByRole("tab", { name: label }));
}

/** A roster the appeared filter will keep every one of `ids` in. */
function appearedRoster(...ids: string[]) {
  return ids.map((id) => ({ kind: "characters", id, version: "default", role: "npc", scenes: ["01"] }));
}

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
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");
  expect(screen.getByText("avatar")).toBeInTheDocument();               // shelf caption
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("a backlog reply that arrives after a world switch is discarded", async () => {
  // The queue carries record ids and image names, so installing the previous
  // world's under the new `wid` can send a description written about one record
  // to another that happens to share its slug.
  let release: (q: unknown[]) => void = () => {};
  (api.listUndescribedImages as any).mockImplementation((sc: any) =>
    sc.id === "w"
      ? new Promise<unknown[]>((resolve) => { release = resolve; })
      : Promise.resolve([]));

  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByRole("button", { name: /\+ New character/ });
  rerender(<CharacterEditor scope={{ kind: "world", id: "other" }} wid="other" />);
  await waitFor(() => expect(api.listUndescribedImages).toHaveBeenCalledWith(
    { kind: "world", id: "other" }));

  release([{ kind: "characters", id: "seraphine", vid: "default", name: "gallery_1",
             record_name: "Seraphine", url: "/img/1" }]);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /Describe images/ })).toBeNull();
});

test("the describe backlog appears as a toolbar button only when it has entries", async () => {
  (api.listUndescribedImages as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", vid: "default", name: "gallery_1",
      record_name: "Seraphine", url: "/img/1" },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  expect(await screen.findByRole("button", { name: /Describe images \(1\)/ }))
    .toBeInTheDocument();
});

test("an empty backlog shows no button, and a campaign scope never asks for one", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByRole("button", { name: /\+ New character/ });
  expect(screen.queryByRole("button", { name: /Describe images/ })).toBeNull();

  (api.listUndescribedImages as any).mockClear();
  render(<CharacterEditor scope={{ kind: "campaign", id: "c" }} wid="w" />);
  // A campaign has a queue too -- its own library and its diverged art, which
  // the world's queue cannot reach -- so it asks, scoped to itself.
  await waitFor(() => expect(api.listUndescribedImages).toHaveBeenCalledWith(
    { kind: "campaign", id: "c" }));
});

test("the art shelf offers a description per image, and says which are unreviewed", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD,
                 images: ["avatar", "gallery_1"],
                 // avatar described, gallery_1 never reviewed -- the two states
                 // the store keeps apart have to stay apart in the shelf.
                 image_descriptions: { avatar: "Half-plate, rain-soaked." } }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");
  expect(screen.getByRole("button", { name: /Description of avatar/ }))
    .toHaveTextContent("Half-plate, rain-soaked.");
  expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
    .toHaveTextContent("Describe…");
});

test("describing a gallery image saves it against that image's name", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar", "gallery_1"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");

  fireEvent.click(screen.getByRole("button", { name: /Description of gallery_1/ }));
  const box = await screen.findByRole("textbox", { name: /Description of gallery_1/ });
  fireEvent.change(box, { target: { value: "Fishing boats under fog." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(api.setCharacterImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", "gallery_1",
    "Fishing boats under fog."));
});

test("detail without avatar shows the dashed placeholder tile", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: [] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
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
  await openDetail();   // opens on the card tab, which is where it would be
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

// #27: the embedded-book import is the same parse -> review -> re-route flow
// the standalone lorebook dialog runs, not a blind one-click commit. Parsing
// writes nothing; the commit goes through the per-entry-category route.
test("the embedded character_book opens a review table and commits with per-entry categories", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /review .* lore/i }));
  // the STORED card is what gets parsed, through the world's parse route
  await waitFor(() => expect(api.lorebookParse).toHaveBeenCalledWith("w", expect.any(File), "json"));
  expect(api.lorebookImport).not.toHaveBeenCalled();   // parsing writes nothing

  // review: rename the entry and re-route it to locations
  fireEvent.change(await screen.findByLabelText("name 0"), { target: { value: "The Salt Pact" } });
  fireEvent.change(screen.getByLabelText("category 0"), { target: { value: "locations" } });
  fireEvent.click(screen.getByRole("button", { name: /^import 1 entry$/i }));
  await waitFor(() => expect(api.lorebookImport).toHaveBeenCalledWith("w",
    [expect.objectContaining({ name: "The Salt Pact", category: "locations" })]));
  await screen.findByText(/imported 1/i);
  expect(api.importCharacterBook).not.toHaveBeenCalled();   // the blind route stays unused here
});

test("a parse still in flight across a version switch cannot repopulate the review", async () => {
  // Codex review: the switch clears the rows, but a LATE parse response from
  // the old card must not put them back under the new version's button.
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "default", card: CARD, images: ["avatar"], importable_lore: 1 },
      { id: "young", name: "young", card: CARD, images: [], importable_lore: 1 },
    ],
  });
  let land: (v: unknown) => void = () => {};
  (api.lorebookParse as any).mockImplementation(() => new Promise((res) => { land = res; }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /review .* lore/i }));
  await waitFor(() => expect(api.lorebookParse).toHaveBeenCalled());

  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "young" } });
  await screen.findByRole("button", { name: /review .* lore/i });
  land({ entries: [{ name: "stale", keys: [], body: "x", category: "lore" }] });

  // the button is still the surface -- the stale rows never rendered
  await screen.findByRole("button", { name: /review .* lore/i });
  expect(screen.queryByLabelText("category 0")).toBeNull();
});

test("cancelling the embedded-book review commits nothing", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /review .* lore/i }));
  await screen.findByLabelText("category 0");
  fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
  await screen.findByRole("button", { name: /review .* lore/i });
  expect(api.lorebookImport).not.toHaveBeenCalled();
});

// Re-importing an unchanged book is a no-op by design (`lorebook.commit`
// drops entries already in the world), and "Imported 0 entries" reads as a
// failure of the thing that in fact worked.
test("re-importing an already-imported book says so instead of reporting zero", async () => {
  (api.lorebookImport as any).mockResolvedValue({ created: [] });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /review .* lore/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^import 1 entry$/i }));
  await screen.findByText(/already in the world/i);
  expect(screen.queryByText(/imported 0/i)).toBeNull();
});

// The review parses the STORED card, not the editor's state. It does not write
// the card back (unlike localize), so unsaved edits survive it; what they do is
// make the click act on a version of the book the editor is no longer showing.
// Blocked until saved, so the entries reviewed are the ones the user is looking
// at (#16).
test("the embedded-lore review is blocked while the form has unsaved edits", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  const button = screen.getByRole("button", { name: /review .* lore/i });
  expect(button).not.toBeDisabled();

  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "keeper of the salt ledgers" } });

  expect(button).toBeDisabled();
  expect(screen.getByText(/save your changes before importing embedded lore/i)).toBeInTheDocument();
  fireEvent.click(button);
  expect(api.lorebookParse).not.toHaveBeenCalled();
});

// `character_book.entries` is the raw list; the import commits it normalized,
// which drops disabled and blank entries. Counting the raw list offers an
// import of 4 that lands 1, so the label takes the server's count instead --
// down to the singular (#16).
test("the embedded-lore count is the server's importable count, not the raw entry list", async () => {
  const fourRawOneImportable = {
    ...CARD,
    data: { ...CARD.data, character_book: { entries: [
      { keys: ["pact"], content: "the salt pact" },
      { keys: ["tide"], content: "the tide table", enabled: false },
      { keys: ["gate"], content: "   " },
      { keys: ["reeve"], content: "the reeve's debt", disable: true },
    ] } },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: fourRawOneImportable, images: ["avatar"], importable_lore: 1 }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByRole("button", { name: /^review 1 embedded lore entry to import$/i });
  expect(screen.queryByRole("button", { name: /review 4 /i })).toBeNull();
});

// A version whose card offers nothing importable shows no button at all, and
// the count is per-version -- switching versions re-reads it.
test("the embedded-lore button follows the selected version and vanishes when it offers nothing", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "default", card: CARD, images: ["avatar"], importable_lore: 2 },
      { id: "young", name: "young", card: CARD, images: [], importable_lore: 0 },
    ],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByRole("button", { name: /^review 2 embedded lore entries to import$/i });

  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "young" } });
  await waitFor(() => expect(screen.queryByRole("button", { name: /review .* lore/i })).toBeNull());
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
  await waitFor(() => expect(api.createCharacter)
    .toHaveBeenCalledWith({ kind: "world", id: "w" }, { name: "Rook" }));
});

test("in campaign scope the create is campaign-local (#60)", async () => {
  // the same button, pointed at the campaign: an NPC who exists only here,
  // with no world counterpart, which is what "emergent" means
  vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  // the campaign roster is appearance-filtered, so it opens empty rather than
  // listing the world's cast -- wait for the button, not for a name
  fireEvent.click(await screen.findByRole("button", { name: /new npc/i }));
  await waitFor(() => expect(api.createCharacter)
    .toHaveBeenCalledWith({ kind: "campaign", id: "run" }, { name: "Winifred" }));
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

test("saving a renamed card renames the character, so the grid tile follows", async () => {
  // #13: the tile and every meta-name prompt section read the CONTAINER name,
  // which saving the card never touched -- the two could diverge forever.
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Winifred" } });
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Winifred", default_version: "default", has_avatar: false, versions: [] },
  ]);
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.setCharacterName).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "Winifred"));
  fireEvent.click(screen.getByRole("button", { name: /all characters/i }));
  expect(await screen.findByText("Winifred")).toBeInTheDocument();
});

test("saving an unchanged name does not call the rename", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "cold keeper" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

test("a blank name is saved on the card but never renames the character", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

test("renaming a non-default version's card leaves the character name alone", async () => {
  // Each version card carries its own `data.name` -- that is the version rail's
  // label. Renaming the whole character from a variant would be the surprise.
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "Seraphine", card: CARD, images: [] },
      { id: "older", name: "Seraphine (young)", card: CARD, images: [] },
    ],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: "Seraphine (young)" }));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

test("grid tiles cache-bust avatars on the stored token, not a session counter", async () => {
  // A `?v=` URL is served immutable for a year, so the token has to name the
  // bytes. A counter that resets to 0 on reload pins the pre-upload avatar.
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true,
      avatar_v: "17ab-2c", versions: [] },
  ]);
  const { container } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const tile = container.querySelector<HTMLImageElement>(".char-card img")!;
  expect(tile.getAttribute("src")).toBe("/img/w/characters/seraphine/default/avatar?v=17ab-2c");
});

test("a tile with no stored token asks for a bare, revalidating URL", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true,
      avatar_v: null, versions: [] },
  ]);
  const { container } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");
  const tile = container.querySelector<HTMLImageElement>(".char-card img")!;
  expect(tile.getAttribute("src")).toBe("/img/w/characters/seraphine/default/avatar");
});

test("the open character's shelf cache-busts each image on its own token", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{
      id: "default", name: "default", card: CARD, images: ["avatar", "gallery_0"],
      image_v: { avatar: "aa-1", gallery_0: "bb-2" },
    }],
  });
  const { container } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");
  const shelf = Array.from(container.querySelectorAll<HTMLImageElement>(".images-shelf img"));
  expect(shelf.map((i) => i.getAttribute("src"))).toEqual([
    "/img/w/characters/seraphine/default/avatar?v=aa-1",
    "/img/w/characters/seraphine/default/gallery_0?v=bb-2",
  ]);
});

test("promoting a gallery image re-reads the tokens, so the avatar is not the old one", async () => {
  // Promotion swaps the bytes UNDER a stable name, which is the case the
  // session counter existed for. The re-read has to carry the new token or the
  // immutable cache keeps serving the pre-promotion avatar.
  const version = (v: string) => ({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{
      id: "default", name: "default", card: CARD, images: ["avatar", "gallery_0"],
      image_v: { avatar: v, gallery_0: "bb-2" },
    }],
  });
  (api.readCharacter as any).mockResolvedValue(version("before-1"));
  (api.promoteImage as any).mockResolvedValue({ ok: true });
  const { container } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");
  const avatarOf = () =>
    container.querySelector<HTMLImageElement>(".avatar-tile img")!.getAttribute("src");
  expect(avatarOf()).toBe("/img/w/characters/seraphine/default/avatar?v=before-1");

  (api.readCharacter as any).mockResolvedValue(version("after-2"));
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() =>
    expect(avatarOf()).toBe("/img/w/characters/seraphine/default/avatar?v=after-2"));
});

test("saving an unrelated edit never renames a character whose card already differed", async () => {
  // Divergence predates this change (old cards, an import with an explicit
  // name, a chub re-download). Converging it silently on a Description edit
  // would rename her across the grid, the cast panel and every prompt section
  // without the user touching the Name field -- and there is no undo.
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{
      id: "default", name: "Seraphine the Bold", images: [],
      card: { ...CARD, data: { ...CARD.data, name: "Seraphine the Bold" } },
    }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "cold keeper" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

test("a name typed with stray whitespace is stored trimmed on both sides", async () => {
  // The card keeps `data.name` verbatim as the version rail's label, so an
  // untrimmed card name against a trimmed container name is a divergence that
  // never converges -- the very thing this is meant to end.
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Winifred  " } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => expect(api.setCharacterName).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "Winifred"));
  expect((api.updateVersion as any).mock.calls[0][3].data.name).toBe("Winifred");
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
  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine"));
});

test("bumping resetSignal returns from the editor to the grid", async () => {
  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" resetSignal={0} />);
  await openEditForm(); // in the edit form
  rerender(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" resetSignal={1} />);
  await screen.findByRole("button", { name: /new character/i }); // back at the grid
  expect(screen.queryByLabelText("Description")).toBeNull();
});

// ---- the world-wide derive (#57) ----

/** A roster where `blanks` characters have no tagline and `set` do. */
function roster(blanks: string[], set: Record<string, string> = {}) {
  return [
    ...blanks.map((id) => ({ id, name: id, default_version: "default", has_avatar: false,
                             tagline: "", versions: [] })),
    ...Object.entries(set).map(([id, tagline]) => ({ id, name: id, default_version: "default",
                                                     has_avatar: false, tagline, versions: [] })),
  ];
}

/** Drive the route's frames through the component's own callback. */
function derives(frames: any[]) {
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void) => { frames.forEach(cb); return Promise.resolve(); });
}

test("the derive button counts the characters with no tagline", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"], { seraphine: "Set." }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByRole("button", { name: /Derive taglines \(2\)/ });
});

test("the derive button is absent when every tagline is set", async () => {
  (api.listCharacters as any).mockResolvedValue(roster([], { seraphine: "Set." }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("seraphine");
  expect(screen.queryByRole("button", { name: /Derive taglines/ })).toBeNull();
});

test("the derive button is absent in campaign scope", async () => {
  // A tagline is a world-level property; a campaign's grid is a view of one.
  (api.listCharacters as any).mockResolvedValue(roster(["mara"]));
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText("mara");
  expect(screen.queryByRole("button", { name: /Derive taglines/ })).toBeNull();
});

test("deriving reports what was written and reloads the grid", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"]));
  derives([
    { total: 2 },
    { done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." },
    { done: 2, character: "winifred", name: "Winifred", tagline: "A locksmith who never sleeps." },
    { summary: { total: 2, written: 2, skipped: 0, stopped: false } },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(2\)/ }));
  await screen.findByText("Derived 2 taglines");
  // The route wrote as it went, so the grid is re-read rather than patched.
  expect(api.listCharacters).toHaveBeenCalledTimes(2);
});

test("a stopped run names the failure and points at the re-run", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred", "seraphine"]));
  derives([
    { total: 3 },
    { done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." },
    { done: 2, character: "winifred", name: "Winifred",
      error: { detail: "rate limited", kind: "rate_limit" } },
    { summary: { total: 3, written: 1, skipped: 0, stopped: true } },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(3\)/ }));
  const note = await screen.findByText(/Derived 1 tagline/);
  expect(note.textContent).toContain("rate limited");
  expect(note.textContent).toContain("run it again");
  // ...and the button's count is the roster's, so it is right without arithmetic.
  await screen.findByRole("button", { name: /Derive taglines \(3\)/ });
});

test("the report says why nothing was written, not just how many", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred", "seraphine"]));
  derives([
    { total: 3 },
    { done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." },
    { done: 2, character: "winifred", name: "Winifred", skipped: "already set" },
    { done: 3, character: "seraphine", name: "Seraphine", skipped: "blank" },
    { summary: { total: 3, written: 1, skipped: 2, stopped: false } },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(3\)/ }));
  const note = await screen.findByText(/Derived 1 tagline/);
  expect(note.textContent).toContain("1 already set");
  expect(note.textContent).toContain("1 blank");
});

test("Stop ends the run and still reports what landed", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"]));
  // A run that never finishes on its own: it answers for the first character
  // and then waits, exactly as a real one waits on the provider.
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void, signal: AbortSignal) => new Promise((_ok, fail) => {
      cb({ total: 2 });
      cb({ done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." });
      signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")));
    }));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(2\)/ }));
  // The live progress line, which only a run still in flight can show.
  await screen.findByText(/Deriving taglines 1\/2 — Mara/);
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  // The abort is the user's own doing, so it reports rather than banners — and
  // what the run wrote before it was stopped is still written.
  const note = await screen.findByText(/Derived 1 tagline/);
  expect(note.textContent).toContain("run it again");
  expect(screen.queryByText(/aborted/)).toBeNull();
});

test("leaving the editor aborts the run rather than leaving it spending", async () => {
  // The progress line and Stop go with the view, so a run that outlived it
  // would be a stream nobody can see or stop, still paying per character.
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"]));
  let signal: AbortSignal | null = null;
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void, sig: AbortSignal) => new Promise((_ok, fail) => {
      signal = sig;
      cb({ total: 2 });
      sig.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")));
    }));
  const view = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(2\)/ }));
  await screen.findByRole("button", { name: "Stop" });
  view.unmount();
  expect(signal!.aborted).toBe(true);
});

test("a run whose editor has moved on reports nothing into the new world", async () => {
  // `adopt`'s rule, applied to the batch: a continuation belonging to world A
  // must not paint anything onto the editor now showing world B.
  (api.listCharacters as any).mockResolvedValue(roster(["mara"]));
  let finish: () => void = () => {};
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void) => new Promise<void>((ok) => {
      cb({ total: 1 });
      cb({ done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." });
      cb({ summary: { total: 1, written: 1, skipped: 0, stopped: false } });
      finish = ok;   // the request itself has not settled yet
    }));
  const view = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(1\)/ }));
  view.rerender(<CharacterEditor scope={{ kind: "world", id: "w2" }} wid="w2" />);
  await act(async () => { finish(); });
  expect(screen.queryByText(/Derived 1 tagline/)).toBeNull();
});

test("a roster read that lands after the editor moved on is discarded", async () => {
  // The guard is on the READ, not the caller: a `listCharacters` that resolves
  // after the scope changed would otherwise paint world A's cards under world
  // B's handlers, where Delete on a shared slug hits B while showing A.
  // One resolver per read, kept in order: the scope change starts a SECOND
  // read, and resolving that one would prove nothing — it is w2's own.
  const reads: ((rows: any[]) => void)[] = [];
  (api.listCharacters as any).mockImplementation(() => new Promise((ok) => { reads.push(ok); }));
  const view = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  view.rerender(<CharacterEditor scope={{ kind: "world", id: "w2" }} wid="w2" />);
  await act(async () => { reads[0](roster(["mara"])); });   // w's read, landing late
  expect(screen.queryByText("mara")).toBeNull();
});

test("a failed roster reload still reports what the run wrote", async () => {
  // The taglines are already on disk. Letting the reload's rejection out of
  // `finally` would take the report with it — and escape the click handler as
  // an unhandled rejection.
  (api.listCharacters as any).mockResolvedValueOnce(roster(["mara"]))
    .mockRejectedValue(new ApiError(503, "roster unavailable"));
  derives([
    { total: 1 },
    { done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." },
    { summary: { total: 1, written: 1, skipped: 0, stopped: false } },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(1\)/ }));
  await screen.findByText(/Derived 1 tagline/);
});

test("the report does not follow the editor into another world", async () => {
  // "Derived 2 taglines" is a claim about one library. Left standing across a
  // scope change it becomes a claim about whichever one is showing now.
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"]));
  derives([
    { total: 2 },
    { done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." },
    { done: 2, character: "winifred", name: "Winifred", tagline: "A locksmith who never sleeps." },
    { summary: { total: 2, written: 2, skipped: 0, stopped: false } },
  ]);
  const view = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(2\)/ }));
  await screen.findByText("Derived 2 taglines");
  view.rerender(<CharacterEditor scope={{ kind: "world", id: "w2" }} wid="w2" />);
  await waitFor(() => expect(screen.queryByText("Derived 2 taglines")).toBeNull());
});

test("a stream that drops mid-run reports what landed as well as the error", async () => {
  // The route writes as it goes, so frames that arrived are facts about the
  // store. Someone whose connection dies at character 200 must not be told
  // that nothing happened.
  (api.listCharacters as any).mockResolvedValue(roster(["mara", "winifred"]));
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void) => {
      cb({ total: 2 });
      cb({ done: 1, character: "mara", name: "Mara", tagline: "A courier with cold hands." });
      return Promise.reject(new TypeError("network error"));
    });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(2\)/ }));
  const note = await screen.findByText(/Derived 1 tagline/);
  expect(note.textContent).toContain("run it again");
  expect(screen.getByText(/network error/)).toBeInTheDocument();   // the banner too
});

test("a refusal before the stream starts is an error banner, not a report", async () => {
  (api.listCharacters as any).mockResolvedValue(roster(["mara"]));
  (api.generateWorldTaglines as any).mockRejectedValue(new ApiError(409, "no connection configured"));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(1\)/ }));
  await screen.findByText(/no connection configured/);
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

test("a focus link to a character that is gone shows the reason, not an unhandled rejection", async () => {
  // `focusCharacter` is called from a mount effect, which cannot await it, so
  // letting the read reject left the screen on the grid saying nothing and put
  // an unhandled rejection on the console (health: floating_promise).
  (api.readCharacter as any).mockRejectedValue(
    new ApiError(404, "character not found"));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" focus={{ cid: "gone", vid: "v1" }} />);
  expect(await screen.findByText("character not found")).toBeInTheDocument();
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
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");

  const thumbs = Array.from(
    container.querySelectorAll<HTMLImageElement>(".images-shelf .shelf-tile:not(.avatar-tile) img"));
  expect(thumbs).toHaveLength(3);
  // numeric order, not lexicographic ("gallery_10" must not sort before "gallery_2")
  expect(thumbs.map((t) => t.src)).toEqual([
    "http://localhost:3000/img/w/characters/seraphine/default/gallery_0",
    "http://localhost:3000/img/w/characters/seraphine/default/gallery_2",
    "http://localhost:3000/img/w/characters/seraphine/default/gallery_10",
  ]);
  const links = thumbs.map((t) => t.closest("a"));
  expect(links.every((a) => a?.getAttribute("target") === "_blank")).toBe(true);
  expect(links[0]).toHaveAttribute("href", "/img/w/characters/seraphine/default/gallery_0");
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
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");
  expect(screen.queryByRole("button", { name: /set as avatar/i })).toBeNull();

  // the download is offered beside the chub link, on the card tab
  await openTab(/^card/i);
  fireEvent.click(await screen.findByRole("button", { name: /^download gallery$/i }));
  await openTab(/^art/i);
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
  await openDetail();
  await openTab(/^greetings/i);
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
  await openDetail();
  // the description is card content and stays literal on the card tab...
  expect(screen.getByText("plain **stars** stay literal")).toBeInTheDocument();
  // ...while both greeting kinds render their markdown, on the greetings tab
  await openTab(/^greetings/i);
  await screen.findByRole("img", { name: "scene" });
  await screen.findByRole("img", { name: "alt-pic" });
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

// A bulk import finishing behind an open avatar crop used to queue the tagline
// prompt UNDER it: same backdrop, same z-index, so the crop stays painted on
// top (later sibling) while the prompt, mounting later, took the keyboard.
// Escape then skipped a prompt the reader never saw instead of cancelling the
// crop in front of them (PR #400 review). The two no longer coexist.
test("a tagline prompt queued behind the avatar crop waits for it", async () => {
  let finishImport: (v: unknown) => void = () => {};
  (api.importCharacterFromChub as any)
    .mockImplementationOnce(() => new Promise((res) => { finishImport = res; }))
    .mockResolvedValue({ character: "two", version: "default", updated: false,
                         gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], avatar_focus: null }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");

  // Two URLs, so the import does not end by opening the new character's detail
  // and pulling the crop out from under the test.
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"), { target: { value: "creator/one\ncreator/two" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

  // ...and while it is in flight, the reader opens the crop.
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /adjust avatar crop/i }));
  await screen.findByLabelText("Crop position");

  finishImport({ character: "one", version: "default", updated: false,
                 gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  await screen.findByText(/added 2\/2 characters/i);

  // The crop is still what the reader sees, and still what Escape answers.
  expect(screen.getByLabelText("Crop position")).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: /set tagline/i })).toBeNull();
  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByLabelText("Crop position")).toBeNull());
  // ...and only then does the queued prompt get its turn.
  expect(await screen.findByRole("dialog", { name: /set tagline/i })).toBeInTheDocument();
});

// The same overlap one door along: the toolbar button is not disabled while an
// import runs, so the URL prompt can be reopened over it, and completion mounts
// the tagline prompt LATER -- taking Escape while the URL prompt is the thing
// painted on top (PR #400 review).
test("a tagline prompt queued behind the URL prompt waits for it", async () => {
  let finishImport: (v: unknown) => void = () => {};
  (api.importCharacterFromChub as any)
    .mockImplementationOnce(() => new Promise((res) => { finishImport = res; }))
    .mockResolvedValue({ character: "two", version: "default", updated: false,
                         gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Seraphine");

  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  fireEvent.change(screen.getByLabelText("Card URLs"), { target: { value: "creator/one\ncreator/two" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

  // ...reopened while that import is still running.
  fireEvent.click(screen.getByRole("button", { name: /^download from url$/i }));
  await screen.findByLabelText("Card URLs");

  finishImport({ character: "one", version: "default", updated: false,
                 gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] } });
  await screen.findByText(/added 2\/2 characters/i);

  expect(screen.queryByRole("dialog", { name: /set tagline/i })).toBeNull();
  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByLabelText("Card URLs")).toBeNull());
  expect(await screen.findByRole("dialog", { name: /set tagline/i })).toBeInTheDocument();
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
  await openDetail();

  // appears-in strip lives with the rest of her art
  await openTab(/^art/i);
  const label = await screen.findByText("Appears in");
  const strip = label.parentElement as HTMLElement;
  fireEvent.click(within(strip).getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.copyGreetingImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", { gid: "sol-1", name: "embed-a", slot: "avatar" }));
  expect(within(strip).getByRole("button", { name: /add to gallery/i })).toBeInTheDocument();

  // world greetings are references out of the card, not card greetings: they
  // sit with the tags on the card tab and do not count toward GREETINGS n
  await openTab(/^card/i);
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
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  const { container } = render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByRole("button", { name: "Import card" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Download from URL" })).toBeNull();
  expect(screen.queryByRole("button", { name: "+ New character" })).toBeNull();
  const img = container.querySelector("img.char-card-avatar")!;
  expect(img.getAttribute("src")).toContain("/img/run/characters/mara/");
});

// A campaign inherits its world's whole character roster, most of which never
// walks on. The grid opens on the campaign's own cast; the rest stays one
// click away rather than being hidden outright.
const TWO_CHARS = [
  { id: "mara", name: "Mara", default_version: "young", versions: [] },
  { id: "winifred", name: "Winifred", default_version: "default", versions: [] },
];

test("campaign scope: the grid opens on the appeared cast and All reveals the rest", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue([
    ...appearedRoster("mara"),
    // a PC sharing a character's id must not smuggle that character in: the
    // filter is per kind, and PCs have their own tab
    { kind: "pcs", id: "winifred", version: "default", role: "player", scenes: ["01"] },
  ]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);

  await screen.findByText("Mara");
  expect(screen.queryByText("Winifred")).toBeNull();
  expect(screen.getByRole("button", { name: "Appeared (1)" })).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(screen.getByRole("button", { name: "All (2)" }));
  await screen.findByText("Winifred");
  expect(screen.getByText("Mara")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Appeared (1)" }));
  await waitFor(() => expect(screen.queryByText("Winifred")).toBeNull());
});

// A roster entry is not an appearance. `transitions.leave` drops a scene from
// an actor's record but keeps the record (it is also what locks them to a
// version), so a character seated and then removed -- or whose only scene was
// deleted -- sits in the roster having never been in a scene.
test("a roster entry with no scenes has not appeared", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue([
    ...appearedRoster("mara"),
    { kind: "characters", id: "winifred", version: "default", role: "npc", scenes: [] },
  ]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);

  await screen.findByText("Mara");
  expect(screen.queryByText("Winifred")).toBeNull();
  expect(screen.getByRole("button", { name: "Appeared (1)" })).toBeInTheDocument();
});

test("world scope offers no appeared filter and reads no roster", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.getByText("Winifred")).toBeInTheDocument();   // nothing is filtered
  expect(screen.queryByRole("button", { name: /^Appeared/ })).toBeNull();
  expect(api.listAppearances).not.toHaveBeenCalled();
});

// The filter narrows a list; it must never be the reason a character cannot be
// found at all. An unreadable roster therefore falls back to showing everything.
test("a failed roster read leaves the campaign grid unfiltered", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockRejectedValue(new Error("nope"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.getByText("Winifred")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Appeared/ })).toBeNull();
});

test("a campaign nobody has played yet says so rather than looking empty", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByText(/No one has appeared in this campaign yet/);
  fireEvent.click(screen.getByRole("button", { name: "All (2)" }));
  await screen.findByText("Mara");
});

// Reached from another tab (a greeting's present-character link, an owner
// chip), a character need not have appeared. Coming back to a grid that
// filtered it out would read as the record having been deleted.
test("returning from a character that has not appeared drops the filter", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Winifred" } } }],
  });
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w"
                          focus={{ cid: "winifred", vid: "default" }} />);
  fireEvent.click(await screen.findByRole("button", { name: /‹ all characters/i }));
  await screen.findByText("Winifred");                       // still listed, not filtered away
  expect(screen.getByRole("button", { name: "All (2)" })).toHaveAttribute("aria-pressed", "true");
});

// The roster grows as scenes are played, and re-clicking the Characters tab is
// how a reader refreshes this page.
test("re-clicking the Characters tab re-reads the roster", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  const { rerender } = render(
    <CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={0} />);
  await screen.findByText("Mara");
  expect(screen.queryByText("Winifred")).toBeNull();

  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara", "winifred"));
  rerender(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={1} />);
  await screen.findByText("Winifred");
});

// Codex review, finding 1. Two reads of the SAME campaign can be in flight at
// once (`resetSignal` re-reads), so a scope check cannot order them: the slow
// first read lands last and reinstates the roster from before the scene that
// was just played, dropping its new arrivals back out of the grid.
test("a slow earlier roster read cannot overwrite a later one", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  let releaseFirst: (v: any) => void = () => {};
  (api.listAppearances as any)
    .mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }))    // read A: hangs
    .mockResolvedValue(appearedRoster("mara", "winifred"));            // read B: the current roster
  const { rerender } = render(
    <CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={0} />);
  rerender(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={1} />);
  await screen.findByText("Winifred");

  // A now lands, carrying the pre-scene roster
  await act(async () => { releaseFirst(appearedRoster("mara")); });
  expect(screen.getByText("Winifred")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Appeared (2)" })).toBeInTheDocument();
});

// Codex review, finding 2. This instance is reused across a scope change, and
// `showAll` is a statement about ONE campaign's inherited roster.
test("All does not carry across a campaign change", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  const { rerender } = render(<CharacterEditor scope={{ kind: "campaign", id: "a" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "All (2)" }));
  await screen.findByText("Winifred");

  rerender(<CharacterEditor scope={{ kind: "campaign", id: "b" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByText("Winifred")).toBeNull();      // campaign b opens on its own cast
  expect(screen.getByRole("button", { name: "Appeared (1)" })).toHaveAttribute("aria-pressed", "true");
});

// Codex review, finding 3. `listCharacters` can resolve first; painting the
// grid then shows the whole inherited roster until the appearances read lands
// and yanks it away.
test("the grid waits for the roster instead of flashing every inherited character", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  let release: (v: any) => void = () => {};
  (api.listAppearances as any).mockReturnValue(new Promise((r) => { release = r; }));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);

  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  expect(screen.queryByText("Winifred")).toBeNull();
  expect(screen.queryByText("Mara")).toBeNull();
  expect(screen.queryByText(/No one has appeared/)).toBeNull();   // no wrong verdict either

  await act(async () => { release(appearedRoster("mara")); });
  await screen.findByText("Mara");
  expect(screen.queryByText("Winifred")).toBeNull();
});

// Codex review, finding 4. Back can be pressed before the roster read lands,
// which is exactly the case the `focus` route hits: the character opens on
// mount, in parallel with the read.
test("a character closed before the roster lands is still not stranded behind the filter", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  let release: (v: any) => void = () => {};
  // The filter's read (first call) hangs; `loadLockState` shares this endpoint
  // and must still answer, or the detail view never opens at all.
  (api.listAppearances as any)
    .mockReturnValueOnce(new Promise((r) => { release = r; }))
    .mockResolvedValue([]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Winifred" } } }],
  });
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w"
                          focus={{ cid: "winifred", vid: "default" }} />);
  fireEvent.click(await screen.findByRole("button", { name: /‹ all characters/i }));

  // only now does the roster arrive, and it does not contain her
  await act(async () => { release(appearedRoster("mara")); });
  await screen.findByText("Winifred");
  expect(screen.getByRole("button", { name: "All (2)" })).toHaveAttribute("aria-pressed", "true");
});

// Codex review round 2. Back is not the only way out of a character: clicking
// the already-active Characters tab bumps `resetSignal`, which closes the
// detail view WITHOUT going through backToGrid. That close owes the character
// the same protection, or the safeguard only covers one of its two doors.
test("closing a character via the Characters tab keeps it out of the filter's way too", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Winifred" } } }],
  });
  const { rerender } = render(
    <CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={0}
                     focus={{ cid: "winifred", vid: "default" }} />);
  await screen.findByRole("button", { name: /‹ all characters/i });   // her detail is open

  // the reader clicks the Characters tab rather than Back
  rerender(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" resetSignal={1}
                            focus={{ cid: "winifred", vid: "default" }} />);
  await screen.findByText("Winifred");
  expect(screen.getByRole("button", { name: "All (2)" })).toHaveAttribute("aria-pressed", "true");
});

// Codex review round 3. A commit that changes BOTH props runs the scope reset
// and the `resetSignal` close together: the reset clears the pending reveal,
// and the close must not then re-arm it with the character that belonged to the
// campaign just left -- which would open the NEW campaign on its whole roster.
test("a simultaneous campaign change and tab reset does not carry the reveal across", async () => {
  (api.listCharacters as any).mockResolvedValue(TWO_CHARS);
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Winifred" } } }],
  });
  const { rerender } = render(
    <CharacterEditor scope={{ kind: "campaign", id: "a" }} wid="w" resetSignal={0}
                     focus={{ cid: "winifred", vid: "default" }} />);
  await screen.findByRole("button", { name: /‹ all characters/i });

  rerender(<CharacterEditor scope={{ kind: "campaign", id: "b" }} wid="w" resetSignal={1} />);
  await screen.findByText("Mara");
  // campaign b opens on its own cast; a's character does not drag the filter open
  expect(screen.queryByText("Winifred")).toBeNull();
  expect(screen.getByRole("button", { name: "Appeared (1)" })).toHaveAttribute("aria-pressed", "true");
});

test("campaign scope: picking a version calls pickVersion", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockImplementation(async () => ({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [
      { id: "young", name: "Young", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
      { id: "veteran", name: "Veteran", card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } },
    ],
  }));
  // An empty roster is the whole point of this case: picking a version is only
  // offered while the character is NOT yet locked to one, and locking is what
  // a first appearance does. So Mara has not appeared, and the grid is opened
  // on All to reach her.
  (api.listAppearances as any).mockResolvedValue([]);
  (api.pickVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "All (1)" }));
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
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
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
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
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
  (api.listAppearances as any).mockResolvedValue(appearedRoster("mara"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail("Mara");
  await openTab(/^art/i);
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
  await openDetail();
  await openTab(/^art/i);
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

it("wires the wizard's deleteRecord to api.deleteCharacter (world scope only) so a failed sheet write rolls back", async () => {
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

  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith({ kind: "world", id: "w1" }, "rook"));
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

test("a Generate the model could not be reached for offers the recovery (#210)", async () => {
  (api.generateCharacterVoiceAnchor as any)
    .mockRejectedValue(new ApiError(502, "connection refused", "network"));
  render(<MemoryRouter><CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" /></MemoryRouter>);
  await openEditForm();
  await screen.findByLabelText("Voice anchor");
  const actions = screen.getByText("Save voice anchor").closest(".form-actions") as HTMLElement;
  fireEvent.click(within(actions).getByText("Generate"));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
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
  (api.listAppearances as any).mockResolvedValue(appearedRoster("seraphine"));
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

// ---- export (#10) ---------------------------------------------------------

const TWO_VERSIONS = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
  versions: [
    { id: "default", name: "default", card: CARD, images: ["avatar"] },
    { id: "winter", name: "Winter", card: CARD, images: [] },
  ],
};

/** The three download links behind the Export disclosure. */
function exportLinks() {
  return {
    json: screen.getByRole("link", { name: /^json$/i }),
    png: screen.getByRole("link", { name: /^png$/i }),
    charx: screen.getByRole("link", { name: /^charx$/i }),
  };
}

test("detail offers a JSON/PNG/CHARX download for the viewed version", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  const links = exportLinks();
  expect(links.json).toHaveAttribute("href", "/export/w/seraphine/default/json");
  expect(links.png).toHaveAttribute("href", "/export/w/seraphine/default/png");
  expect(links.charx).toHaveAttribute("href", "/export/w/seraphine/default/charx");
  // `download` is what makes the browser save the response instead of showing it
  expect(links.json).toHaveAttribute("download");
  expect(links.png).toHaveAttribute("download");
  expect(links.charx).toHaveAttribute("download");
});

test("the export links follow the selected version", async () => {
  (api.readCharacter as any).mockResolvedValue(TWO_VERSIONS);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  fireEvent.click(screen.getByRole("button", { name: "Winter" }));
  await waitFor(() => expect(exportLinks().json)
    .toHaveAttribute("href", "/export/w/seraphine/winter/json"));
  expect(exportLinks().charx).toHaveAttribute("href", "/export/w/seraphine/winter/charx");
});

test("the edit form exports the version being edited", async () => {
  (api.readCharacter as any).mockResolvedValue(TWO_VERSIONS);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "winter" } });
  await waitFor(() => expect(exportLinks().png)
    .toHaveAttribute("href", "/export/w/seraphine/winter/png"));
});

test("campaign scope has no export control — the route is world-scoped", async () => {
  (api.listAppearances as any).mockResolvedValue(appearedRoster("seraphine"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();
  expect(screen.queryByText("Export")).toBeNull();
  expect(screen.queryByRole("link", { name: /^charx$/i })).toBeNull();
});


// ---- 4f: the card is the world's, the state beside it is one campaign's ----
//
// The detail view is three panes: who she is, the card that is sent to the
// model and shared by every campaign built on this world, and what one
// campaign has made of her. That last pane has no campaign-scoped endpoint of
// its own -- the casefile route is nested under a scene and checks she is cast
// in it -- so these cover what each scope can honestly answer.

/** A roster whose entry for `id` carries the given scene ids. */
function rosterWithScenes(id: string, scenes: string[], version = "default") {
  return [{ kind: "characters", id, version, role: "npc", scenes }];
}

test("world scope says there is no campaign in scope rather than showing an empty frame", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  const pane = screen.getByRole("complementary", { name: "Campaign state" });
  expect(within(pane).getByText(/no campaign in scope/i)).toBeInTheDocument();
  expect(within(pane).getByText(/belongs to a campaign/i)).toBeInTheDocument();
  // there is no campaign, so nothing was asked about one
  expect(api.getCasefile).not.toHaveBeenCalled();
  expect(api.getCampaign).not.toHaveBeenCalled();
});

test("campaign scope reads her state through the newest scene she is cast in", async () => {
  (api.listAppearances as any).mockResolvedValue(
    rosterWithScenes("seraphine", ["001--the-tide-gate", "004--the-priory-door"]));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();

  // The record is campaign-scoped; a scene is only how the route lets us ask
  // for it, and the newest is the one whose cast check is certain to pass.
  await waitFor(() => expect(api.getCasefile).toHaveBeenCalledWith(
    "run", "004--the-priory-door", "characters", "seraphine"));

  const pane = screen.getByRole("complementary", { name: "Campaign state" });
  expect(await within(pane).findByText("Guarded. Will not be alone with the Reeve.")).toBeInTheDocument();
  expect(within(pane).getByText("The priory's debt.")).toBeInTheDocument();
  expect(within(pane).getByText("A novice who counts the tide instead of the hours.")).toBeInTheDocument();
  expect(within(pane).getByText("dossier.md")).toBeInTheDocument();
  // the campaign is named, so the pane's heading has a subject
  expect(within(pane).getByText(/in the long tide/i)).toBeInTheDocument();
  // scene numbers come from the ids' own leading number, not from list order
  expect(within(pane).getByText("Scene 1")).toBeInTheDocument();
  expect(within(pane).getByText("Scene 4")).toBeInTheDocument();
  // none of it leaked into the card pane, which is the world's
  expect(within(screen.getByRole("region", { name: "Character card" }))
    .queryByText(/will not be alone/i)).toBeNull();
});

test("a character the campaign has never played is not asked about", async () => {
  // A roster entry with no scenes: seated and removed again, or her only scene
  // deleted. The casefile route would 404, and a 404 rendered as an empty pane
  // reads as "nothing recorded about her" -- a different sentence.
  (api.listAppearances as any).mockResolvedValue(rosterWithScenes("seraphine", []));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  // no scenes is also "not appeared", so the grid's default filter hides her
  fireEvent.click(await screen.findByRole("button", { name: /^all \(/i }));
  await openDetail();
  const pane = screen.getByRole("complementary", { name: "Campaign state" });
  expect(await within(pane).findByText(/has not been in a scene in The Long Tide yet/i)).toBeInTheDocument();
  expect(api.getCasefile).not.toHaveBeenCalled();
});

test("an unreadable casefile still reports the scenes instead of blanking the pane", async () => {
  (api.listAppearances as any).mockResolvedValue(
    rosterWithScenes("seraphine", ["004--the-priory-door"]));
  (api.getCasefile as any).mockRejectedValue({ detail: "not in scene" });
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();
  const pane = screen.getByRole("complementary", { name: "Campaign state" });
  expect(await within(pane).findByText(/could not read/i)).toBeInTheDocument();
  expect(within(pane).getByText("Scene 4")).toBeInTheDocument();
});

test("a campaign that has recorded nothing about her yet says so", async () => {
  (api.listAppearances as any).mockResolvedValue(
    rosterWithScenes("seraphine", ["004--the-priory-door"]));
  (api.getCasefile as any).mockResolvedValue({
    ...CASEFILE, standing: "", knows: "", suspects: "", dossier: "", tagline: "",
  });
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();
  const pane = screen.getByRole("complementary", { name: "Campaign state" });
  expect(await within(pane).findByText(/no absorb pass has written/i)).toBeInTheDocument();
  expect(within(pane).getByText("Scene 4")).toBeInTheDocument();   // she was still there
});

test("the previous character's campaign state does not sit under the next one's name", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "winifred", name: "Winifred", default_version: "default", versions: [] },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", role: "npc", scenes: ["004--the-priory-door"] },
    { kind: "characters", id: "winifred", version: "default", role: "npc", scenes: [] },
  ]);
  (api.readCharacter as any).mockImplementation((_s: unknown, id: string) => Promise.resolve({
    meta: { id, name: id === "winifred" ? "Winifred" : "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [], card: { ...CARD, data: {
      ...CARD.data, name: id === "winifred" ? "Winifred" : "Seraphine" } } }],
  }));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  // Winifred has no scenes, so the grid's default filter would hide her
  fireEvent.click(await screen.findByRole("button", { name: /^all \(/i }));
  await openDetail();
  const pane = () => screen.getByRole("complementary", { name: "Campaign state" });
  expect(await within(pane()).findByText("Guarded. Will not be alone with the Reeve.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /all characters/i }));
  await openDetail("Winifred");
  // Winifred has walked into nothing; Seraphine's standing must not follow her
  expect(await within(pane()).findByText(/has not been in a scene/i)).toBeInTheDocument();
  expect(within(pane()).queryByText(/will not be alone/i)).toBeNull();
});

test("the reach warning points the opposite way in each scope", async () => {
  const { unmount } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  // the world record is the one every campaign shares
  expect(screen.getByText(/reach every campaign using this world/i)).toBeInTheDocument();
  unmount();

  (api.listAppearances as any).mockResolvedValue(appearedRoster("seraphine"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();
  // ...and a campaign's copy is a fork, which is the opposite claim
  expect(screen.getByText(/leave the world record untouched/i)).toBeInTheDocument();
});

test("the locked version is marked with the campaign that locked it", async () => {
  (api.readCharacter as any).mockResolvedValue(TWO_VERSIONS);
  (api.listAppearances as any).mockResolvedValue(
    rosterWithScenes("seraphine", ["004--the-priory-door"], "winter"));
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openDetail();
  const locked = await screen.findByText(/locked in The Long Tide/i);
  // marked on the version it belongs to, not merely present on the page
  expect(within(locked.parentElement as HTMLElement)
    .getByRole("button", { name: "Winter" })).toBeInTheDocument();
  // ...and the badge stays out of the button's accessible name, so a version
  // is still picked by its own name
  expect(screen.getByRole("button", { name: "Winter" })).toBeInTheDocument();
});

test("the middle pane opens on the card and counts only the card's own greetings", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", images: ["avatar", "gallery_1"], card: {
      ...CARD,
      data: { ...CARD.data, first_mes: "the tide is out", alternate_greetings: ["hi", "hello"] },
    } }],
  });
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-1", name: "SoL 1", character: "other", version: "main",
      present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  expect(screen.getByRole("tab", { name: /^card$/i })).toHaveAttribute("aria-selected", "true");
  // first_mes + two alternates = 3; the world greeting featuring her is a
  // reference out of the card, not one of its greetings
  await waitFor(() => expect(screen.getByRole("tab", { name: /^greetings 3$/i })).toBeInTheDocument());
  expect(screen.getByRole("tab", { name: /^art 2$/i })).toBeInTheDocument();   // avatar + one gallery
});

test("the description carries what it costs, every turn", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", images: [],
                 card: { ...CARD, data: { ...CARD.data, description: "x".repeat(400) } } }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  // An estimate, and marked as one: there is no tokenizer in the browser.
  expect(screen.getByText(/≈ 100 tokens · sent every turn she is in scene/i)).toBeInTheDocument();
});

test("the tab survives a version switch and resets on a different character", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "winifred", name: "Winifred", default_version: "default", versions: [] },
  ]);
  (api.readCharacter as any).mockImplementation((_s: unknown, id: string) =>
    Promise.resolve(id === "winifred"
      ? { meta: { id, name: "Winifred", default_version: "default" },
          versions: [{ id: "default", name: "default", images: [],
                       card: { ...CARD, data: { ...CARD.data, name: "Winifred" } } }] }
      : TWO_VERSIONS));
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();

  await openTab(/^art/i);
  // comparing two versions' art is exactly what the version list is for
  fireEvent.click(screen.getByRole("button", { name: "Winter" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: /^art/i }))
    .toHaveAttribute("aria-selected", "true"));

  // a different character is a different record, and opens on her card
  fireEvent.click(screen.getByRole("button", { name: /all characters/i }));
  await openDetail("Winifred");
  expect(screen.getByRole("tab", { name: /^card$/i })).toHaveAttribute("aria-selected", "true");
});

test("the lore tab is offered only when there is a lore view to route to", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "pact", name: "The Pact", owners: "characters:seraphine" },
    { id: "tide", name: "The Tide", owners: "characters:other" },
  ]);
  const onOpenLore = vi.fn();
  const { unmount } = render(
    <CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" onOpenLore={onOpenLore} />);
  await openDetail();
  // counted by owner, so a world's other lore does not inflate her tab
  expect(await screen.findByRole("tab", { name: /^lore 1$/i })).toBeInTheDocument();
  unmount();

  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  expect(screen.queryByRole("tab", { name: /^lore/i })).toBeNull();
});

test("the description field belongs to one image, not to a slot in the shelf", async () => {
  // Two versions, each with an image of the same name and a different state:
  // described on one, never reviewed on the other. Unkeyed, the field is the
  // same React element in the same position, so switching version left the
  // first version's text sitting in the second version's field -- and Save
  // would have written it there.
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "default", card: CARD, images: ["gallery_1"],
        image_descriptions: { gallery_1: "Half-plate, rain-soaked." } },
      { id: "winter", name: "Winter", card: CARD, images: ["gallery_1"] },
    ],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openDetail();
  await openTab(/^art/i);
  await screen.findByText("Images");

  fireEvent.click(screen.getByRole("button", { name: /Description of gallery_1/ }));
  const box = await screen.findByRole("textbox", { name: /Description of gallery_1/ });
  fireEvent.change(box, { target: { value: "A draft nobody asked to keep." } });

  fireEvent.click(screen.getByRole("button", { name: "Winter" }));
  await openTab(/^art/i);
  await waitFor(() => expect(
    screen.queryByRole("textbox", { name: /Description of gallery_1/ })).toBeNull());
  expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
    .toHaveTextContent("Describe…");
});

test("an open describe queue does not follow the page to another scope", async () => {
  // `items` is seeded once, but each Save addresses whatever `scope` currently
  // says -- so a queue left open across a switch of world (or into a campaign)
  // would write this reader's sentence onto a same-named image belonging to
  // somewhere else. The switch retires the queue instead.
  (api.listUndescribedImages as any).mockImplementation((sc: any) =>
    Promise.resolve([{ kind: "characters", id: "seraphine", vid: "default",
                      name: "gallery_1", record_name: `Whoever ${sc.id} means`,
                      url: "/img/1" }]));

  const { rerender } = render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /Describe images/ }));
  await screen.findByText(/Describing 1 \/ 1 — Whoever w means/);
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }),
                   { target: { value: "Words about the other world's picture." } });

  rerender(<CharacterEditor scope={{ kind: "world", id: "other" }} wid="other" />);
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull());
  // ...and the new scope's own backlog is what is offered from here on
  expect(await screen.findByRole("button", { name: /Describe images \(1\)/ }))
    .toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Describe images/ }));
  expect(await screen.findByText(/Describing 1 \/ 1 — Whoever other means/))
    .toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue("");
});


test("campaign scope offers a delete, and says which library it removes from (#60)", async () => {
  // creating an emergent NPC with no way to remove it is not a finished
  // feature: the Delete controls used to be world-scope only
  (api.listAppearances as any).mockResolvedValue([]);   // nobody has appeared yet
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "All (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));

  expect(confirm).toHaveBeenCalledWith("Delete character 'Seraphine' from this campaign?");
  await waitFor(() => expect(api.deleteCharacter)
    .toHaveBeenCalledWith({ kind: "campaign", id: "run" }, "seraphine"));
});

// ---- the voice-anchor backlog (#voice) ----
//
// The COUNT moved to the To do page (`world-anchors`), where it is one chore
// among the rest, computed server-side over the whole library rather than
// inferred from whichever roster this page happens to be showing. What stays
// here is the rule that outlived it: anchors are never bulk-filled.
test("the toolbar does not report the anchor backlog", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", has_voice_anchor: false, versions: [] },
    { id: "winifred", name: "Winifred", default_version: "default", has_voice_anchor: true, versions: [] },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByText(/no voice anchor/i)).toBeNull();
});

test("no bulk-generate button is offered for anchors", async () => {
  // The one that must not be lost with the count. An anchor steers every scene
  // the character appears in, so a roster-wide unattended derive would write
  // inferred voices into the prompt with the same authority as hand-written
  // ones, at a volume nobody will review afterwards. Taglines have such a
  // button; anchors deliberately do not.
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", has_voice_anchor: false, versions: [] },
  ]);
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Mara");
  expect(screen.queryByRole("button", { name: /anchors \(/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /derive anchors/i })).toBeNull();
});

test("the over-cap warning uses the cap the server reported", async () => {
  (api.getConfig as any).mockResolvedValue({ voice_anchor_cap: 10 });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "x".repeat(11) });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByDisplayValue("x".repeat(11));
  expect(await screen.findByText(/Over 10 characters/)).toBeTruthy();
});

test("astral characters count as one each, not two", async () => {
  // Six emoji: six code points, twelve UTF-16 units. A naive `.length` warns
  // here, and would fire at half the real cap on any anchor containing emoji.
  const emoji = String.fromCodePoint(0x1F600).repeat(6);
  (api.getConfig as any).mockResolvedValue({ voice_anchor_cap: 10 });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: emoji });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  await screen.findByDisplayValue(emoji);
  expect(screen.queryByText(/Over 10 characters/)).toBeNull();
});

test("saving an anchor still returns to a freshly-read roster", async () => {
  // What the removed backlog-count test was really pinning: `backToGrid`
  // re-reads rather than patching the row it just saved. The count it used to
  // be observed through has moved to the To do page, but the reload is what
  // keeps every other roster-derived figure -- the tagline backlog beside it --
  // from being observed stale.
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "" });
  (api.setCharacterVoiceAnchor as any).mockResolvedValue({ ok: true });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await openEditForm();
  fireEvent.change(await screen.findByLabelText("Voice anchor"),
                   { target: { value: "Clipped. Never contracts." } });
  fireEvent.click(screen.getByText("Save voice anchor"));
  const before = (api.listCharacters as any).mock.calls.length;
  fireEvent.click(await screen.findByText("‹ All characters"));
  await waitFor(() => expect((api.listCharacters as any).mock.calls.length)
    .toBeGreaterThan(before));
});
