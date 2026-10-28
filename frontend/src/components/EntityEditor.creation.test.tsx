import { createMemoryRouter, RouterProvider, useParams, useSearchParams } from "react-router-dom";
import { EntityEditor } from "./EntityEditor";
import type { ModuleDetail } from "../api/types";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, navigationProbe, deferred, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

test("campaign scope with a module mounts SheetPanel with a Sheet side-section", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt-knife", name: "Salt Knife" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({ meta: { id: "salt-knife", name: "Salt Knife" }, body: "sharp" }));
  const module = fixture<ModuleDetail>({
    id: "mod1", source: "user",
    manifest: { id: "mod1", name: "Mod One" },
    sheets: { groups: {}, sheet_types: { itemSheet: { label: "Item Sheet", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  });
  const { container } = render(
    <Wrap wid="w" kind="items" scope={{ kind: "campaign", id: "run" }} module={module} selected="salt-knife" />,
  );
  await waitFor(() => expect(api.getSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mod1", "items", "salt-knife"));
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Sheet")).toBeInTheDocument();
});

test("merges module content into the rail as templates and previews on click", async () => {
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  });
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "sword", name: "Sword" }]);
  vi.mocked(api.readModuleContent).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  await screen.findByText("Sword");
  const templateRow = await screen.findByText("Lantern of Winnowing");
  fireEvent.click(templateRow);
  await screen.findByText("A soft lantern.");
  expect(screen.getByText("Instantiate")).toBeInTheDocument();
  expect(screen.queryByText("Edit")).not.toBeInTheDocument();
});

test("instantiate creates a real record and navigates to its own address", async () => {
  // Reproduces the reviewer's proof: mount, instantiate a template, and the
  // address bar has to move off the section root -- otherwise a subsequent
  // "+ New" (a `Link` to that same root) points at the screen already
  // showing and silently does nothing.
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "items", id: "lantern", name: "Lantern of Winnowing", sheet_type: null }],
    errors: [],
  });
  vi.mocked(api.listEntities).mockResolvedValue([]);
  vi.mocked(api.instantiateContent).mockResolvedValue({ id: "lantern" });
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "lantern", name: "Lantern of Winnowing" }, body: "A soft lantern.",
  }));
  vi.mocked(api.readModuleContent).mockResolvedValue({
    kind: "items", id: "lantern", name: "Lantern of Winnowing", body: "A soft lantern.",
    keys: "", sheet_type: null, fields: {},
  });
  navigationProbe.path = "";
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("Lantern of Winnowing"));
  fireEvent.click(await screen.findByText("Instantiate"));
  await waitFor(() => expect(api.instantiateContent).toHaveBeenCalledWith(
    { kind: "world", id: "w1" }, "items", "testmod", "lantern"));
  await waitFor(() => expect(navigationProbe.path).toBe("/section/lantern"));
});

test("a route change to a pre-owned form clears a stale content preview", async () => {
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: {} }, checks: {}, rules: [],
    content: [{ kind: "lore", id: "pact", name: "Salt Pact", sheet_type: null }],
    errors: [],
  });
  vi.mocked(api.listEntities).mockResolvedValue([]);
  vi.mocked(api.readModuleContent).mockResolvedValue({
    kind: "lore", id: "pact", name: "Salt Pact", body: "Binds all salt-related magic.",
    keys: "", sheet_type: null, fields: {},
  });
  const { rerender } = render(
    <Wrap wid="w" kind="lore" module={module} selected={null} isNew={false} />
  );
  // First, click the template to populate contentPreview
  fireEvent.click(await screen.findByText("Salt Pact"));
  await screen.findByText("Binds all salt-related magic.");
  expect(screen.getByText("Instantiate")).toBeInTheDocument();

  // Now rerender with a pre-owned route, simulating navigation from OwnedLorePanel
  rerender(
    <Wrap wid="w" kind="lore" module={module}
      selected={null} newOwner="locations:some-location" />
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
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  });
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  const trigger = await screen.findByText("+ New item with sheet…");
  fireEvent.click(trigger);
  await screen.findByText("New item (with sheet)");
});

it("wires the wizard's deleteRecord to api.deleteEntity so a failed sheet write rolls back the entity", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  });
  vi.mocked(api.createEntity).mockResolvedValue({ id: "e1" });
  vi.mocked(api.putSheetCreation).mockRejectedValue({ detail: "nope" });
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("+ New item with sheet…"));
  await screen.findByText("New item (with sheet)");

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));

  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w1" }, "items", "e1"));
});

test("the sheet wizard navigates to the new record's own address on Create", async () => {
  // Same defect, second call site: `CreationWizard`'s own `onDone` used to
  // finish with `select(id)` alone, leaving the address bar on the section
  // root once the wizard closed.
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  });
  vi.mocked(api.createEntity).mockResolvedValue({ id: "sword-id" });
  // A prior test in this file (deliberately) rejects this call; `beforeEach`
  // only clears call history, not a stubbed implementation, so this one has
  // to restate its own success rather than inherit that failure.
  vi.mocked(api.putSheetCreation).mockResolvedValue(fixture({ sheet: {} }));
  navigationProbe.path = "";
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  fireEvent.click(await screen.findByText("+ New item with sheet…"));
  await screen.findByText("New item (with sheet)");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalled());
  await waitFor(() => expect(navigationProbe.path).toBe("/section/sword-id"));
});

it("hides the wizard trigger when the module has no sheet type for this kind", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([]);
  const module = fixture<ModuleDetail>({
    id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
    sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } } },
    checks: {}, rules: [], content: [], errors: [],
  });
  render(<Wrap isNew={false} wid="w1" kind="items" module={module} />);
  await screen.findByText("+ New item");
  expect(screen.queryByText("+ New item with sheet…")).not.toBeInTheDocument();
});

// ---- secrecy (#49) ---------------------------------------------------------

const creationModule = () => fixture<ModuleDetail>({
  id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
  sheets: { groups: {}, sheet_types: { hero: { label: "Hero", kind: "items", groups: [], fields: [] } } },
  checks: {}, rules: [],
  content: [{ kind: "items", id: "lantern", name: "Lantern", sheet_type: null }], errors: [],
});
const template = { kind: "items", id: "lantern", name: "Lantern", body: "Template body", keys: "", sheet_type: null, fields: {} };
async function startWizard() {
  fireEvent.click(await screen.findByText("+ New item with sheet…"));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Sword" } });
  fireEvent.click(screen.getByText("Next"));
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
  fireEvent.click(screen.getByText("Create"));
}

test("committed wizard creation still navigates when its summary refresh fails", async () => {
  vi.mocked(api.putSheetCreation).mockResolvedValue(fixture({ sheet: {} }));
  render(<Wrap wid="w" kind="items" isNew={false} module={creationModule()} />);
  await screen.findByText("+ New item with sheet…");
  vi.mocked(api.listEntities).mockRejectedValueOnce(new Error("Created, but list unavailable"));
  await startWizard();
  expect(await screen.findByText(/Created, but list unavailable/)).toBeInTheDocument();
  await waitFor(() => expect(navigationProbe.path).toBe("/section/e1"));
  expect(api.deleteEntity).not.toHaveBeenCalled();
});

test.each(["create", "sheet", "refresh"] as const)("wizard completion after leaving during %s preserves the new draft", async (stage) => {
  const creating = deferred<{ id: string }>();
  const sheet = deferred<Awaited<ReturnType<typeof api.putSheetCreation>>>();
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  vi.mocked(api.putSheetCreation).mockResolvedValue(fixture({ sheet: {} }));
  if (stage === "create") vi.mocked(api.createEntity).mockReturnValueOnce(creating.promise);
  if (stage === "sheet") vi.mocked(api.putSheetCreation).mockReturnValueOnce(sheet.promise);
  const module = creationModule();
  const { rerender } = render(<Wrap wid="w" kind="items" isNew={false} module={module} />);
  await screen.findByText("+ New item with sheet…");
  if (stage === "refresh") vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  await startWizard();
  await waitFor(() => expect(stage === "create" ? api.createEntity : stage === "sheet" ? api.putSheetCreation : api.listEntities).toHaveBeenCalled());
  rerender(<Wrap wid="w" kind="items" isNew module={module} />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "new draft" } });
  await act(async () => {
    creating.resolve({ id: "old-created" }); sheet.resolve(fixture({ sheet: {} })); listing.resolve([]);
  });
  expect(screen.getByLabelText("Body")).toHaveValue("new draft");
  expect(navigationProbe.path).toBe("/");
});

test.each([false, true])("a departed module preview cannot replace a draft, rejected=%s", async (reject) => {
  const preview = deferred<Awaited<ReturnType<typeof api.readModuleContent>>>();
  vi.mocked(api.readModuleContent).mockReturnValueOnce(preview.promise);
  const module = creationModule();
  const { rerender } = render(<Wrap wid="w" kind="items" isNew={false} module={module} />);
  fireEvent.click(await screen.findByText("Lantern"));
  rerender(<Wrap wid="w" kind="items" isNew module={module} />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "new draft" } });
  await act(async () => { if (reject) preview.reject(new Error("Old preview failed")); else preview.resolve(fixture(template)); });
  expect(screen.getByLabelText("Body")).toHaveValue("new draft");
  expect(screen.queryByText(/Old preview failed/)).not.toBeInTheDocument();
});

test("a module preview failure is reported on the browse surface", async () => {
  vi.mocked(api.readModuleContent).mockRejectedValueOnce(new Error("Cannot read template"));
  render(<Wrap wid="w" kind="items" isNew={false} module={creationModule()} />);
  fireEvent.click(await screen.findByText("Lantern"));
  expect(await screen.findByText(/Cannot read template/)).toBeInTheDocument();
});

test.each([false, true])("a departed instantiate cannot reset or navigate the next scope, rejected=%s", async (reject) => {
  const writing = deferred<{ id: string }>();
  vi.mocked(api.readModuleContent).mockResolvedValue(fixture(template));
  vi.mocked(api.instantiateContent).mockReturnValueOnce(writing.promise);
  const module = creationModule();
  const { rerender } = render(<Wrap wid="w" kind="items" isNew={false} module={module} />);
  fireEvent.click(await screen.findByText("Lantern"));
  fireEvent.click(await screen.findByText("Instantiate"));
  rerender(<Wrap wid="w" kind="items" scope={{ kind: "campaign", id: "w" }} isNew module={module} />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "next scope draft" } });
  await act(async () => { if (reject) writing.reject(new Error("Old instantiate failed")); else writing.resolve({ id: "old" }); });
  expect(screen.getByLabelText("Body")).toHaveValue("next scope draft");
  expect(screen.queryByText(/Old instantiate failed/)).not.toBeInTheDocument();
  expect(navigationProbe.path).toBe("/");
});

test("cancelling a pending wizard retires its later completion", async () => {
  const creating = deferred<{ id: string }>();
  vi.mocked(api.createEntity).mockReturnValueOnce(creating.promise);
  vi.mocked(api.putSheetCreation).mockResolvedValue(fixture({ sheet: {} }));
  render(<Wrap wid="w" kind="items" isNew={false} module={creationModule()} />);
  await startWizard();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByText("+ New item with sheet…");
  await act(async () => { creating.resolve({ id: "old-created" }); });
  expect(navigationProbe.path).toBe("/");
  expect(screen.getByText("+ New item with sheet…")).toBeInTheDocument();
});

test("unmounting the editor retires an instantiate before it can navigate", async () => {
  const writing = deferred<{ id: string }>();
  vi.mocked(api.readModuleContent).mockResolvedValue(fixture(template));
  vi.mocked(api.instantiateContent).mockReturnValueOnce(writing.promise);
  const router = createMemoryRouter([
    { path: "/", element: <EntityEditor wid="w" kind="items" module={creationModule()}
      sectionPath="/items" newHref="/items?new=1" recordHref={(id) => `/items/${id}`} /> },
    { path: "/elsewhere", element: <p>Another section</p> },
    { path: "/items/:id", element: <p>Created item</p> },
  ]);
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByText("Lantern"));
  fireEvent.click(await screen.findByText("Instantiate"));
  await act(async () => { await router.navigate("/elsewhere"); });
  await act(async () => { writing.resolve({ id: "old" }); });
  expect(router.state.location.pathname).toBe("/elsewhere");
  expect(screen.getByText("Another section")).toBeInTheDocument();
});

function RoutedCreation() {
  const { id } = useParams();
  const [params] = useSearchParams();
  return <EntityEditor wid="w" kind="items" module={creationModule()} selected={id ?? null}
    isNew={params.has("new")} sectionPath="/items" newHref="/items?new=1"
    recordHref={(record) => `/items/${record}`} />;
}

test.each(["wizard", "instantiate", "plain"] as const)("a routed %s keeps its committed refresh error after the created record loads", async (action) => {
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "e1", name: "Created item" }, body: "Created body", rev: "r1" });
  vi.mocked(api.putSheetCreation).mockResolvedValue(fixture({ sheet: {} }));
  vi.mocked(api.readModuleContent).mockResolvedValue(fixture(template));
  const router = createMemoryRouter([{ path: "/items/:id?", element: <RoutedCreation /> }], {
    initialEntries: [action === "plain" ? "/items?new=1" : "/items"],
  });
  render(<RouterProvider router={router} />);
  if (action === "plain") await screen.findByLabelText("Name");
  else await screen.findByText("+ New item with sheet…");
  vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  if (action === "wizard") await startWizard();
  else if (action === "instantiate") {
    fireEvent.click(screen.getByText("Lantern"));
    fireEvent.click(await screen.findByText("Instantiate"));
  } else {
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Created item" } });
    fireEvent.click(screen.getByRole("button", { name: "Create item" }));
  }
  await waitFor(() => expect(api.listEntities).toHaveBeenLastCalledWith({ kind: "world", id: "w" }, "items", true));
  await act(async () => { listing.reject(new Error("Committed, but summary unavailable")); });
  await screen.findByText("Created body");
  expect(router.state.location.pathname).toBe("/items/e1");
  expect(screen.getByText(/Committed, but summary unavailable/)).toBeInTheDocument();
  expect(api.deleteEntity).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Create item" })).toBeNull();
  expect(api.createEntity).toHaveBeenCalledTimes(action === "instantiate" ? 0 : 1);
  // This error belongs to the committed record, not a subsequent blank draft.
  await act(async () => { await router.navigate("/items?new=1"); });
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "next draft" } });
  expect(screen.queryByText(/Committed, but summary unavailable/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("next draft");
});

test.each(["create", "refresh"] as const)("plain creation retired during %s cannot replace another world's draft", async (stage) => {
  const creating = deferred<{ id: string }>();
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  if (stage === "create") vi.mocked(api.createEntity).mockReturnValueOnce(creating.promise);
  const { rerender } = render(<Wrap wid="w" kind="items" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Created item" } });
  if (stage === "refresh") vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  fireEvent.click(screen.getByRole("button", { name: "Create item" }));
  await waitFor(() => expect(api.createEntity).toHaveBeenCalledTimes(1));
  if (stage === "refresh") await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "items", true));
  rerender(<Wrap wid="next" kind="items" />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "next world draft" } });
  await act(async () => {
    if (stage === "create") creating.resolve({ id: "e1" });
    else listing.reject(new Error("Old listing failed"));
  });
  expect(screen.getByLabelText("Body")).toHaveValue("next world draft");
  expect(screen.queryByText(/Old listing failed/)).toBeNull();
  expect(navigationProbe.path).toBe("/");
});
