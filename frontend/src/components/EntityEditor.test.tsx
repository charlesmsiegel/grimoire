import type { ModuleDetail } from "../api/types";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { artImage, artRead } from "../testkit/recordArt";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, navigationProbe, editorWith, renderEditor, deferred, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

const entityFixture = (id: string, name: string) => ({ meta: { id, name }, body: "x", rev: "r1" });

test("structured frontmatter keeps its content when loaded into a text field", async () => {
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "saltmarch", name: "Saltmarch", keys: "", item_type: { category: "relic" } },
    body: "", rev: "r1",
  });
  renderEditor({ kind: "items", selected: "saltmarch", isNew: false });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Type")).toHaveValue('{"category":"relic"}');
});

test("a bookmarked address that fails to read reports the failure instead of leaving a blank form", async () => {
  vi.mocked(api.readEntity).mockRejectedValueOnce(new Error("not found"));
  render(<Wrap wid="w" kind="lore" selected="ghost" />);
  await screen.findByText("Error: not found");
});

test("lists entities and creates one with keys", async () => {
  render(<Wrap wid="w" kind="lore" />);
  await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Salt Pact" } });
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "binds" } });
  fireEvent.change(screen.getByLabelText("Keys"), { target: { value: "pact,salt" } });
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", {
      name: "Salt Pact", body: "binds", keys: "pact,salt", owners: "", secrecy: "public",
    }),
  );
});

test("clicking an entity shows a read-only view; Edit reveals the form", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({ meta: { id: "salt", name: "Salt", keys: "pact,brine" }, body: "Binds **all**" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(screen.getByText("all")).toBeInTheDocument();          // markdown rendered
  expect(container.querySelector("textarea")).toBeNull();        // read-only
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("pact")).toBeInTheDocument();    // keys in sidebar
  expect(within(side).getByText("brine")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();    // form revealed
});

test("detail sidebar shows the suggested image prompt when set", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "the-crypt", name: "The Crypt" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "the-crypt", name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt, torchlight" },
    body: "cold" }));
  const { container } = render(<Wrap wid="w" kind="locations" selected="the-crypt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Image prompt")).toBeInTheDocument();
  expect(within(side).getByText("a dark crypt, torchlight")).toBeInTheDocument();
});

test("detail sidebar omits the image prompt section when unset", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  // default readEntity mock (from beforeEach) has no sd_prompt
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).queryByText("Image prompt")).toBeNull();
});

test("editing an entity saves with updated keys", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Keys"), { target: { value: "pact,brine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      expect.objectContaining({ keys: "pact,brine" })),
  );
});

test("creates a lore entry with a selected owner", async () => {
  render(<Wrap isNew wid="w" kind="lore" />);
  await screen.findByLabelText("Name");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exile" } });
  fireEvent.click(await screen.findByLabelText("Tanaka")); // owner checkbox
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "Exile", owners: "characters:tanaka" })),
  );
});

test("owners picker offers items, groups and creatures", async () => {
  // An object owner is real now: an item is present when its holder is, a
  // group with its leader or at its headquarters, a creature in its habitat
  // (spec §7.1), and public lore it owns reaches everyone there (§8.2).
  const byKind: Record<string, { id: string; name: string }[]> = {
    locations: [{ id: "quay", name: "Quay" }],
    items: [{ id: "lantern", name: "Lantern" }],
    groups: [{ id: "dock-union", name: "Dock Union" }],
    creatures: [{ id: "gull", name: "Gull" }],
  };
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(byKind[kind] ?? []));
  render(<Wrap isNew wid="w" kind="lore" />);
  await screen.findByLabelText("Name");
  for (const label of ["Tanaka", "Quay", "Lantern", "Dock Union", "Gull"]) {
    expect(await screen.findByLabelText(label)).toBeInTheDocument();
  }
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Oil" } });
  fireEvent.click(screen.getByLabelText("Lantern"));
  fireEvent.click(screen.getByLabelText("Gull"));
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "Oil", owners: "items:lantern, creatures:gull" })),
  );
});

test("groups the rail by owner with an Unowned group", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "a", name: "Owned A", owners: "characters:tanaka" },
      { id: "b", name: "World B" },
    ]));
  const { container } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  expect(await screen.findByText("Unowned (world)")).toBeInTheDocument();
  const rail = container.querySelector(".entity-grid") as HTMLElement;
  // "Owned A" sits under the Tanaka group; "World B" under Unowned
  const tanakaGroup = within(rail).getByText("Tanaka").closest(".rail-group") as HTMLElement;
  expect(within(tanakaGroup).getByText("Owned A")).toBeInTheDocument();
  expect(within(tanakaGroup).queryByText("World B")).toBeNull();
  const unownedGroup = within(rail).getByText("Unowned (world)").closest(".rail-group") as HTMLElement;
  expect(within(unownedGroup).getByText("World B")).toBeInTheDocument();
});

test("?owner= pre-checks the owner for a new entry", async () => {
  render(<Wrap wid="w" kind="lore" selected={null} newOwner="characters:tanaka" />);
  const tanaka = await screen.findByLabelText("Tanaka");
  expect((tanaka as HTMLInputElement).checked).toBe(true);
});

test("the route's record opens that entry in the read-only view", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "a", name: "Owned A", owners: "characters:tanaka" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({ meta: { id: "a", name: "Owned A", owners: "characters:tanaka" }, body: "hi" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="a" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "a"));
  expect(await screen.findByText("hi")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull(); // read-only view, not the form
});

test("a route change away from a pre-owned form clears the owner", async () => {
  // guards the loreNav-never-cleared regression: starting a world-level entry must be unowned
  const { rerender } = render(<Wrap wid="w" kind="lore" selected={null} newOwner="characters:tanaka" />);
  expect(await screen.findByLabelText("Tanaka")).toBeChecked();
  rerender(<Wrap wid="w" kind="lore" selected={null} />);
  expect(screen.getByLabelText("Tanaka")).not.toBeChecked();
});

test("owner chip in the read-only view calls onOpenOwner", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [] : [{ id: "a", name: "Owned A", owners: "characters:tanaka" }]));
  vi.mocked(api.readEntity).mockResolvedValue(fixture({ meta: { id: "a", name: "Owned A", owners: "characters:tanaka" }, body: "x" }));
  const onOpenOwner = vi.fn();
  render(<Wrap wid="w" kind="lore" selected="a" onOpenOwner={onOpenOwner} />);
  // the only button labelled exactly "Tanaka" is the owner chip in the sidebar
  fireEvent.click(await screen.findByRole("button", { name: "Tanaka" }));
  expect(onOpenOwner).toHaveBeenCalledWith("characters:tanaka");
});

test("location rail rows show the primary image when one exists", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "warehouse", name: "Warehouse Nine", has_image: true },
    { id: "reeds", name: "The Reeds", has_image: false },
  ]);
  const { container } = render(<Wrap isNew={false} wid="w" kind="locations" />);
  await screen.findByText("Warehouse Nine");
  expect(container.querySelectorAll(".loc-row-img")).toHaveLength(1);
});

test("the owned-lore panel addresses this location's lore, and pre-owns a new entry under it", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "lore"
      ? [{ id: "the-well", name: "The Well", owners: "locations:warehouse" }] : []));
  render(<Wrap wid="w" kind="locations" selected="warehouse" />);
  expect(await screen.findByRole("link", { name: "The Well" }))
    .toHaveAttribute("href", "/worlds/w/lore/the-well");
  expect(screen.getByRole("link", { name: /\+ new lore/i }))
    .toHaveAttribute("href", "/worlds/w/lore?owner=locations%3Awarehouse");
});

test("location Details keeps a compact preview and offers guarded promotion through Manage", async () => {
  vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch" }, body: "At Saltmarch.", rev: "r1" });
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  expect(await screen.findByRole("button", { name: "View Saltmarch art" })).toBeInTheDocument();
  expect(screen.getByText("At Saltmarch.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Art 2" }));
  expect(screen.queryByRole("button", { name: "Make gallery_1 primary" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Make gallery_1 primary" }));
  const target = { scope: { kind: "world" as const, id: "realm" }, kind: "locations" as const, record_id: "saltmarch" };
  await waitFor(() => expect(api.promoteRecordArt).toHaveBeenCalledWith(target, artImage(target, "gallery_1"), "avatar-1"));
  confirm.mockRestore();
});

test("entity descriptions retain reviewed state and save captured revisions through Manage", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [
    artImage(target), artImage(target, "gallery_1", { description: "", described: false })]));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  expect(screen.getByText("Not described")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of gallery_1" }), { target: { value: "Saltmarch." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const target = { scope: { kind: "world" as const, id: "realm" }, kind: "locations" as const, record_id: "saltmarch" };
  await waitFor(() => expect(api.setRecordArtDescription).toHaveBeenCalledWith(target,
    artImage(target, "gallery_1", { description: "", described: false }), "Saltmarch.", "gallery_1-caption-1", false));
});

test("an empty location keeps Details clean and makes Add images available from Art", async () => {
  renderEditor({ kind: "locations", selected: "saltmarch" });
  await screen.findByRole("tab", { name: "Art 0" });
  expect(screen.queryByRole("button", { name: "Add images" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Art (0)" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Art 0" }));
  expect(screen.getByText("No images yet")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add images" })).toBeInTheDocument();
});

test("lore rows stack owner avatars; owners without avatars are omitted", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "smuggling", name: "Smuggling", owners: "characters:maren, characters:hedde" },
    ]));
  const { container } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findAllByText("Smuggling");
  await waitFor(() => expect(container.querySelectorAll(".owner-stack-img")).toHaveLength(2));
  expect(container.querySelector(".owner-stack-img")).toHaveAttribute("title", "Maren");
  // A 26px stack draws the smallest bucket.
  expect(container.querySelector(".owner-stack-img"))
    .toHaveAttribute("src", "/img/w/characters/maren/v1/avatar?w=128");
});

test("lore detail owner chips include an avatar or initials", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "maren", name: "Maren Voss", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "smuggling", name: "Smuggling", owners: "characters:maren" },
    ]));
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "smuggling", name: "Smuggling", owners: "characters:maren" }, body: "quiet boats" }));
  render(<Wrap wid="w" kind="lore" selected="smuggling" />);
  await screen.findByText("quiet boats");
  expect(await screen.findByText("MV")).toBeInTheDocument(); // initials inside the owner chip
});

test("deleting the open record returns to the grid rather than a blank form under its id", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  navigationProbe.path = "";
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(navigationProbe.path).toBe("/section"));
});

test("a failed listing on the browse grid says so instead of reading as an empty library", async () => {
  vi.mocked(api.listEntities).mockRejectedValue(new Error("network down"));
  renderEditor({ kind: "items", isNew: false });
  expect(await screen.findByText(/network down/)).toBeInTheDocument();
  expect(screen.queryByText("No item yet.")).not.toBeInTheDocument();
});

test("deletes after confirm", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt"));
});

test("entity thumbnails retain byte versions and the viewer opens the original", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target,
    [artImage(target, "gallery_1", { url: "/api/entity/gallery_1?v=bbb2", thumb: "/api/entity/gallery_1?v=bbb2&w=160" })]));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
  const tile = screen.getByRole("button", { name: "View Gallery image 1" });
  expect(within(tile).getByRole("img")).toHaveAttribute("src", "/api/entity/gallery_1?v=bbb2&w=160");
  expect(within(tile).getByRole("img")).toHaveAttribute("loading", "lazy");
  fireEvent.click(tile);
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", "/api/entity/gallery_1?v=bbb2");
});

test("new kinds render the list/detail pattern with their own label", async () => {
  // Kind-aware: groups now has a `headquarters` ref field, so the editor also
  // lists locations for its picker — a blanket mock would put a second "Salt
  // Circle" on the page.
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "groups" ? [{ id: "salt-circle", name: "Salt Circle" }] : []));
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "salt-circle", name: "Salt Circle" }, body: "A quiet **cabal**" }));
  const { container, rerender } = render(<Wrap isNew={false} wid="w" kind="groups" />);
  expect(await screen.findByRole("link", { name: /\+ new group/i })).toBeInTheDocument();
  rerender(<Wrap wid="w" kind="groups" selected="salt-circle" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups", "salt-circle"));
  expect(screen.getByText("cabal")).toBeInTheDocument();       // markdown rendered, read-only
  expect(container.querySelector("textarea")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("an item uses the common Art shelf while retaining its rail preview", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch", has_image: true }]);
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target)]));
  renderEditor({ kind: "items", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
  expect(screen.getByRole("button", { name: "View Primary image" })).toBeInTheDocument();
  expect(api.listEntityImages).not.toHaveBeenCalled();
});

test("a wizard left open after its module goes away shows the grid, not a blank form", async () => {
  const module = fixture<ModuleDetail>({
    id: "m", source: "builtin", manifest: { id: "m", name: "M" },
    sheets: { groups: {}, sheet_types: { w: { kind: "items" } } }, checks: {}, rules: [],
    content: [], errors: [],
  });
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "bare", name: "Bare" }]);
  const { rerender } = renderEditor({ kind: "items", isNew: false, module });
  fireEvent.click(await screen.findByRole("button", { name: /with sheet/ }));
  rerender(editorWith({ kind: "items", isNew: false, module: null }));
  expect(await screen.findByRole("link", { name: /Bare/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Create item/ })).not.toBeInTheDocument();
});

test("a browse card is a card whether or not its record has an image", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "bare", name: "Bare" }]);
  const { container } = renderEditor({ kind: "lore", isNew: false });
  await screen.findByRole("link", { name: /Bare/ });
  expect(container.querySelector(".entity-grid .row.loc-row")).not.toBeNull();
});

test("a browse card carries its record's image", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch", has_image: true }]);
  const { container } = renderEditor({ kind: "items", isNew: false });
  await screen.findByRole("link", { name: /Saltmarch/ });
  expect(container.querySelector(".entity-grid .loc-row-img")).not.toBeNull();
});

test("a long roster can be filtered, and says how much of it is showing", async () => {
  // A world can hold hundreds of records and the rail is a fixed 220px. Without
  // a filter the only way to reach one is to scroll for it — which is the
  // problem the redesign's full-width browse grid was answering.
  const many = Array.from({ length: 12 }, (_, i) => ({
    id: `e${i}`, name: `Record ${i}`, keys: i === 3 ? "harbour" : "",
    secrecy: "public", tokens: 10,
  }));
  vi.mocked(api.listEntities).mockResolvedValue(many);
  render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findByText("Record 0");
  expect(screen.getByText(/12 lores?/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText(/search lore/i), { target: { value: "harbour" } });
  // Matched on keys as well as name: keys are what a reader knows a lore record
  // by, and the id is a slug they never typed.
  expect(screen.getByText("1 of 12")).toBeInTheDocument();
  // Scoped to the rail: the filter narrows what you can NAVIGATE to, and must
  // not close whatever record is already open in the body beside it.
  const rail = document.querySelector(".entity-browse")!;
  expect(within(rail as HTMLElement).queryByText("Record 0")).not.toBeInTheDocument();
  expect(within(rail as HTMLElement).getByText("Record 3")).toBeInTheDocument();
});

test("a filter matching nothing is not an empty world", async () => {
  const many = Array.from({ length: 12 }, (_, i) => ({
    id: `e${i}`, name: `Record ${i}`, secrecy: "public", tokens: 10,
  }));
  vi.mocked(api.listEntities).mockResolvedValue(many);
  render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findByText("Record 0");
  fireEvent.change(screen.getByLabelText(/search lore/i), { target: { value: "zzz" } });
  expect(screen.getByText(/nothing matches/i)).toBeInTheDocument();
  expect(screen.queryByText(/no lore yet/i)).not.toBeInTheDocument();
});

// ---- ref-valued fields (#222) ----------------------------------------------

describe("selecting from the route", () => {
  // A fixture shared by most of these: an "items" listing that resolves the
  // same record the read-only-view and row-link tests both need, so each test
  // states only what it is actually about. `entityFixture` mirrors this shape.
  beforeEach(() => {
    vi.mocked(api.listEntities).mockResolvedValue([{ id: "the-salt-pact", name: "Salt Pact" }]);
    vi.mocked(api.readEntity).mockResolvedValue(entityFixture("the-salt-pact", "The Salt Pact"));
  });

test("a record row is a link to that record's address", async () => {
  renderEditor({ kind: "items", selected: null, isNew: false });
  const row = await screen.findByRole("link", { name: /Salt Pact/ });
  expect(row).toHaveAttribute("href", "/worlds/realm/items/the-salt-pact");
});

test("an unowned blank form can be pre-owned by the chip that opened it", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "seraphine", name: "Seraphine" }]));
  renderEditor({ kind: "lore", selected: null, newOwner: "characters:seraphine" });
  const seraphine = await screen.findByLabelText(/Seraphine/);
  expect((seraphine as HTMLInputElement).checked).toBe(true);
});

test("leaving a record for the section root does not get reopened by its own late read", async () => {
  const slow = deferred();
  vi.mocked(api.readEntity).mockImplementationOnce(() => slow.promise);
  const { rerender } = renderEditor({ kind: "items", selected: "the-salt-pact" });
  rerender(editorWith({ kind: "items", selected: null, isNew: false }));
  slow.resolve(entityFixture("the-salt-pact", "The Salt Pact"));
  await screen.findByRole("link", { name: /New item/ });
  expect(screen.queryByRole("heading", { name: "The Salt Pact" })).toBeNull();
});

test("a record's Back link returns to the section root, the browse grid", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  expect(await screen.findByRole("link", { name: /Back to the list/ }))
    .toHaveAttribute("href", "/worlds/realm/items");
});

test("+ New on the grid links to the blank form, not the root", async () => {
  renderEditor({ kind: "items", isNew: false });
  expect(await screen.findByRole("link", { name: /New item/ }))
    .toHaveAttribute("href", "/worlds/realm/items?new=1");
});

test("the selected record opens read-only", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("a later selection wins even when the earlier read lands last", async () => {
  const slow = deferred(); const fast = deferred();
  vi.mocked(api.readEntity)
    .mockImplementationOnce(() => slow.promise)
    .mockImplementationOnce(() => fast.promise);
  const { rerender } = renderEditor({ kind: "items", selected: "a" });
  rerender(editorWith({ kind: "items", selected: "b" }));
  fast.resolve(entityFixture("b", "Bee"));
  slow.resolve(entityFixture("a", "Ay"));
  await screen.findByRole("heading", { name: "Bee" });
  expect(screen.queryByRole("heading", { name: "Ay" })).toBeNull();
});

test("the same id under a changed scope reads the new scope's record", async () => {
  const { rerender } = renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  rerender(editorWith({ kind: "items", selected: "the-salt-pact",
                        scope: { kind: "campaign", id: "run" } }));
  await waitFor(() => expect(vi.mocked(api.readEntity).mock.calls.slice(-1)[0][0])
    .toEqual({ kind: "campaign", id: "run" }));
});

test("re-selecting the record already open leaves an unsaved draft alone", async () => {
  const props = { kind: "items" as const, selected: "the-salt-pact" };
  const { rerender } = renderEditor(props);
  await screen.findByRole("heading", { name: "The Salt Pact" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  const reads = vi.mocked(api.readEntity).mock.calls.length;
  rerender(editorWith(props));              // the same address, asked for again
  expect(vi.mocked(api.readEntity).mock.calls.length).toBe(reads);
  expect(screen.getByLabelText(/Name/)).toHaveValue("Half written");
});

test("...and Cancel is still the control that discards it", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByRole("heading", { name: "The Salt Pact" });
  expect(screen.queryByDisplayValue("Half written")).toBeNull();
});

}); // describe("selecting from the route")

// ---- activation controls (lore activation spec 3, 4.2) --------------------

// `wid` stays the parent world while the editor moves into a campaign.
test("lore owner options follow scope kind with the same wid and discard late results", async () => {
  const old = deferred<Awaited<ReturnType<typeof api.listCharacters>>>();
  vi.mocked(api.listCharacters).mockImplementation((scope) => scope.kind === "world"
    ? old.promise : Promise.resolve(fixture([{ id: "mara", name: "Mara" }])));
  const { rerender } = render(<Wrap wid="w" kind="lore" scope={{ kind: "world", id: "w" }} />);
  rerender(<Wrap wid="w" kind="lore" scope={{ kind: "campaign", id: "w" }} />);
  expect(await screen.findByRole("checkbox", { name: "Mara" })).toBeInTheDocument();
  await act(async () => { old.resolve(fixture([{ id: "tanaka", name: "Tanaka" }])); });
  expect(screen.queryByRole("checkbox", { name: "Tanaka" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Mara" })).toBeInTheDocument();
});

test("lore owners clear on scope change while the next listing fails partially", async () => {
  const next = deferred<Awaited<ReturnType<typeof api.listCharacters>>>();
  const { rerender } = render(<Wrap wid="w" kind="lore" scope={{ kind: "world", id: "a" }} />);
  await screen.findByRole("checkbox", { name: "Tanaka" });
  vi.mocked(api.listCharacters).mockReturnValueOnce(next.promise);
  vi.mocked(api.listPCs).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  rerender(<Wrap wid="w" kind="lore" scope={{ kind: "world", id: "b" }} />);
  expect(screen.queryByRole("checkbox", { name: "Tanaka" })).not.toBeInTheDocument();
  await act(async () => { next.reject(new Error("owners unavailable")); });
  expect(await screen.findByRole("checkbox", { name: "Mara" })).toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: "Tanaka" })).not.toBeInTheDocument();
});
