import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { EntityEditor } from "./EntityEditor";

vi.mock("../api/client", () => ({
  // The editor branches on `instanceof ApiError` to tell a stale-record 409
  // from any other failure, so the mock has to hand back a real class. Defined
  // inside the factory: `vi.mock` is hoisted above every top-level statement,
  // so a class declared out here would not exist yet when it runs.
  ApiError: class extends Error {
    constructor(public status: number, public detail: string, public kind?: string,
                public body?: Record<string, unknown>) {
      super(detail);
    }
  },
  SECRECY_LEVELS: ["public", "secret", "gm-only"],
  ENTITY_KINDS: ["locations", "lore", "items", "groups", "creatures"],
  SECRECY_LABELS: { public: "Public", secret: "Secret", "gm-only": "GM-only" },
  ENTITY_FIELDS: {
    locations: [], lore: [],
    items: [{ key: "item_type", label: "Type" }, { key: "rarity", label: "Rarity" }],
    groups: [{ key: "group_type", label: "Type" }],
    creatures: [{ key: "creature_type", label: "Type" }, { key: "threat", label: "Threat" }],
  },
  api: {
    listEntities: vi.fn(),
    createEntity: vi.fn(),
    readEntity: vi.fn(),
    updateEntity: vi.fn(),
    deleteEntity: vi.fn(),
    reclassifyEntity: vi.fn(),
    listCharacters: vi.fn(),
    listPCs: vi.fn(),
    listEntityImages: vi.fn(),
    setEntityImageDescription: vi.fn(),
    draftEntityImageDescription: vi.fn(),
    putEntityImage: vi.fn(),
    promoteEntityImage: vi.fn(),
    // The campaign-scope sidebar's LibraryPanel (#52, #53). "already library
    // content, unedited" is the state that renders no button, so every test
    // here sees the sidebar it always did; LibraryPanel.test.tsx owns the rest.
    libraryStatus: vi.fn().mockResolvedValue(
      { in_library: true, diverged: false, can_promote: false, can_push: false }),
    promoteToLibrary: vi.fn(),
    // World scope reaches DemotePanel; campaign scope reaches LibraryPanel.
    // Both resolve to "nothing to do", so these views render as they always did.
    libraryDependents: vi.fn().mockResolvedValue([]),
    demoteFromLibrary: vi.fn(),
    pushToLibrary: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
    actorImageUrl: (sc: { kind: string; id: string }, k: string, a: string, v: string, n: string) =>
      `/img/${sc.id}/${k}/${a}/${v}/${n}`,
    entityImageUrl: (_s: any, k: string, e: string, n: string) => `/img/${k}/${e}/${n}`,
    getSheet: vi.fn(),
    putSheet: vi.fn(),
    putSheetCreation: vi.fn(),
    readModuleContent: vi.fn(),
    instantiateContent: vi.fn(),
  },
}));
import { ApiError, api } from "../api/client";

const fail = (status: number, detail: string, kind?: string,
              body?: Record<string, unknown>) =>
  new (ApiError as any)(status, detail, kind, body);

beforeEach(() => {
  vi.clearAllMocks();
  (api.listEntities as any).mockResolvedValue([]);
  (api.createEntity as any).mockResolvedValue({ id: "e1" });
  (api.updateEntity as any).mockResolvedValue({ ok: true });
  (api.deleteEntity as any).mockResolvedValue({ ok: true });
  (api.reclassifyEntity as any).mockResolvedValue({ id: "salt", campaigns: [] });
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt", name: "Salt", keys: "pact" }, body: "x", rev: "r1" });
  (api.listCharacters as any).mockResolvedValue([{ id: "tanaka", name: "Tanaka" }]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.setEntityImageDescription as any).mockResolvedValue({ ok: true });
  (api.putEntityImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.promoteEntityImage as any).mockResolvedValue({ ok: true });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.readModuleContent as any).mockResolvedValue(null);
  (api.instantiateContent as any).mockResolvedValue({ id: "e1" });
});

test("lists entities and creates one with keys", async () => {
  render(<EntityEditor wid="w" kind="lore" />);
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
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt", name: "Salt", keys: "pact,brine" }, body: "Binds **all**" });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
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
  (api.listEntities as any).mockResolvedValue([{ id: "the-crypt", name: "The Crypt" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "the-crypt", name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt, torchlight" },
    body: "cold" });
  const { container } = render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("The Crypt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Image prompt")).toBeInTheDocument();
  expect(within(side).getByText("a dark crypt, torchlight")).toBeInTheDocument();
});

test("detail sidebar omits the image prompt section when unset", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  // default readEntity mock (from beforeEach) has no sd_prompt
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).queryByText("Image prompt")).toBeNull();
});

test("editing an entity saves with updated keys", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
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
  render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByRole("button", { name: /\+ new lore entry/i });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exile" } });
  fireEvent.click(await screen.findByLabelText("Tanaka")); // owner checkbox
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "Exile", owners: "characters:tanaka" })),
  );
});

test("groups the rail by owner with an Unowned group", async () => {
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "a", name: "Owned A", owners: "characters:tanaka" },
      { id: "b", name: "World B" },
    ]));
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  expect(await screen.findByText("Unowned (world)")).toBeInTheDocument();
  const rail = container.querySelector(".editor-list") as HTMLElement;
  // "Owned A" sits under the Tanaka group; "World B" under Unowned
  const tanakaGroup = within(rail).getByText("Tanaka").closest(".rail-group") as HTMLElement;
  expect(within(tanakaGroup).getByText("Owned A")).toBeInTheDocument();
  expect(within(tanakaGroup).queryByText("World B")).toBeNull();
  const unownedGroup = within(rail).getByText("Unowned (world)").closest(".rail-group") as HTMLElement;
  expect(within(unownedGroup).getByText("World B")).toBeInTheDocument();
});

test("nav.newOwner pre-checks the owner for a new entry", async () => {
  render(<EntityEditor wid="w" kind="lore" nav={{ newOwner: "characters:tanaka" }} onNavConsumed={vi.fn()} />);
  const tanaka = await screen.findByLabelText("Tanaka");
  expect((tanaka as HTMLInputElement).checked).toBe(true);
});

test("nav.focusEntry opens that entry in the read-only view", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "a", name: "Owned A", owners: "characters:tanaka" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "a", name: "Owned A", owners: "characters:tanaka" }, body: "hi" });
  const { container } = render(<EntityEditor wid="w" kind="lore" nav={{ focusEntry: "a" }} onNavConsumed={vi.fn()} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "a"));
  expect(await screen.findByText("hi")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull(); // read-only view, not the form
});

test("manual '+ New' after a nav.newOwner does NOT inherit the stale owner", async () => {
  // guards the loreNav-never-cleared regression: starting a world-level entry must be unowned
  render(<EntityEditor wid="w" kind="lore" nav={{ newOwner: "characters:tanaka" }} onNavConsumed={vi.fn()} />);
  expect((await screen.findByLabelText("Tanaka") as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /\+ new lore entry/i }));
  expect((screen.getByLabelText("Tanaka") as HTMLInputElement).checked).toBe(false);
});

test("owner chip in the read-only view calls onOpenOwner", async () => {
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [{ id: "a", name: "Owned A", owners: "characters:tanaka" }]));
  (api.readEntity as any).mockResolvedValue({ meta: { id: "a", name: "Owned A", owners: "characters:tanaka" }, body: "x" });
  const onOpenOwner = vi.fn();
  render(<EntityEditor wid="w" kind="lore" onOpenOwner={onOpenOwner} />);
  fireEvent.click(await screen.findByText("Owned A"));
  // the only button labelled exactly "Tanaka" is the owner chip in the sidebar
  fireEvent.click(await screen.findByRole("button", { name: "Tanaka" }));
  expect(onOpenOwner).toHaveBeenCalledWith("characters:tanaka");
});

test("location rail rows show the primary image when one exists", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "warehouse", name: "Warehouse Nine", has_image: true },
    { id: "reeds", name: "The Reeds", has_image: false },
  ]);
  const { container } = render(<EntityEditor wid="w" kind="locations" />);
  await screen.findByText("Warehouse Nine");
  expect(container.querySelectorAll(".loc-row-img")).toHaveLength(1);
});

test("location detail shows the primary image header and Images shelf with promote", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "warehouse", name: "Warehouse Nine", has_image: true }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "warehouse", name: "Warehouse Nine" }, body: "docks" });
  (api.listEntityImages as any).mockResolvedValue([
    { name: "avatar", ext: "png" }, { name: "gallery_1", ext: "png" },
  ]);
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("Warehouse Nine"));
  await screen.findByText("Images");
  expect(await screen.findByText("primary")).toBeInTheDocument();           // shelf caption
  expect(screen.getByAltText("Warehouse Nine primary")).toBeInTheDocument(); // header image
  fireEvent.click(screen.getByRole("button", { name: /set as primary/i }));
  await waitFor(() => expect(api.promoteEntityImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "locations", "warehouse", "gallery_1"));
});

test("an entity image carries its description, and unreviewed art says so", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "warehouse", name: "Warehouse Nine", has_image: true }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "warehouse", name: "Warehouse Nine" }, body: "docks" });
  (api.listEntityImages as any).mockResolvedValue([
    // `described` is what separates "never reviewed" from "reviewed, nothing
    // to say" -- both arrive with an empty `description`.
    { name: "avatar", ext: "png", description: "A grey quay.", described: true },
    { name: "gallery_1", ext: "png", description: "", described: false },
  ]);
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("Warehouse Nine"));
  await screen.findByText("Images");

  expect(screen.getByRole("button", { name: /Description of avatar/ }))
    .toHaveTextContent("A grey quay.");
  expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
    .toHaveTextContent("Describe…");

  fireEvent.click(screen.getByRole("button", { name: /Description of gallery_1/ }));
  const box = await screen.findByRole("textbox", { name: /Description of gallery_1/ });
  fireEvent.change(box, { target: { value: "Crates on the quay." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setEntityImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "locations", "warehouse", "gallery_1", "Crates on the quay."));
});

test("location detail without images shows the add tile only", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "reeds", name: "The Reeds", has_image: false }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "reeds", name: "The Reeds" }, body: "marsh" });
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("The Reeds"));
  await screen.findByText("no image");
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("lore rows stack owner avatars; owners without avatars are omitted", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "smuggling", name: "Smuggling", owners: "characters:maren, characters:hedde" },
    ]));
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  await screen.findAllByText("Smuggling");
  await waitFor(() => expect(container.querySelectorAll(".owner-stack-img")).toHaveLength(2));
  expect(container.querySelector(".owner-stack-img")).toHaveAttribute("title", "Maren");
});

test("lore detail owner chips include an avatar or initials", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren Voss", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "smuggling", name: "Smuggling", owners: "characters:maren" },
    ]));
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "smuggling", name: "Smuggling", owners: "characters:maren" }, body: "quiet boats" });
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click((await screen.findAllByText("Smuggling"))[0]);
  await screen.findByText("quiet boats");
  expect(await screen.findByText("MV")).toBeInTheDocument(); // initials inside the owner chip
});

test("deletes after confirm", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt"));
});

test("image urls carry per-record version tokens for immutable caching", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "warehouse", name: "Warehouse Nine", has_image: true, image_v: "aaa1" },
  ]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "warehouse", name: "Warehouse Nine" }, body: "docks" });
  (api.listEntityImages as any).mockResolvedValue([
    { name: "avatar", ext: "png", v: "aaa1" }, { name: "gallery_1", ext: "png", v: "bbb2" },
  ]);
  const { container } = render(<EntityEditor wid="w" kind="locations" />);
  await screen.findByText("Warehouse Nine");
  expect(container.querySelector(".loc-row-img")!.getAttribute("src"))
    .toBe("/img/locations/warehouse/avatar?v=aaa1");
  fireEvent.click(screen.getByText("Warehouse Nine"));
  await screen.findByText("Images");
  expect(screen.getByAltText("Warehouse Nine primary").getAttribute("src"))
    .toBe("/img/locations/warehouse/avatar?v=aaa1");
  expect(screen.getByAltText("gallery_1").getAttribute("src"))
    .toBe("/img/locations/warehouse/gallery_1?v=bbb2");
});

test("new kinds render the list/detail pattern with their own label", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt-circle", name: "Salt Circle" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt-circle", name: "Salt Circle" }, body: "A quiet **cabal**" });
  const { container } = render(<EntityEditor wid="w" kind="groups" />);
  expect(await screen.findByRole("button", { name: /\+ new group/i })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Salt Circle"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups", "salt-circle"));
  expect(screen.getByText("cabal")).toBeInTheDocument();       // markdown rendered, read-only
  expect(container.querySelector("textarea")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("image shelf renders for non-location kinds", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt-knife", name: "Salt Knife", has_image: true }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt-knife", name: "Salt Knife" }, body: "sharp" });
  (api.listEntityImages as any).mockResolvedValue([{ name: "avatar", v: "1" }]);
  const { container } = render(<EntityEditor wid="w" kind="items" />);
  fireEvent.click(await screen.findByText("Salt Knife"));
  await waitFor(() => expect(api.listEntityImages).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", "salt-knife"));
  expect(screen.getByText("Images")).toBeInTheDocument();            // shelf present
  expect(container.querySelector(".loc-row-img")).not.toBeNull();    // rail thumbnail
});

test("typed fields render in the form and are sent on create", async () => {
  render(<EntityEditor wid="w" kind="items" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Salt Knife" } });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "weapon" } });
  fireEvent.change(screen.getByLabelText("Rarity"), { target: { value: "rare" } });
  fireEvent.click(screen.getByRole("button", { name: /create item/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", {
      name: "Salt Knife", body: "", keys: "", owners: "", secrecy: "public",
      fields: { item_type: "weapon", rarity: "rare" },
    }),
  );
});

test("typed field values show as chips in the detail sidebar", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "marsh-wyrm", name: "Marsh Wyrm" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "marsh-wyrm", name: "Marsh Wyrm", creature_type: "wyrm", threat: "apex" }, body: "old" });
  const { container } = render(<EntityEditor wid="w" kind="creatures" />);
  fireEvent.click(await screen.findByText("Marsh Wyrm"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/Type: wyrm/)).toBeInTheDocument();
  expect(within(side).getByText(/Threat: apex/)).toBeInTheDocument();
});

test("campaign scope with a module mounts SheetPanel with a Sheet side-section", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt-knife", name: "Salt Knife" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt-knife", name: "Salt Knife" }, body: "sharp" });
  const module = {
    id: "mod1", source: "user",
    manifest: { id: "mod1", name: "Mod One" },
    sheets: { groups: {}, sheet_types: { itemSheet: { label: "Item Sheet", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  const { container } = render(
    <EntityEditor wid="w" kind="items" scope={{ kind: "campaign", id: "run" }} module={module} />,
  );
  fireEvent.click(await screen.findByText("Salt Knife"));
  await waitFor(() => expect(api.getSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mod1", "items", "salt-knife"));
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Sheet")).toBeInTheDocument();
});

test("merges module content into the rail as templates and previews on click", async () => {
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  } as any;
  (api.listEntities as any).mockResolvedValue([{ id: "sword", name: "Sword" }]);
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  await screen.findByText("Sword");
  const templateRow = await screen.findByText("Lantern of Winnowing");
  fireEvent.click(templateRow);
  await screen.findByText("A soft lantern.");
  expect(screen.getByText("Instantiate")).toBeInTheDocument();
  expect(screen.queryByText("Edit")).not.toBeInTheDocument();
});

test("instantiate creates a real record and selects it", async () => {
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  } as any;
  (api.listEntities as any).mockResolvedValue([]);
  (api.instantiateContent as any).mockResolvedValue({ id: "lantern" });
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "lantern", name: "Lantern of Winnowing" }, body: "A soft lantern.",
  });
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("Lantern of Winnowing"));
  fireEvent.click(await screen.findByText("Instantiate"));
  await waitFor(() => expect(api.instantiateContent).toHaveBeenCalledWith(
    { kind: "world", id: "w1" }, "items", "testmod", "lantern"));
  await screen.findByText("Edit"); // back to a normal read-only view of the new record
});

test("nav.newOwner clears stale contentPreview to show the new-entry form", async () => {
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "lore", id: "pact", name: "Salt Pact", sheet_type: null }],
    errors: [],
  } as any;
  (api.listEntities as any).mockResolvedValue([]);
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "pact", name: "Salt Pact", body: "Binds all salt-related magic.",
    keys: "", sheet_type: null, fields: {},
  });
  const onNavConsumed = vi.fn();
  const { rerender } = render(
    <EntityEditor wid="w" kind="lore" module={module} onNavConsumed={onNavConsumed} />
  );
  // First, click the template to populate contentPreview
  fireEvent.click(await screen.findByText("Salt Pact"));
  await screen.findByText("Binds all salt-related magic.");
  expect(screen.getByText("Instantiate")).toBeInTheDocument();

  // Now rerender with nav.newOwner, simulating navigation from OwnedLorePanel
  rerender(
    <EntityEditor wid="w" kind="lore" module={module}
      nav={{ newOwner: "locations:some-location" }} onNavConsumed={onNavConsumed} />
  );

  // The form should now show (new entry, not template preview)
  // Assert that the new-entry form title shows
  await waitFor(() => expect(screen.getByText("New lore entry")).toBeInTheDocument());
  // Assert that the stale template body is NOT visible
  expect(screen.queryByText("Binds all salt-related magic.")).not.toBeInTheDocument();
  // Assert that Instantiate button is NOT visible
  expect(screen.queryByText("Instantiate")).not.toBeInTheDocument();
});

it("shows a wizard trigger only when the module has a sheet type for this kind, and opens the wizard", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  const trigger = await screen.findByText("+ New item with sheet…");
  fireEvent.click(trigger);
  await screen.findByText("New item (with sheet)");
});

it("wires the wizard's deleteRecord to api.deleteEntity so a failed sheet write rolls back the entity", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  (api.createEntity as any).mockResolvedValue({ id: "e1" });
  (api.putSheetCreation as any).mockRejectedValue({ detail: "nope" });
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("+ New item with sheet…"));
  await screen.findByText("New item (with sheet)");

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));

  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w1" }, "items", "e1"));
});

it("hides the wizard trigger when the module has no sheet type for this kind", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = {
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  } as any;
  render(<EntityEditor wid="w1" kind="items" module={module} />);
  await screen.findByText("+ New item");
  expect(screen.queryByText("+ New item with sheet…")).not.toBeInTheDocument();
});

// ---- secrecy (#49) ---------------------------------------------------------

test("creates an entry with a chosen secrecy level", async () => {
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "The Twist" } });
  fireEvent.click(screen.getByRole("radio", { name: "Secret" }));
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "The Twist", secrecy: "secret" })),
  );
});

test("the secrecy picker offers all three levels and starts on public", async () => {
  render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByLabelText("Name");
  const group = screen.getByRole("radiogroup", { name: "Secrecy" });
  expect(within(group).getAllByRole("radio").map((r) => r.getAttribute("value")))
    .toEqual(["public", "secret", "gm-only"]);
  expect(within(group).getByRole("radio", { name: "Public" })).toBeChecked();
});

test("the detail sidebar badges a secret entry and the form opens on its level", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "secret" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "twist", name: "Twist", secrecy: "secret" }, body: "the harbourmaster did it" });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Twist"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Secrecy")).toBeInTheDocument();
  expect(within(side).getByText("Secret")).toBeInTheDocument();
  // Edit opens the form already on the stored level, so a save can't downgrade it
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByRole("radio", { name: "Secret" })).toBeChecked();
});

test("an unmarked entry reads as public in the sidebar", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt", name: "Salt" }, body: "x" });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Public")).toBeInTheDocument();
});

test("the rail badges non-public rows only", async () => {
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "a", name: "Open" },
      { id: "b", name: "Twist", secrecy: "secret" },
      { id: "c", name: "Note", secrecy: "gm-only" },
    ]));
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByText("Twist");
  const rail = container.querySelector(".editor-list") as HTMLElement;
  expect(within(rail).getByText("Secret")).toBeInTheDocument();
  expect(within(rail).getByText("GM-only")).toBeInTheDocument();
  expect(within(rail).queryByText("Public")).toBeNull();
});

test("switching a viewed entry back to public sends 'public', clearing the level", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "gm-only" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "twist", name: "Twist", secrecy: "gm-only" }, body: "x" });
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Twist"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("radio", { name: "Public" }));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "twist",
      expect.objectContaining({ secrecy: "public" })),
  );
});

test("'+ New' after viewing a secret entry does not inherit its level", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "secret" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "twist", name: "Twist", secrecy: "secret" }, body: "x" });
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Twist"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /\+ new lore entry/i }));
  expect(screen.getByRole("radio", { name: "Public" })).toBeChecked();
});

test("a hand-edited secrecy value is read the way the backend reads it", async () => {
  // `store.entities.normalize_secrecy` trims and lowercases, because frontmatter
  // is hand-editable. Matching only the canonical spelling here badged the entry
  // Public and made the next save send "public" — a valid level, so the route
  // accepted it and the entry was silently published.
  (api.listEntities as any).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: " Secret " }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "twist", name: "Twist", secrecy: " Secret " }, body: "x" });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Twist"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Secret")).toBeInTheDocument();
  expect(within(side).queryByText("Public")).toBeNull();

  // and an unrelated edit must not downgrade it on the way back out
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByRole("radio", { name: "Secret" })).toBeChecked();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "twist",
      expect.objectContaining({ body: "edited", secrecy: "secret" })),
  );
});

test("an unrecognised secrecy value still reads as public", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "typo", name: "Typo", secrecy: "sercet" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "typo", name: "Typo", secrecy: "sercet" }, body: "x" });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Typo"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Public")).toBeInTheDocument();
});

// ---- per-record prompt cost (#51) ----

test("rail rows carry the record's token count", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "salt", name: "Salt", tokens: 1240 },
    { id: "brine", name: "Brine", tokens: 7 },
  ]);
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByText("Salt");
  const counts = Array.from(container.querySelectorAll(".row-tokens")).map((n) => n.textContent);
  expect(counts).toEqual(["1,240", "7"]);
  // the rail drops the unit for width, so the row's accessible name carries it
  expect(screen.getByRole("button", { name: /Salt 1,240 tokens/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Brine 7 tokens/ })).toBeInTheDocument();
});

test("a zero-token record still shows its count, in the rail and the detail", async () => {
  // 0 is falsy: a `{e.tokens && ...}` guard would silently blank an empty
  // record's badge, which reads identically to the field being absent.
  (api.listEntities as any).mockResolvedValue([{ id: "stub", name: "Stub", tokens: 0 }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "stub", name: "Stub" }, body: "", tokens: 0 });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Stub"));
  expect(container.querySelector(".row-tokens")!.textContent).toBe("0");
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".token-badge")!.textContent).toBe("0 tokens");
});

test("a row with no token count renders no badge", async () => {
  // a payload from before the field existed must not render "undefined" or 0
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByText("Salt");
  expect(container.querySelector(".row-tokens")).toBeNull();
});

test("the detail header and sidebar report the cost, keyed as per-activation", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 42 }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt", name: "Salt", keys: "pact" }, body: "Binds", tokens: 42 });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".detail-main h3 .token-badge")!.textContent).toBe("42 tokens");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Context cost")).toBeInTheDocument();
  expect(within(side).getByText("42 tokens")).toBeInTheDocument();
  expect(within(side).getByText(/these keys activate/i)).toBeInTheDocument();
});

test("a keyless lore entry is described as always-on, a keyless location as the setting", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 3 }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 3 });
  const lore = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const loreSide = lore.container.querySelector(".detail-sidebar") as HTMLElement;
  // "always-on" alone also matches the Keys hint above it, so match the sentence
  expect(within(loreSide).getByText(/charged on every turn/i)).toBeInTheDocument();
  expect(within(loreSide).getByText("3 tokens")).toBeInTheDocument();  // singular only at 1
  lore.unmount();

  // a keyless LOCATION never joins world info; it is charged as the setting
  const loc = render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const locSide = loc.container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(locSide).getByText(/current setting/i)).toBeInTheDocument();
  expect(within(locSide).queryByText(/charged on every turn/i)).toBeNull();
});

test("the header badge clears when the form is reset for a new record", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 42 }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 42 });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(container.querySelector(".token-badge")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: /\+ new lore entry/i }));
  expect(container.querySelector(".token-badge")).toBeNull();
});

test("a GM-only record's count is marked as never charged, not as a live cost", async () => {
  // #49 drops a gm-only body before every activation rule, so the number is
  // what it WOULD cost. Showing it plain would read as a standing charge.
  (api.listEntities as any).mockResolvedValue([{ id: "vault", name: "Vault", tokens: 88, secrecy: "gm-only" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "vault", name: "Vault", secrecy: "gm-only" }, body: "Behind the seawall.", tokens: 88 });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  // by role, not by text: the same mock feeds `loreOwnerOptions`, so "Vault"
  // is also an owner checkbox label further down the form
  const railRow = await screen.findByRole("button", { name: /Vault.*88 tokens/ });
  expect(container.querySelector(".row-tokens")!.className).toContain("never-charged");
  // Struck-through text does not announce as struck, so the name has to say it.
  // The row also carries #49's own GM-only tag, hence the gap in the middle --
  // asserted loosely so that tag's wording stays that feature's business.
  expect(screen.getByRole("button", { name: /Vault.*88 tokens, never charged/ })).toBeInTheDocument();

  fireEvent.click(railRow);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".token-badge")!.className).toContain("never-charged");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/never charged/i)).toBeInTheDocument();
});

test("a public record's count carries no never-charged marking", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 88 }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 88 });
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByRole("button", { name: /Salt.*88 tokens/ }));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".row-tokens")!.className).not.toContain("never-charged");
  expect(container.querySelector(".token-badge")!.className).not.toContain("never-charged");
});

// ---- external edits: the save precondition (#35) ----

async function openForEdit() {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
}

test("a save echoes back the rev the record was read at", async () => {
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      expect.objectContaining({ body: "mine", rev: "r1" })),
  );
});

test("a stale save is refused without discarding what was typed", async () => {
  (api.updateEntity as any).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/changed on disk while you had it open/i)).toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("mine"); // still the user's text
});

test("overwrite retries against the rev the refusal reported", async () => {
  (api.updateEntity as any).mockRejectedValueOnce(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /overwrite with mine/i }));

  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenLastCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      expect.objectContaining({ body: "mine", rev: "r2" })),
  );
});

test("discard-and-reload throws the edit away and re-reads the record", async () => {
  (api.updateEntity as any).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  (api.readEntity as any).mockResolvedValue(
    { meta: { id: "salt", name: "Salt", keys: "pact" }, body: "theirs", rev: "r2" });
  fireEvent.click(await screen.findByRole("button", { name: /discard mine and reload/i }));

  await waitFor(() => expect(screen.getByText("theirs")).toBeInTheDocument());
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a record deleted underneath offers no overwrite", async () => {
  (api.updateEntity as any).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: null }));
  await openForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/has been deleted/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /overwrite with mine/i })).toBeNull();
});

test("a failure that is not a conflict still reaches the error banner", async () => {
  (api.updateEntity as any).mockRejectedValue(fail(400, "bad fields", "fields"));
  await openForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  expect(await screen.findByText("bad fields")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

// ---- reclassify (#119) ----

test("the detail sidebar offers every kind but this one", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const picker = screen.getByLabelText<HTMLSelectElement>("Reclassify as");
  expect([...picker.options].map((o) => o.value))
    .toEqual(["", "locations", "items", "groups", "creatures"]);
});

test("reclassifying sends the record's rev and reports where it went", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<EntityEditor wid="w" kind="lore" onReclassified={onReclassified} />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() =>
    expect(api.reclassifyEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      "locations", "r1"));
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt"));
});

test("the id the server hands back is the one navigated to", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  (api.reclassifyEntity as any).mockResolvedValue({ id: "salt-2", campaigns: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<EntityEditor wid="w" kind="lore" onReclassified={onReclassified} />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt-2"));
});

test("declining the confirm reclassifies nothing", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() => expect(api.reclassifyEntity).not.toHaveBeenCalled());
});

test("a stale record refuses the move and offers to make it anyway", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  (api.reclassifyEntity as any).mockRejectedValueOnce(
    fail(409, "record changed", "stale_record", { rev: "r2" }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<EntityEditor wid="w" kind="lore" onReclassified={onReclassified} />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  const anyway = await screen.findByRole("button", { name: /reclassify anyway/i });
  expect(onReclassified).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /overwrite with mine/i })).toBeNull();
  confirm.mockClear();
  (api.reclassifyEntity as any).mockResolvedValue({ id: "salt", campaigns: [] });
  fireEvent.click(anyway);
  // the on-disk rev, not the one the editor loaded -- and no second confirm
  await waitFor(() =>
    expect(api.reclassifyEntity).toHaveBeenLastCalledWith({ kind: "world", id: "w" }, "lore",
      "salt", "locations", "r2"));
  expect(confirm).not.toHaveBeenCalled();
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt"));
});

test("a refused move reports the error and leaves the record where it is", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  (api.reclassifyEntity as any).mockRejectedValue(fail(400, "already a lore record"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<EntityEditor wid="w" kind="lore" onReclassified={onReclassified} />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  expect(await screen.findByText("already a lore record")).toBeInTheDocument();
  expect(onReclassified).not.toHaveBeenCalled();
});

test("the campaign hint says the world keeps its own copy", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const { container } = render(
    <EntityEditor wid="w" scope={{ kind: "campaign", id: "c1" }} kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/the world keeps its own/i)).toBeInTheDocument();
});

test("moving out of locations warns that scenes lose their setting", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "lore" } });
  expect(confirm.mock.calls[0][0]).toMatch(/no longer show a setting/);
});

test("moving into locations says nothing about settings", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  expect(confirm.mock.calls[0][0]).not.toMatch(/setting/);
});
