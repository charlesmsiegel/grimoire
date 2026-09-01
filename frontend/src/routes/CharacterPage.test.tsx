import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CharacterPage from "./CharacterPage";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string) =>
        `/img/${sc.id}/${k}/${a}/${v}/${n}`,
      exportUrl: (w: string, c: string, v: string, f: string) => `/export/${w}/${c}/${v}/${f}`,
      imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
      readCharacter: vi.fn(), listCharacters: vi.fn(), deleteCharacter: vi.fn(),
      updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
      setCharacterName: vi.fn(), setCharacterBirthdate: vi.fn(), getCalendarMonths: vi.fn(),
      importCharacter: vi.fn(), localizeImages: vi.fn(),
      putImage: vi.fn(), deleteImage: vi.fn(), promoteImage: vi.fn(), setAvatarFocus: vi.fn(),
      setCharacterImageDescription: vi.fn(), draftCharacterImageDescription: vi.fn(),
      lorebookParse: vi.fn(), lorebookImport: vi.fn(), entityKinds: vi.fn(),
      importCharacterFromChub: vi.fn(), setCharacterChubSource: vi.fn(),
      clearCharacterChubSource: vi.fn(), downloadCharacterChubGallery: vi.fn(),
      downloadCharacterChubLorebooks: vi.fn(),
      getCharacterTagline: vi.fn(), setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
      getCharacterVoiceAnchor: vi.fn(), setCharacterVoiceAnchor: vi.fn(),
      generateCharacterVoiceAnchor: vi.fn(),
      listImageAppearances: vi.fn(), copyGreetingImage: vi.fn(), listGreetings: vi.fn(),
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
      getCampaign: vi.fn(), getCampaignModule: vi.fn(), getCasefile: vi.fn(),
      listEntities: vi.fn(), getConfig: vi.fn(), readModule: vi.fn(),
      getWorldSheetsIndex: vi.fn(), listModules: vi.fn(),
      libraryStatus: vi.fn(), promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
    },
  };
});
import { api } from "../api/client";

const GREG_MONTHS = [
  { key: "01", name: "January", days: 31 }, { key: "02", name: "February", days: 28 },
  { key: "03", name: "March", days: 31 }, { key: "04", name: "April", days: 30 },
  { key: "05", name: "May", days: 31 }, { key: "06", name: "June", days: 30 },
  { key: "07", name: "July", days: 31 }, { key: "08", name: "August", days: 31 },
  { key: "09", name: "September", days: 30 }, { key: "10", name: "October", days: 31 },
  { key: "11", name: "November", days: 30 }, { key: "12", name: "December", days: 31 },
];

const card = (over: Record<string, unknown> = {}) => ({
  spec: "chara_card_v3", spec_version: "3.0",
  data: {
    name: "Seraphine", description: "keeper of the tide gate",
    personality: "guarded", scenario: "the priory's debt",
    first_mes: "You are early.", alternate_greetings: ["Rain on the shutters."],
    tags: ["priory", "tide"], extensions: {},
    character_book: { entries: [{ keys: ["pact"], content: "x" }] },
    ...over,
  },
});

const DETAIL = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default", birthdate: "" },
  // Two versions with DISTINCT labels — the whole point of the label chain. It
  // used to fall back to the card's name, so both of these read "Seraphine".
  versions: [
    { id: "default", name: "default", card: card(), images: ["avatar"],
      image_v: { avatar: "a1" }, image_descriptions: {}, importable_lore: 1 },
    { id: "veiled", name: "veiled", card: card({ description: "veiled" }), images: [],
      image_v: {}, image_descriptions: {}, importable_lore: 0 },
  ],
};

const CASEFILE = {
  kind: "characters", id: "seraphine", name: "Seraphine", version: "default", role: "npc",
  scenes: ["001--the-tide-gate", "004--the-priory-door"],
  last_seen: "004--the-priory-door",
  standing: "Guarded. Will not be alone with the Reeve.",
  knows: "The priory's debt.", suspects: "",
  dossier: "A novice who counts the tide instead of the hours.",
  tagline: "", feels_toward: [], standing_facts: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.readCharacter as any).mockResolvedValue(DETAIL);
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  (api.updateVersion as any).mockResolvedValue({ ok: true });
  (api.createVersion as any).mockResolvedValue({ version: "young" });
  (api.setDefaultVersion as any).mockResolvedValue({ ok: true });
  (api.setCharacterName as any).mockResolvedValue({ ok: true });
  (api.setCharacterBirthdate as any).mockResolvedValue({ ok: true });
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "" });
  (api.setCharacterVoiceAnchor as any).mockResolvedValue({ ok: true });
  (api.listGreetings as any).mockResolvedValue([]);
  (api.listImageAppearances as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.getCasefile as any).mockResolvedValue(CASEFILE);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "The Long Tide", world: "realm" } });
  (api.getCampaignModule as any).mockResolvedValue({ resolved: null });
  (api.getConfig as any).mockResolvedValue({ voice_anchor_cap: 400 });
  (api.getWorldSheetsIndex as any).mockResolvedValue({ default: "", modules: [] });
  (api.listModules as any).mockResolvedValue([]);
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
  (api.entityKinds as any).mockResolvedValue({ kinds: ["locations", "lore", "items"] });
  (api.libraryStatus as any).mockResolvedValue(
    { in_library: true, diverged: false, can_promote: false, can_push: false });
  (api.localizeImages as any).mockResolvedValue(undefined);
  (api.importCharacter as any).mockResolvedValue({ character: "seraphine", version: "young" });
});

/** The address bar, so a test can assert where a click went. */
let lastLocation = "";
function Spy() {
  const loc = useLocation();
  lastLocation = loc.pathname + loc.search;
  return null;
}

async function renderWorld(url = "/worlds/realm/characters/seraphine") {
  lastLocation = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <Spy />
      <Routes>
        <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Seraphine", level: 1 });
}

async function renderCampaign(url = "/campaigns/run/characters/seraphine") {
  lastLocation = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <Spy />
      <Routes>
        <Route path="/campaigns/:cid/characters/:eid" element={<CharacterPage campaign />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Seraphine", level: 1 });
}

/** The card field with this label, whether it is being read or edited. */
function fieldBlock(label: string): HTMLElement {
  const head = screen.getAllByText(label, { selector: ".data-label" })[0];
  return head.closest(".card-field") as HTMLElement;
}

async function editField(label: string) {
  fireEvent.click(within(fieldBlock(label)).getByRole("button", { name: /^(edit|add)$/i }));
  return await screen.findByRole("textbox", { name: label });
}

// ---------------------------------------------------------------- the layout

test("the page is one column plus main, not a pane nested in another page", async () => {
  await renderWorld();
  // `PageShell`'s column, and nothing that would be a second record surface
  // beside it. The three-pane grid this replaced is what collapsed the card to
  // 113px at 1280px.
  expect(document.querySelectorAll(".context-column")).toHaveLength(1);
  expect(document.querySelector(".char-detail")).toBeNull();
  expect(document.querySelector(".campaign-pane")).toBeNull();
});

test("the card opens read-only: rendered prose, no textarea", async () => {
  await renderWorld();
  expect(within(fieldBlock("Description")).getByText("keeper of the tide gate")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
});

test("the tabs count the card's own greetings and art", async () => {
  await renderWorld();
  // One first message plus one alternate; one avatar and no gallery.
  expect(screen.getByRole("tab", { name: /Greetings 2/ })).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Art 1/ })).toBeTruthy();
});

test("the description carries what it costs, every turn", async () => {
  await renderWorld();
  expect(within(fieldBlock("Description")).getByText(/sent every turn in scene/i)).toBeTruthy();
});

// ------------------------------------------------------------ editing inline

test("Edit swaps one field for a textarea and Save writes the card back", async () => {
  await renderWorld();
  const box = await editField("Personality");
  fireEvent.change(box, { target: { value: "guarded, and slower than she was" } });
  fireEvent.click(within(fieldBlock("Personality")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  const [, , , sent] = (api.updateVersion as any).mock.calls[0];
  expect(sent.data.personality).toBe("guarded, and slower than she was");
  // The rest of the card rides along untouched — a field save is a whole-card
  // PUT, which is exactly why only one field may be open at a time.
  expect(sent.data.description).toBe("keeper of the tide gate");
  expect(sent.data.alternate_greetings).toEqual(["Rain on the shutters."]);
});

test("Cancel discards the draft and writes nothing", async () => {
  await renderWorld();
  const box = await editField("Personality");
  fireEvent.change(box, { target: { value: "never mind" } });
  fireEvent.click(within(fieldBlock("Personality")).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("textbox", { name: "Personality" })).toBeNull());
  expect(api.updateVersion).not.toHaveBeenCalled();
  expect(within(fieldBlock("Personality")).getByText("guarded")).toBeTruthy();
});

test("opening a second field closes the first", async () => {
  await renderWorld();
  await editField("Personality");
  await editField("Scenario");
  // Two open at once is how a whole-card PUT built from one clobbers the other.
  expect(screen.queryByRole("textbox", { name: "Personality" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Scenario" })).toBeTruthy();
});

test("a field that failed to save stays open, holding the draft", async () => {
  (api.updateVersion as any).mockRejectedValue(new Error("disk full"));
  await renderWorld();
  const box = await editField("Personality");
  fireEvent.change(box, { target: { value: "kept" } });
  fireEvent.click(within(fieldBlock("Personality")).getByRole("button", { name: "Save" }));
  await screen.findByText(/disk full/);
  expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Personality" }).value)
    .toBe("kept");
});

test("an empty field offers Add rather than Edit, and says it is unset", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], card: card({ scenario: "" }) }],
  });
  await renderWorld();
  expect(within(fieldBlock("Scenario")).getByRole("button", { name: "Add" })).toBeTruthy();
});

test("tags edit as text and read back as chips", async () => {
  await renderWorld();
  expect(within(fieldBlock("Tags")).getByText("priory")).toBeTruthy();
  const box = await editField("Tags");
  fireEvent.change(box, { target: { value: "priory, tide, ledger" } });
  fireEvent.click(within(fieldBlock("Tags")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect((api.updateVersion as any).mock.calls[0][3].data.tags).toEqual(["priory", "tide", "ledger"]);
});

// ----------------------------------------------------------------- the name

test("renaming the card from the default version renames the character too", async () => {
  await renderWorld();
  const box = await editField("Name");
  fireEvent.change(box, { target: { value: "Seraphine Vale" } });
  fireEvent.click(within(fieldBlock("Name")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setCharacterName).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "seraphine", "Seraphine Vale"));
});

test("saving an unchanged name does not rename the character", async () => {
  await renderWorld();
  const box = await editField("Name");
  fireEvent.change(box, { target: { value: "Seraphine" } });
  fireEvent.click(within(fieldBlock("Name")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

test("a sibling version's card name is that version's business, not the character's", async () => {
  await renderWorld("/worlds/realm/characters/seraphine?v=veiled");
  const box = await editField("Name");
  fireEvent.change(box, { target: { value: "The Veiled One" } });
  fireEvent.click(within(fieldBlock("Name")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(api.setCharacterName).not.toHaveBeenCalled();
});

// -------------------------------------------------------------- the versions

test("versions are listed by their own labels, not by the card's name", async () => {
  await renderWorld();
  const list = document.querySelector(".version-list") as HTMLElement;
  expect([...list.querySelectorAll(".version-pick")].map((b) => b.textContent))
    .toEqual(["default", "veiled"]);
});

test("?v= opens that version, and picking one moves the URL", async () => {
  await renderWorld("/worlds/realm/characters/seraphine?v=veiled");
  expect(within(fieldBlock("Description")).getByText("veiled")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "default" }));
  await waitFor(() => expect(lastLocation).toBe("/worlds/realm/characters/seraphine?v=default"));
});

test("renaming a version stores the label on the card, so it survives a re-read", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: /Rename veiled/i }));
  const input = await screen.findByRole("textbox", { name: /Rename version veiled/i });
  fireEvent.change(input, { target: { value: "veiled · after the vow" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  const [scope, cid, vid, sent] = (api.updateVersion as any).mock.calls[0];
  expect([scope, cid, vid]).toEqual([{ kind: "world", id: "realm" }, "seraphine", "veiled"]);
  expect(sent.data.extensions.grimoire_label).toBe("veiled · after the vow");
});

test("a blank rename clears the label rather than storing an empty one", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [DETAIL.versions[0], {
      ...DETAIL.versions[1],
      card: card({ description: "veiled", extensions: { grimoire_label: "veiled" } }),
    }],
  });
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: /Rename veiled/i }));
  const input = await screen.findByRole("textbox", { name: /Rename version veiled/i });
  fireEvent.change(input, { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  // Deleted, not blanked: the backend's fallback chain then supplies a label,
  // and a stored "" would beat it to nothing on screen.
  expect((api.updateVersion as any).mock.calls[0][3].data.extensions)
    .not.toHaveProperty("grimoire_label");
});

test("+ New version names the version it creates", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("young");
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ New version" }));
  await waitFor(() => expect(api.createVersion).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "seraphine", expect.objectContaining({ name: "young" })));
});

test("Set default is offered only for a version that is not already it", async () => {
  await renderWorld();
  expect(screen.queryByRole("button", { name: "Set default" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  await screen.findByRole("button", { name: "Set default" });
});

test("importing a version asks what to call it and sends that name", async () => {
  await renderWorld();
  const input = document.querySelector('input[aria-label="Import version"]')!;
  const file = new File(["{}"], "elder.json", { type: "application/json" });
  fireEvent.change(input, { target: { files: [file] } });
  const name = await screen.findByRole("textbox", { name: "Version name" });
  // Pre-targeted at the open character, so there is no picker to answer.
  expect(screen.queryByRole("searchbox", { name: /find a character/i })).toBeNull();
  fireEvent.change(name, { target: { value: "elder" } });
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith(
    "realm", file, "json", "seraphine", "elder"));
});

test("importing a version from a URL sends the URL and the name", async () => {
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "seraphine", version: "after-the-flood", updated: false,
    gallery: { attempted: 0, stored: 0 }, lore: { lorebooks_found: 0, created: [] },
  });
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ From URL…" }));
  const url = await screen.findByRole("textbox", { name: "Card URL" });
  fireEvent.change(url, { target: { value: "creator/seraphine" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Version name" }),
                  { target: { value: "after the flood" } });
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  // No `into_version`: a URL import forks a version rather than overwriting
  // whichever one happens to be open.
  await waitFor(() => expect(api.importCharacterFromChub).toHaveBeenCalledWith(
    "realm", "creator/seraphine", "seraphine", undefined, "after the flood"));
});

test("a URL import cannot be submitted until a URL is typed", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ From URL…" }));
  const submit = await screen.findByRole("button", { name: "Import" });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Card URL" }),
                  { target: { value: "creator/seraphine" } });
  expect(submit).not.toBeDisabled();
});

// ------------------------------------------------------------- the two scopes

test("world scope renders no campaign section at all", async () => {
  await renderWorld();
  // The pane this replaced spent 300px in world scope explaining that there was
  // no campaign to report on. An absent section says the same and costs nothing.
  expect(screen.queryByText(/no campaign in scope/i)).toBeNull();
  expect(screen.queryByText(/Campaign-local/i)).toBeNull();
});

test("campaign scope reads the character's state through the newest scene they are cast in", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default",
      scenes: ["001--the-tide-gate", "004--the-priory-door"] },
  ]);
  await renderCampaign();
  await screen.findByText("A novice who counts the tide instead of the hours.");
  expect(api.getCasefile).toHaveBeenCalledWith("run", "004--the-priory-door", "characters", "seraphine");
  expect(screen.getByText("In The Long Tide")).toBeTruthy();
});

test("a character the campaign has never played is not asked about", async () => {
  await renderCampaign();
  await screen.findByText(/has not been in a scene/i);
  expect(api.getCasefile).not.toHaveBeenCalled();
});

test("an unreadable casefile still reports the scenes", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", scenes: ["001--the-tide-gate"] },
  ]);
  (api.getCasefile as any).mockRejectedValue(new Error("gone"));
  await renderCampaign();
  await screen.findByText(/Could not read Seraphine's state/i);
  expect(screen.getByText("Scene 1")).toBeTruthy();
});

test("the reach warning points the opposite way in each scope", async () => {
  await renderWorld();
  expect(screen.getByText(/reach every campaign using this world/i)).toBeTruthy();
});

test("campaign scope says the edit stays in the campaign", async () => {
  await renderCampaign();
  expect(screen.getByText(/leave the world record untouched/i)).toBeTruthy();
});

test("the locked version is marked with the campaign that locked it", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "veiled", scenes: ["001--x"] },
  ]);
  await renderCampaign();
  await screen.findByText("Locked in The Long Tide");
});

test("export is world-only — the route is world-scoped", async () => {
  await renderWorld();
  expect(screen.getByText("Export")).toBeTruthy();
  screen.getByRole("link", { name: "JSON" });
});

test("campaign scope offers no export", async () => {
  await renderCampaign();
  expect(screen.queryByText("Export")).toBeNull();
});

test("delete says which library it removes from", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  await renderCampaign();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalled());
  expect(confirm.mock.calls[0][0]).toMatch(/from this campaign/i);
});

// ------------------------------------------------------------- voice anchor

test("the voice anchor loads, and a blank save is the documented opt-out", async () => {
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Short declaratives." });
  await renderWorld();
  const anchor = (await screen.findByText("Short declaratives."))
    .closest(".column-section") as HTMLElement;
  fireEvent.click(within(anchor).getByRole("button", { name: "Edit" }));
  const box = await screen.findByRole("textbox", { name: "Voice anchor" });
  fireEvent.change(box, { target: { value: "  " } });
  fireEvent.click(within(box.closest(".column-section") as HTMLElement)
    .getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setCharacterVoiceAnchor).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "seraphine", ""));
});

test("a failed anchor read disables editing rather than offering a blank to save", async () => {
  (api.getCharacterVoiceAnchor as any).mockRejectedValue(new Error("nope"));
  await renderWorld();
  const failed = (await screen.findByText(/Could not read the voice anchor/i))
    .closest(".column-section") as HTMLElement;
  expect(within(failed).getByRole("button", { name: "Write one" })).toBeDisabled();
});

test("an empty generated anchor is a failure, not a draft", async () => {
  (api.generateCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "  " });
  await renderWorld();
  const anchor = (await screen.findByText(/not judged for voice drift/i))
    .closest(".column-section") as HTMLElement;
  fireEvent.click(within(anchor).getByRole("button", { name: "Generate" }));
  await screen.findByText(/returned an empty voice anchor/i);
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();
});

test("a generated anchor opens for review rather than being persisted", async () => {
  (api.generateCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "Clipped." });
  await renderWorld();
  const anchor = (await screen.findByText(/not judged for voice drift/i))
    .closest(".column-section") as HTMLElement;
  fireEvent.click(within(anchor).getByRole("button", { name: "Generate" }));
  const box = await screen.findByRole("textbox", { name: "Voice anchor" });
  expect((box as HTMLTextAreaElement).value).toBe("Clipped.");
  expect(api.setCharacterVoiceAnchor).not.toHaveBeenCalled();
});

test("the over-cap warning uses the cap the server reported, counting code points", async () => {
  (api.getConfig as any).mockResolvedValue({ voice_anchor_cap: 4 });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "𐐷𐐷𐐷" });
  await renderWorld();
  const anchor = (await screen.findByText("𐐷𐐷𐐷"))
    .closest(".column-section") as HTMLElement;
  fireEvent.click(within(anchor).getByRole("button", { name: "Edit" }));
  await screen.findByRole("textbox", { name: "Voice anchor" });
  // Three astral characters are three, not the six UTF-16 units `.length` sees,
  // so a cap of 4 is not exceeded.
  expect(screen.queryByText(/Over 4 characters/)).toBeNull();
});

// ----------------------------------------------------------------- tagline

test("the tagline is world-scoped and saves through its own route", async () => {
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "Counts the tide." });
  await renderWorld();
  const block = (await screen.findByText("Counts the tide."))
    .closest(".identity-tagline") as HTMLElement;
  fireEvent.click(within(block).getByRole("button", { name: "Edit" }));
  const box = await screen.findByRole("textbox", { name: "Tagline" });
  fireEvent.change(box, { target: { value: "Counts the tide, not the hours." } });
  fireEvent.click(within(box.closest(".identity-tagline") as HTMLElement)
    .getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith(
    "realm", "seraphine", "Counts the tide, not the hours."));
});

test("campaign scope has no tagline control — the route is world-scoped", async () => {
  await renderCampaign();
  expect(screen.queryByRole("textbox", { name: "Tagline" })).toBeNull();
  expect(api.getCharacterTagline).not.toHaveBeenCalled();
});

// ------------------------------------------------------------------- tabs

test("the greetings tab shows the first message and each alternate", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  await screen.findByText("You are early.");
  expect(screen.getByText("Rain on the shutters.")).toBeTruthy();
});

test("a card with no greetings says what a greeting is for", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], card: card({ first_mes: "", alternate_greetings: [] }) }],
  });
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  await screen.findByText(/opening a scene can start from/i);
});

test("editing the first message writes it back to the card", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  const box = await editField("First message");
  fireEvent.change(box, { target: { value: "You are late." } });
  fireEvent.click(within(fieldBlock("First message")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect((api.updateVersion as any).mock.calls[0][3].data.first_mes).toBe("You are late.");
});

test("the art tab shows the avatar tile and offers a description per image", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], images: ["avatar", "gallery_1"],
                 image_v: { avatar: "a1", gallery_1: "g1" },
                 image_descriptions: { avatar: "a novice at the gate" } }],
  });
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Art/ }));
  await screen.findByText("avatar");
  expect(screen.getByAltText("gallery_1")).toBeTruthy();
  expect(screen.getByText("a novice at the gate")).toBeTruthy();
});

test("promoting a gallery image re-reads the character, so the avatar is not the old one", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], images: ["avatar", "gallery_1"],
                 image_v: { avatar: "a1", gallery_1: "g1" }, image_descriptions: {} }],
  });
  (api.promoteImage as any).mockResolvedValue({ ok: true });
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Art/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Set as avatar" }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "seraphine", "default", "gallery_1"));
  expect((api.readCharacter as any).mock.calls.length).toBeGreaterThan(1);
});

test("the lore tab is counted from the entries that name this character as owner", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "the-pact", name: "The Pact", owners: "characters:seraphine" },
    { id: "the-tide", name: "The Tide", owners: "characters:mara" },
  ]);
  await renderWorld();
  await screen.findByRole("tab", { name: /Lore 1/ });
});

// ------------------------------------------------------------ embedded lore

test("the embedded book opens a review table and commits with per-entry categories", async () => {
  (api.lorebookParse as any).mockResolvedValue(
    { entries: [{ name: "pact", keys: ["pact"], body: "x", category: "lore" }] });
  (api.lorebookImport as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
  await renderWorld();
  fireEvent.click(await screen.findByRole("button", { name: /Review 1 embedded lore entry/i }));
  await screen.findByText(/Review and route each entry/i);
  fireEvent.click(screen.getByRole("button", { name: /^Import 1 entry$/i }));
  await waitFor(() => expect(api.lorebookImport).toHaveBeenCalled());
  await screen.findByText(/Imported 1 entry/i);
});

test("re-importing an unchanged book says so instead of reporting zero", async () => {
  (api.lorebookImport as any).mockResolvedValue({ created: [] });
  await renderWorld();
  fireEvent.click(await screen.findByRole("button", { name: /Review 1 embedded lore entry/i }));
  await screen.findByText(/Review and route each entry/i);
  fireEvent.click(screen.getByRole("button", { name: /^Import 1 entry$/i }));
  await screen.findByText(/Already in the world/i);
});

test("the embedded-lore button follows the version and vanishes when it offers nothing", async () => {
  await renderWorld("/worlds/realm/characters/seraphine?v=veiled");
  expect(screen.queryByRole("button", { name: /embedded lore/i })).toBeNull();
});

// ----------------------------------------------------------------- chub

test("linking a version to a URL shows a clickable link and allows unlinking", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/seraphine");
  (api.setCharacterChubSource as any).mockResolvedValue({ ok: true });
  (api.readCharacter as any)
    .mockResolvedValueOnce(DETAIL)
    .mockResolvedValue({
      ...DETAIL,
      versions: [{ ...DETAIL.versions[0], chub_source: "creator/seraphine", is_chub: true },
                 DETAIL.versions[1]],
    });
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: "Link to URL" }));
  const link = await screen.findByRole("link", { name: "creator/seraphine" });
  expect(link.getAttribute("href")).toBe("https://chub.ai/characters/creator/seraphine");
  expect(screen.getByRole("button", { name: "Download gallery" })).toBeTruthy();
});

test("a sibling version does not show another version's chub link", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], chub_source: "creator/seraphine", is_chub: true },
               DETAIL.versions[1]],
  });
  await renderWorld("/worlds/realm/characters/seraphine?v=veiled");
  expect(screen.queryByRole("link", { name: "creator/seraphine" })).toBeNull();
  expect(screen.getByRole("button", { name: "Link to URL" })).toBeTruthy();
});

// ------------------------------------------------------------------ misc

test("a character that cannot be read reports the reason instead of hanging", async () => {
  (api.readCharacter as any).mockRejectedValue(new Error("no such character"));
  render(
    <MemoryRouter initialEntries={["/worlds/realm/characters/ghost"]}>
      <Routes><Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} /></Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/no such character/i);
});

test("back returns to the roster, in this scope", async () => {
  await renderCampaign();
  expect(screen.getByRole("link", { name: /All characters/ }).getAttribute("href"))
    .toBe("/campaigns/run/world?section=characters");
});

test("the birthdate picker is world-only and persists a complete date", async () => {
  await renderCampaign();
  expect(screen.queryByLabelText(/Birthdate/)).toBeNull();
});

// ------------------------------------------- what the review found (round 2)

test("walking to another character remounts rather than reusing the page", async () => {
  // React Router reuses an element when only a param changes, and every piece
  // of this page's state is about ONE character: a stale card under the new
  // name, an editor belonging to whoever you left, a tagline read on mount and
  // saved against the current id.
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "Counts the tide." });
  render(
    <MemoryRouter initialEntries={["/worlds/realm/characters/seraphine"]}>
      <Spy />
      <Routes>
        <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Seraphine", level: 1 });
  await editField("Personality");            // an editor belonging to Seraphine

  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    meta: { ...DETAIL.meta, id: "mara", name: "Mara" },
    versions: [{ ...DETAIL.versions[0], card: card({ name: "Mara" }) }],
  });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "Keeps the ledger." });
  fireEvent.click(screen.getByRole("link", { name: /All characters/ }));  // any nav
  // Re-enter at the other character.
  await waitFor(() => expect(lastLocation).toContain("section=characters"));
});

test("a save in flight holds every other field's control", async () => {
  let release: (v: unknown) => void = () => {};
  (api.updateVersion as any).mockReturnValue(new Promise((r) => { release = r; }));
  await renderWorld();
  const box = await editField("Personality");
  fireEvent.change(box, { target: { value: "slower" } });
  fireEvent.click(within(fieldBlock("Personality")).getByRole("button", { name: "Save" }));
  // Opening a second field while the first is saving is how a whole-card PUT
  // built from the pre-save card silently drops the write that is in flight.
  await waitFor(() =>
    expect(within(fieldBlock("Scenario")).getByRole("button", { name: "Edit" })).toBeDisabled());
  await act(async () => { release({ ok: true }); });
});

test("a lore parse that lands after a version switch is dropped", async () => {
  let release: (v: unknown) => void = () => {};
  (api.lorebookParse as any).mockReturnValue(new Promise((r) => { release = r; }));
  await renderWorld();
  fireEvent.click(await screen.findByRole("button", { name: /Review 1 embedded lore entry/i }));
  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  await waitFor(() => expect(lastLocation).toContain("v=veiled"));
  await act(async () => {
    release({ entries: [{ name: "pact", keys: ["pact"], body: "x", category: "lore" }] });
  });
  // The previous version's entries must not appear under this one, where a
  // commit would file them against the wrong card.
  expect(screen.queryByText(/Review and route each entry/i)).toBeNull();
});

test("Generate is held until the anchor has actually been read", async () => {
  let release: (v: unknown) => void = () => {};
  (api.getCharacterVoiceAnchor as any).mockReturnValue(new Promise((r) => { release = r; }));
  await renderWorld();
  const anchor = (await screen.findByText("Reading…")).closest(".column-section") as HTMLElement;
  // A draft that lands first is overwritten by the read; a read that then FAILS
  // leaves the draft on screen and unsavable.
  expect(within(anchor).getByRole("button", { name: "Generate" })).toBeDisabled();
  await act(async () => { release({ voice_anchor: "" }); });
});

test("+ Add greeting writes only once there is something to write", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  fireEvent.click(await screen.findByRole("button", { name: "+ Add greeting" }));
  const box = await screen.findByRole("textbox", { name: "Alternate greeting 2" });
  // A blank placeholder saved to make the row exist would not survive the round
  // trip: `buildCard` drops empty greetings on the way out.
  expect(api.updateVersion).not.toHaveBeenCalled();
  fireEvent.change(box, { target: { value: "The council chamber." } });
  fireEvent.click(within(fieldBlock("Alternate greeting 2"))
    .getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect((api.updateVersion as any).mock.calls[0][3].data.alternate_greetings)
    .toEqual(["Rain on the shutters.", "The council chamber."]);
});

test("cancelling a new greeting leaves the card alone", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  fireEvent.click(await screen.findByRole("button", { name: "+ Add greeting" }));
  await screen.findByRole("textbox", { name: "Alternate greeting 2" });
  fireEvent.click(within(fieldBlock("Alternate greeting 2"))
    .getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Alternate greeting 2" })).toBeNull());
  expect(api.updateVersion).not.toHaveBeenCalled();
});

test("the creator is editable again, not just a byline", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ ...DETAIL.versions[0], card: card({ creator: "Saltmarch archive" }) }],
  });
  await renderWorld();
  const box = await editField("Creator");
  fireEvent.change(box, { target: { value: "The harbour board" } });
  fireEvent.click(within(fieldBlock("Creator")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect((api.updateVersion as any).mock.calls[0][3].data.creator).toBe("The harbour board");
});

test("picking a version re-reads the lock, so the page stops offering what is gone", async () => {
  // Seated but NOT locked to a version: that is the state that offers Pick.
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", scenes: ["001--x"] },
  ]);
  (api.pickVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await renderCampaign();
  await screen.findByRole("button", { name: "Pick this version" });
  fireEvent.click(screen.getByRole("button", { name: "Pick this version" }));
  // The lock lives in the appearance record, which `refresh()` does not read —
  // without a re-read the page kept offering "+ New version" and the backend
  // answered the button it should not have drawn with a 409.
  await waitFor(() => expect((api.listAppearances as any).mock.calls.length).toBeGreaterThan(1));
});

test("going back hands the grid the character, so its filter cannot swallow them", async () => {
  await renderCampaign();
  const back = screen.getByRole("link", { name: /All characters/ });
  fireEvent.click(back);
  await waitFor(() => expect(lastLocation).toBe("/campaigns/run/world?section=characters"));
});
