import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { CharacterGrid } from "./CharacterGrid";

// Counted, not replaced: one call per card render, which is how the tests at
// the bottom see whether a card re-rendered.
vi.mock("../api/thumbs", async () => {
  const actual = await vi.importActual<typeof import("../api/thumbs")>("../api/thumbs");
  return { ...actual, thumbSet: vi.fn(actual.thumbSet) };
});
import { THUMB_REV, thumbSet } from "../api/thumbs";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      // The real builder: what a card asks the route for (its `?w=` and
      // `?v=`) is the thing the portrait test below is about.
      actorImageUrl: actual.api.actorImageUrl,
      listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
      deleteCharacter: vi.fn(), importCharacter: vi.fn(), localizeImages: vi.fn(),
      importCharacterFromChub: vi.fn(), importCharacterBook: vi.fn(),
      findChubUnlinked: vi.fn(), listUndescribedImages: vi.fn(), countUndescribedImages: vi.fn(),
      rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(),
      setCharacterImageDescription: vi.fn(),
      generateWorldTaglines: vi.fn(), getCharacterTagline: vi.fn(),
      setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
      listAppearances: vi.fn(), putSheetCreation: vi.fn(),
    },
  };
});
import { api } from "../api/client";

const WORLD = { kind: "world", id: "realm" } as const;
const CAMPAIGN = { kind: "campaign", id: "run" } as const;

const row = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id, name, default_version: "default", versions: [{ id: "default", name: "default" }], ...over,
});

let lastLocation = "";
function Spy() {
  lastLocation = useLocation().pathname + useLocation().search;
  return null;
}

function renderGrid(props: Partial<Parameters<typeof CharacterGrid>[0]> = {}) {
  lastLocation = "";
  return render(
    <MemoryRouter initialEntries={["/worlds/realm"]}>
      <Spy />
      <Routes>
        <Route path="*" element={
          <CharacterGrid scope={WORLD} wid="realm" {...props} />
        } />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCharacters as any).mockResolvedValue([row("seraphine", "Seraphine")]);
  (api.listUndescribedImages as any).mockResolvedValue([]);
  (api.countUndescribedImages as any).mockResolvedValue(0);
  // Nothing remembered unless a test says so: every other test here is a first
  // visit, and a first visit paints after its read.
  (api.rememberedCharacters as any).mockReturnValue(undefined);
  (api.rememberedAppearances as any).mockReturnValue(undefined);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.createCharacter as any).mockResolvedValue({ character: "rook", version: "default" });
  (api.deleteCharacter as any).mockResolvedValue({ ok: true });
  (api.importCharacter as any).mockResolvedValue({ character: "imp", version: "default" });
  (api.readCharacter as any).mockResolvedValue({ meta: { id: "imp", name: "Imported" }, versions: [] });
  (api.localizeImages as any).mockImplementation(
    (_w: string, _c: string, _v: string, cb?: (e: any) => void) => {
      cb?.({ summary: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false } });
      return Promise.resolve();
    });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
});

// -------------------------------------------------------------------- cards

test("a card's portrait is a lazy, versioned thumbnail, never the original", async () => {
  // A world grid drew every card from its full-size original: the whole
  // roster's files moved and decoded at full resolution to fill tiles a few
  // hundred pixels wide, the ones below the fold included.
  (api.listCharacters as any).mockResolvedValue([
    row("seraphine", "Seraphine", { has_avatar: true, avatar_v: "abc" }),
    row("mara", "Mara", { has_avatar: true }),
  ]);
  const { container } = renderGrid();
  await screen.findByText("Seraphine");
  const [sera, mara] = Array.from(container.querySelectorAll(".char-card-avatar"));
  const base = "/api/worlds/realm/characters/seraphine/versions/default/images/avatar";
  expect(sera.getAttribute("src")).toBe(`${base}?w=512&t=${THUMB_REV}&v=abc`);
  expect(sera.getAttribute("srcset")).toContain(`${base}?w=1024&t=${THUMB_REV}&v=abc 682w`);
  expect(sera.getAttribute("sizes")).toMatch(/px/);
  expect(sera.getAttribute("loading")).toBe("lazy");
  expect(sera.getAttribute("decoding")).toBe("async");
  // No token, no `?v=`: a bare thumbnail revalidates rather than caching a
  // year under a name that does not pin its bytes.
  expect(mara.getAttribute("src"))
    .toBe(`/api/worlds/realm/characters/mara/versions/default/images/avatar?w=512&t=${THUMB_REV}`);
});

test("a card shows the tagline and its badges, versions included", async () => {
  (api.listCharacters as any).mockResolvedValue([
    row("seraphine", "Seraphine", {
      tagline: "Counts the tide.", greeting_count: 2, gallery_count: 3, localized_count: 1,
      versions: [{ id: "default", name: "default" }, { id: "veiled", name: "veiled" }],
    }),
  ]);
  renderGrid();
  await screen.findByText("Seraphine");
  expect(screen.getByText("Counts the tide.")).toBeTruthy();
  // The versions badge is new: a character with more than one is worth saying
  // so on the tile, now that the versions are distinguishable at all.
  expect(screen.getByText("2 versions")).toBeTruthy();
  expect(screen.getByText("2 greetings")).toBeTruthy();
  expect(screen.getByText("3 gallery")).toBeTruthy();
  // All four can show at once; the row wraps rather than dropping one.
  expect(screen.getByText("1 localized")).toBeTruthy();
});

test("badges are omitted when they would all read zero", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  expect(screen.queryByText(/gallery/)).toBeNull();
  expect(screen.queryByText(/versions/)).toBeNull();
});

test("clicking a card goes to that character's page", async () => {
  renderGrid();
  fireEvent.click(await screen.findByText("Seraphine"));
  await waitFor(() => expect(lastLocation).toBe("/worlds/realm/characters/seraphine"));
});

test("a character card is a link, which is what lets it open in a new tab", async () => {
  renderGrid();
  expect(await screen.findByRole("link", { name: /Seraphine/ }))
    .toHaveAttribute("href", "/worlds/realm/characters/seraphine");
});

test("a campaign's card points inside that campaign's world copy", async () => {
  // Filtered to the cast that has appeared by default (see the "appeared
  // filter" tests below) — give Seraphine a scene so the card renders at all.
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", scenes: ["001--x"] },
  ]);
  renderGrid({ scope: CAMPAIGN });
  expect(await screen.findByRole("link", { name: /Seraphine/ }))
    .toHaveAttribute("href", "/campaigns/run/world/characters/seraphine");
});

test("a card's Delete removes the character and says which library", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteCharacter).toHaveBeenCalledWith(WORLD, "seraphine"));
  expect(confirm.mock.calls[0][0]).toMatch(/from the library/i);
});

test("creating a character prompts, posts the name, and opens the new page", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: "+ New character" }));
  await waitFor(() => expect(api.createCharacter).toHaveBeenCalledWith(WORLD, { name: "Rook" }));
  await waitFor(() => expect(lastLocation).toBe("/worlds/realm/characters/rook"));
});

test("in campaign scope the create is campaign-local (#60)", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  render(
    <MemoryRouter><Spy />
      <CharacterGrid scope={CAMPAIGN} wid="realm" />
    </MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /New NPC \(this campaign\)/ }));
  await waitFor(() => expect(api.createCharacter).toHaveBeenCalledWith(CAMPAIGN, { name: "Rook" }));
});

// ------------------------------------------------------------------ import

test("a single card asks where it lands before importing", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  fireEvent.change(screen.getByLabelText("Import character card"),
    { target: { files: [new File(["{}"], "card.json")] } });
  await screen.findByText("Import card.json");
  // Nothing has been sent yet: the dialog is the whole point.
  expect(api.importCharacter).not.toHaveBeenCalled();
});

test("the default answer is a new character, and it sends no version name", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  const file = new File(["{}"], "card.json");
  fireEvent.change(screen.getByLabelText("Import character card"), { target: { files: [file] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  await waitFor(() => expect(api.importCharacter)
    .toHaveBeenCalledWith("realm", file, "json", undefined, undefined));
});

test("a card can be added as a NAMED version of a character found by name", async () => {
  (api.listCharacters as any).mockResolvedValue([
    row("seraphine", "Seraphine"), row("mara", "Mara Tolliver"),
  ]);
  renderGrid();
  await screen.findByText("Mara Tolliver");
  const file = new File(["{}"], "elder.png");
  fireEvent.change(screen.getByLabelText("Import character card"), { target: { files: [file] } });
  fireEvent.click(await screen.findByRole("button", { name: "A version of…" }));
  fireEvent.change(screen.getByLabelText("Find a character"), { target: { value: "mara" } });
  const target = screen.getByRole("group", { name: "Import as" }).parentElement as HTMLElement;
  fireEvent.click(within(target).getByRole("button", { name: "Mara Tolliver" }));
  fireEvent.change(screen.getByLabelText("Version name"), { target: { value: "possessed" } });
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await waitFor(() => expect(api.importCharacter)
    .toHaveBeenCalledWith("realm", file, "png", "mara", "possessed"));
});

test("cancelling the dialog imports nothing", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  fireEvent.change(screen.getByLabelText("Import character card"),
    { target: { files: [new File(["{}"], "card.json")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByText("Import card.json")).toBeNull());
  expect(api.importCharacter).not.toHaveBeenCalled();
});

test("many files skip the dialog and each becomes its own character", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  const files = [new File(["{}"], "a.json"), new File(["{}"], "b.png")];
  fireEvent.change(screen.getByLabelText("Import character card"), { target: { files } });
  // A dialog per file in a thirty-card drop is worse than no dialog.
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/^Import a\.json$/)).toBeNull();
  expect((api.importCharacter as any).mock.calls[0][2]).toBe("json");
  expect((api.importCharacter as any).mock.calls[1][2]).toBe("png");
});

test("a bulk import localizes each card and reports one summary", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  fireEvent.change(screen.getByLabelText("Import character card"),
    { target: { files: [new File(["{}"], "a.json"), new File(["{}"], "b.json")] } });
  await waitFor(() => expect(api.localizeImages).toHaveBeenCalledTimes(2));
  await screen.findByText(/across 2 cards/);
});

test("a failing file is named and the rest still import", async () => {
  (api.importCharacter as any)
    .mockRejectedValueOnce(new Error("not a card"))
    .mockResolvedValue({ character: "imp", version: "default" });
  renderGrid();
  await screen.findByText("Seraphine");
  fireEvent.change(screen.getByLabelText("Import character card"),
    { target: { files: [new File(["{}"], "bad.json"), new File(["{}"], "good.json")] } });
  await screen.findByText(/bad\.json: Error: not a card/);
  expect(api.importCharacter).toHaveBeenCalledTimes(2);
});

// ---------------------------------------------------------------- taglines

test("the derive button counts the characters with no tagline", async () => {
  (api.listCharacters as any).mockResolvedValue([
    row("a", "A", { tagline: "set" }), row("b", "B"), row("c", "C"),
  ]);
  renderGrid();
  await screen.findByRole("button", { name: /Derive taglines \(2\)/ });
});

test("no derive button when every tagline is set", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid", { tagline: "set" })]);
  renderGrid();
  await screen.findByText("Astrid");
  expect(screen.queryByRole("button", { name: /Derive taglines/ })).toBeNull();
});

test("the derive button is absent in campaign scope — a tagline is world-level", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", scenes: ["001--x"] },
  ]);
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  await screen.findByText("Seraphine");
  expect(screen.queryByRole("button", { name: /Derive taglines/ })).toBeNull();
});

test("deriving reports what was written and reloads the roster", async () => {
  (api.listCharacters as any).mockResolvedValue([row("b", "B")]);
  (api.generateWorldTaglines as any).mockImplementation(
    async (_w: string, cb: (e: any) => void) => {
      cb({ total: 1 });
      cb({ done: 1, name: "B", tagline: "a line" });
      cb({ summary: true });
    });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines \(1\)/ }));
  await screen.findByText(/Derived 1 tagline/);
  expect((api.listCharacters as any).mock.calls.length).toBeGreaterThan(1);
});

test("the report says WHY nothing was written, not just how many", async () => {
  (api.listCharacters as any).mockResolvedValue([row("b", "B")]);
  (api.generateWorldTaglines as any).mockImplementation(
    async (_w: string, cb: (e: any) => void) => {
      cb({ total: 1 });
      cb({ done: 1, name: "B", skipped: "already had one" });
      cb({ summary: true });
    });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines/ }));
  await screen.findByText(/1 already had one/);
});

test("a run stopped part-way still reports what landed and points at the re-run", async () => {
  (api.listCharacters as any).mockResolvedValue([row("b", "B"), row("c", "C")]);
  (api.generateWorldTaglines as any).mockImplementation(
    async (_w: string, cb: (e: any) => void) => {
      cb({ total: 2 });
      cb({ done: 1, name: "B", tagline: "a line" });
      cb({ error: { detail: "no key", kind: "config" } });
    });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines/ }));
  await screen.findByText(/Derived 1 tagline.*no key.*run it again/s);
});

test("a refusal before the stream starts is an error banner, not a report", async () => {
  (api.listCharacters as any).mockResolvedValue([row("b", "B")]);
  (api.generateWorldTaglines as any).mockRejectedValue(new Error("no connection"));
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines/ }));
  await screen.findByText(/no connection/);
  expect(screen.queryByText(/Derived 0 taglines/)).toBeNull();
});

test("leaving aborts the run rather than leaving it spending a call per character", async () => {
  (api.listCharacters as any).mockResolvedValue([row("b", "B")]);
  let signal: AbortSignal | undefined;
  (api.generateWorldTaglines as any).mockImplementation(
    (_w: string, cb: (e: any) => void, s: AbortSignal) => {
      signal = s;
      cb({ total: 1 });
      return new Promise(() => {});   // never settles: the run is still going
    });
  const view = renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Derive taglines/ }));
  await screen.findByText(/Deriving taglines/);
  act(() => view.unmount());
  expect(signal?.aborted).toBe(true);
});

// ------------------------------------------------------- the appeared filter

test("campaign scope opens on the cast that has appeared, and All reveals the rest", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid"), row("b", "Bram")]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "a", version: "default", scenes: ["001--x"] },
  ]);
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  await screen.findByText("Astrid");
  expect(screen.queryByText("Bram")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^All \(2\)$/ }));
  await screen.findByText("Bram");
});

test("a roster entry with no scenes has not appeared", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid")]);
  // `transitions.leave` keeps the entry and empties its scenes, because the
  // entry is also what locks a version. The grid's question is "who is in this
  // campaign", and the answer to that is the scene list.
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "a", version: "default", scenes: [] },
  ]);
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  await screen.findByText(/No one has appeared in this campaign yet/);
});

test("world scope offers no filter and reads no roster", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  expect(screen.queryByRole("group", { name: "Show" })).toBeNull();
  expect(api.listAppearances).not.toHaveBeenCalled();
});

test("a failed roster read leaves the campaign grid unfiltered", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid"), row("b", "Bram")]);
  (api.listAppearances as any).mockRejectedValue(new Error("nope"));
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  // An unreadable roster must not hide the records it was meant to narrow.
  await screen.findByText("Astrid");
  await screen.findByText("Bram");
  expect(screen.queryByRole("group", { name: "Show" })).toBeNull();
});

test("the grid waits for the roster instead of flashing every inherited character", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid"), row("b", "Bram")]);
  let release: (v: unknown) => void = () => {};
  (api.listAppearances as any).mockReturnValue(new Promise((r) => { release = r; }));
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  expect(screen.queryByText("Bram")).toBeNull();
  expect(screen.queryByText(/No one has appeared/)).toBeNull();
  await act(async () => { release([{ kind: "characters", id: "a", scenes: ["001--x"] }]); });
  await screen.findByText("Astrid");
});

test("a character handed back by their own page is not swallowed by the filter", async () => {
  (api.listCharacters as any).mockResolvedValue([row("a", "Astrid"), row("b", "Bram")]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "a", version: "default", scenes: ["001--x"] },
  ]);
  render(<MemoryRouter><Spy />
    <CharacterGrid scope={CAMPAIGN} wid="realm" reveal="b" />
  </MemoryRouter>);
  // Landing on a grid that hides them reads as the record having been deleted.
  await screen.findByText("Bram");
});

// ------------------------------------------------------------- the toolbar

test("the describe backlog is a button only when it has entries", async () => {
  (api.countUndescribedImages as any).mockResolvedValue(1);
  renderGrid();
  await screen.findByRole("button", { name: /Describe images \(1\)/ });
});

test("a visit labels the describe button from the count, not the whole backlog", async () => {
  // The backlog is every undescribed image with its record's name and URL --
  // downloaded in full on every visit, to print one number on a button.
  (api.countUndescribedImages as any).mockResolvedValue(3);
  renderGrid();
  await screen.findByRole("button", { name: /Describe images \(3\)/ });
  expect(api.countUndescribedImages).toHaveBeenCalledWith(WORLD);
  expect(api.listUndescribedImages).not.toHaveBeenCalled();
});

test("a campaign's grid counts its own backlog the same way", async () => {
  (api.countUndescribedImages as any).mockResolvedValue(2);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", scenes: ["001--x"] },
  ]);
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  await screen.findByRole("button", { name: /Describe images \(2\)/ });
  expect(api.countUndescribedImages).toHaveBeenCalledWith(CAMPAIGN);
  expect(api.listUndescribedImages).not.toHaveBeenCalled();
});

test("opening the describe queue is what fetches it, and a save recounts", async () => {
  (api.countUndescribedImages as any).mockResolvedValue(1);
  (api.listUndescribedImages as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", vid: "default", name: "avatar",
      record_name: "Seraphine", url: "/img/seraphine/avatar" },
  ]);
  (api.setCharacterImageDescription as any).mockResolvedValue({ ok: true });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: /Describe images \(1\)/ }));
  await screen.findByText(/Describing 1 \/ 1 — Seraphine/);
  expect(api.listUndescribedImages).toHaveBeenCalledWith(WORLD);
  (api.countUndescribedImages as any).mockClear();
  (api.countUndescribedImages as any).mockResolvedValue(0);
  fireEvent.click(screen.getByRole("button", { name: "No description" }));
  await waitFor(() => expect(api.countUndescribedImages).toHaveBeenCalledWith(WORLD));
  await waitFor(() => expect(screen.queryByRole("button", { name: /Describe images/ })).toBeNull());
});

test("an empty backlog shows no button", async () => {
  renderGrid();
  await screen.findByText("Seraphine");
  expect(screen.queryByRole("button", { name: /Describe images/ })).toBeNull();
});

test("checking chub links lists the unlinked versions and opens one on click", async () => {
  (api.findChubUnlinked as any).mockResolvedValue({
    versions: [{ character: "mara", character_name: "Mara", version: "futa", version_name: "futa" }],
  });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: "Check chub.ai links" }));
  // `version_name` is the version's own label now, so this no longer reads
  // "Mara (Mara)" as it did when the label fell back to the card's name.
  fireEvent.click(await screen.findByRole("link", { name: "Mara (futa)" }));
  await waitFor(() => expect(lastLocation).toBe("/worlds/realm/characters/mara?v=futa"));
});

test("checking chub links with none unlinked says so", async () => {
  (api.findChubUnlinked as any).mockResolvedValue({ versions: [] });
  renderGrid();
  fireEvent.click(await screen.findByRole("button", { name: "Check chub.ai links" }));
  await screen.findByText(/All versions are linked/);
});

test("an empty world says what to do about it", async () => {
  (api.listCharacters as any).mockResolvedValue([]);
  renderGrid();
  await screen.findByText(/No characters yet/);
});

test("world-only tooling is absent in campaign scope", async () => {
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "default", scenes: ["001--x"] },
  ]);
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  await screen.findByText("Seraphine");
  expect(screen.queryByRole("button", { name: "Import card" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Download from URL" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Check chub.ai links" })).toBeNull();
});

// ------------------------------------------------------- remembered rows

test("a revisit paints the remembered rows on its first render, then the fresh ones", async () => {
  // A world seen before used to open on an empty grid for as long as its
  // read took. The api client remembers the last answer; the grid paints it
  // at once and replaces it wholesale with the read it makes anyway.
  (api.rememberedCharacters as any).mockImplementation(
    (sc: { kind: string; id: string }) =>
      (sc.kind === "world" && sc.id === "realm" ? [row("mara", "Mara")] : undefined));
  let land: (rows: unknown) => void = () => {};
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderGrid();
  // Before any await: the read has not landed, and the cast is already there.
  expect(screen.getByText("Mara")).toBeInTheDocument();
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
  await act(async () => { land([row("seraphine", "Seraphine")]); });
  await screen.findByText("Seraphine");
  expect(screen.queryByText("Mara")).toBeNull();
});

test("remembered rows are asked for by the grid's own scope, never another's", async () => {
  (api.rememberedCharacters as any).mockImplementation(
    (sc: { kind: string; id: string }) =>
      (sc.kind === "world" && sc.id === "realm" ? [row("mara", "Mara")] : undefined));
  (api.listCharacters as any).mockReturnValue(new Promise(() => {}));
  // Same id, other kind: a campaign called "realm" is not the world "realm".
  render(<MemoryRouter><Spy />
    <CharacterGrid scope={{ kind: "campaign", id: "realm" }} wid="realm" />
  </MemoryRouter>);
  expect(screen.queryByText("Mara")).toBeNull();
  expect(api.rememberedCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "realm" });
  // Settled before the test ends, so the count landing is not an update
  // outside act.
  await waitFor(() => expect(api.countUndescribedImages).toHaveBeenCalled());
});

test("a scope switch drops the old cast in the same render, remembered or not", async () => {
  // The route keeps this grid across a world switch. Its old rows used to
  // stay up -- under the new world's links -- until the new list landed.
  const view = render(
    <MemoryRouter><Spy /><CharacterGrid scope={WORLD} wid="realm" /></MemoryRouter>);
  await screen.findByText("Seraphine");
  (api.listCharacters as any).mockReturnValue(new Promise(() => {}));
  view.rerender(
    <MemoryRouter><Spy />
      <CharacterGrid scope={{ kind: "world", id: "saltmarch" }} wid="saltmarch" />
    </MemoryRouter>);
  expect(screen.queryByText("Seraphine")).toBeNull();
  // ...and no claim that the new world is empty, either: its list is not in.
  expect(screen.queryByText(/No characters yet/)).toBeNull();
  // Settled before the test ends, so the count landing is not an update
  // outside act.
  await waitFor(() => expect(api.countUndescribedImages).toHaveBeenCalled());
});

test("a switch to a remembered scope paints that scope's rows at once", async () => {
  const view = render(
    <MemoryRouter><Spy /><CharacterGrid scope={WORLD} wid="realm" /></MemoryRouter>);
  await screen.findByText("Seraphine");
  (api.rememberedCharacters as any).mockImplementation(
    (sc: { kind: string; id: string }) => (sc.id === "saltmarch" ? [row("winifred", "Winifred")] : undefined));
  (api.listCharacters as any).mockReturnValue(new Promise(() => {}));
  view.rerender(
    <MemoryRouter><Spy />
      <CharacterGrid scope={{ kind: "world", id: "saltmarch" }} wid="saltmarch" />
    </MemoryRouter>);
  expect(screen.getByText("Winifred")).toBeInTheDocument();
  expect(screen.queryByText("Seraphine")).toBeNull();
  expect(screen.getByRole("link", { name: /Winifred/ }))
    .toHaveAttribute("href", "/worlds/saltmarch/characters/winifred");
  // Settled before the test ends, so the count landing is not an update
  // outside act.
  await waitFor(() => expect(api.countUndescribedImages).toHaveBeenCalled());
});

test("a revalidation that fails takes the remembered rows down and says why", async () => {
  (api.rememberedCharacters as any).mockReturnValue([row("mara", "Mara")]);
  (api.listCharacters as any).mockRejectedValue(new Error("store unreachable"));
  renderGrid();
  expect(screen.getByText("Mara")).toBeInTheDocument();
  await screen.findByText(/store unreachable/);
  expect(screen.queryByText("Mara")).toBeNull();
});

test("a first visit claims nothing about the roster until its read lands", async () => {
  let land: (rows: unknown) => void = () => {};
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderGrid();
  expect(screen.queryByText(/No characters yet/)).toBeNull();
  await act(async () => { land([]); });
  await screen.findByText(/No characters yet/);
});

test("a campaign revisit filters by the remembered roster on its first render", async () => {
  (api.rememberedCharacters as any).mockReturnValue([row("a", "Astrid"), row("b", "Bram")]);
  (api.rememberedAppearances as any).mockReturnValue([
    { kind: "characters", id: "a", version: "default", scenes: ["001--x"] },
  ]);
  (api.listCharacters as any).mockReturnValue(new Promise(() => {}));
  (api.listAppearances as any).mockReturnValue(new Promise(() => {}));
  render(<MemoryRouter><Spy /><CharacterGrid scope={CAMPAIGN} wid="realm" /></MemoryRouter>);
  expect(screen.getByText("Astrid")).toBeInTheDocument();
  expect(screen.queryByText("Bram")).toBeNull();
  // Settled before the test ends, so the count landing is not an update
  // outside act.
  await waitFor(() => expect(api.countUndescribedImages).toHaveBeenCalled());
});

test("the grid says when its list has landed, and not before", async () => {
  let land: (rows: unknown) => void = () => {};
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { land = r; }));
  const onListed = vi.fn();
  renderGrid({ onListed });
  expect(onListed).not.toHaveBeenCalled();
  await act(async () => { land([row("seraphine", "Seraphine")]); });
  await screen.findByText("Seraphine");
  expect(onListed).toHaveBeenCalled();
});

// ------------------------------------------------------------ re-renders

test("a revalidation that confirms the painted rows re-renders no card", async () => {
  // One `thumbSet` call per card render. The remembered paint renders each
  // card once; the read that answers with the same rows (as new objects), and
  // the describe count landing beside it, must not render them again.
  const rows = [
    row("seraphine", "Seraphine", { has_avatar: true, avatar_v: "a1" }),
    row("mara", "Mara", { has_avatar: true, avatar_v: "b2" }),
  ];
  (api.rememberedCharacters as any).mockReturnValue(rows);
  (api.listCharacters as any).mockResolvedValue(JSON.parse(JSON.stringify(rows)));
  (api.countUndescribedImages as any).mockResolvedValue(4);
  (thumbSet as any).mockClear();
  renderGrid();
  await screen.findByRole("button", { name: /Describe images \(4\)/ });
  expect(api.listCharacters).toHaveBeenCalled();
  expect((thumbSet as any).mock.calls.length).toBe(2);
});

test("a revalidation that changes one card re-renders that card", async () => {
  const rows = [
    row("seraphine", "Seraphine", { has_avatar: true, avatar_v: "a1" }),
    row("mara", "Mara", { has_avatar: true, avatar_v: "b2" }),
  ];
  (api.rememberedCharacters as any).mockReturnValue(rows);
  const fresh = JSON.parse(JSON.stringify(rows));
  fresh[1].avatar_v = "b3";
  (api.listCharacters as any).mockResolvedValue(fresh);
  (thumbSet as any).mockClear();
  const { container } = renderGrid();
  await waitFor(() => expect(container.querySelectorAll(".char-card-avatar")[1].getAttribute("src"))
    .toContain("v=b3"));
  expect((thumbSet as any).mock.calls.length).toBe(3);
});

test("a long roster paints its first cards at once and the rest right after", async () => {
  // A roster is paid for all at once, and on a phone-class CPU the first card
  // used to wait for the last. The first commit draws a screenful; the rest
  // follow once that has painted.
  const many = Array.from({ length: 40 }, (_, i) => row(`c${i}`, `Card ${i}`));
  (api.rememberedCharacters as any).mockReturnValue(many);
  (api.listCharacters as any).mockResolvedValue(many);
  const { container } = renderGrid();
  const first = container.querySelectorAll(".char-card").length;
  expect(first).toBeGreaterThan(0);
  expect(first).toBeLessThan(40);
  expect(screen.getByText("Card 0")).toBeInTheDocument();
  await waitFor(() => expect(container.querySelectorAll(".char-card")).toHaveLength(40));
  // The counts that describe the roster never described the slice.
  expect(screen.getByText("Card 39")).toBeInTheDocument();
});

test("the rest of a long roster still arrives when it came from the read, not memory", async () => {
  const many = Array.from({ length: 40 }, (_, i) => row(`c${i}`, `Card ${i}`));
  (api.listCharacters as any).mockResolvedValue(many);
  renderGrid();
  await screen.findByText("Card 39");
});
