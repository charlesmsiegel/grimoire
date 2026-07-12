import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { EntityEditor } from "./EntityEditor";

vi.mock("../api/client", () => ({
  api: {
    listEntities: vi.fn(),
    createEntity: vi.fn(),
    readEntity: vi.fn(),
    updateEntity: vi.fn(),
    deleteEntity: vi.fn(),
    listCharacters: vi.fn(),
    listPCs: vi.fn(),
    listEntityImages: vi.fn(),
    putEntityImage: vi.fn(),
    promoteEntityImage: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
    actorImageUrl: (sc: { kind: string; id: string }, c: string, v: string, n: string) => `/img/${sc.id}/${c}/${v}/${n}`,
    entityImageUrl: (_s: any, k: string, e: string, n: string) => `/img/${k}/${e}/${n}`,
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listEntities as any).mockResolvedValue([]);
  (api.createEntity as any).mockResolvedValue({ id: "e1" });
  (api.updateEntity as any).mockResolvedValue({ ok: true });
  (api.deleteEntity as any).mockResolvedValue({ ok: true });
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt", name: "Salt", keys: "pact" }, body: "x" });
  (api.listCharacters as any).mockResolvedValue([{ id: "tanaka", name: "Tanaka" }]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.putEntityImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.promoteEntityImage as any).mockResolvedValue({ ok: true });
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
      name: "Salt Pact", body: "binds", keys: "pact,salt", owners: "",
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
