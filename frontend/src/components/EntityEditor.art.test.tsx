import { fixture } from "../testkit/fixture";
import { MemoryRouter, useParams, createMemoryRouter, RouterProvider } from "react-router-dom";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { artChanged } from "../appEvents";
import { EntityEditor } from "./EntityEditor";
import { ArtNavigationProvider } from "./art/ArtNavigationProvider";
import { artImage, artRead } from "../testkit/recordArt";
import { setInnerWidth } from "../testkit/viewport";
import { api } from "../api/client";
import { DEFAULTS, Wrap, PathSpy, navigationProbe, editorWith, renderEditor, deferred, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

const artKinds = ["locations", "lore", "items", "groups", "creatures"] as const;
test.each(artKinds.flatMap((kind) => (["world", "campaign"] as const).map((scope) => ({ kind, scope }))))(
  "$scope $kind keeps Details and metadata while sharing the Art panel",
  async ({ kind, scope }) => {
    vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch", keys: "pact" }, body: "At Saltmarch.", rev: "r1" });
    vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target, "avatar", { focus: 20 })]));
    renderEditor({ kind, selected: "saltmarch", scope: { kind: scope, id: "realm" } });
    expect(await screen.findByRole("tab", { name: "Details" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("At Saltmarch.")).toBeInTheDocument();
    expect(document.querySelector(".detail-sidebar")).toBeInTheDocument();
    expect(document.querySelector(".detail-main textarea")).toBeNull();
    expect(screen.queryByRole("button", { name: "Manage art" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
    expect(screen.getByRole("button", { name: "Manage art" })).toBeInTheDocument();
    expect(api.readRecordArt).toHaveBeenCalledWith({ scope: { kind: scope, id: "realm" }, kind, record_id: "saltmarch" }, expect.any(Object));
    expect(screen.getByRole("button", { name: "View Primary image" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Details" }));
    fireEvent.click(within(document.querySelector(".detail-sidebar")!).getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("textbox", { name: "Body" })).toHaveValue("At Saltmarch.");
  });
test("a failed Art read leaves entity prose readable and its count unknown", async () => {
  vi.mocked(api.readRecordArt).mockRejectedValue(new Error("art unavailable"));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  await screen.findByText("x", { selector: ".detail-rendered p" });
  expect(screen.getByRole("tab", { name: "Art" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Art" }));
  expect(await screen.findByText(/Art could not be read:.*art unavailable/)).toBeInTheDocument();
  expect(screen.queryByText("No images yet")).not.toBeInTheDocument();
});
test("an entity Art deep link opens Art without manufacturing an actor version", async () => {
  render(<MemoryRouter initialEntries={["/worlds/realm/items/saltmarch?tab=art&v=ignored"]}>
    <EntityEditor {...DEFAULTS} kind="items" selected="saltmarch" />
  </MemoryRouter>);
  expect(await screen.findByRole("tab", { name: /^Art/ })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("No images yet")).toBeInTheDocument();
  expect(vi.mocked(api.readRecordArt).mock.calls[0][0]).not.toHaveProperty("version");
});
test("refusing Art navigation preserves the selected entity and its draft", async () => {
  // The native Node Request rejects jsdom's AbortSignal; this is the same
  // bridge used by the existing WorldView and Art navigation tests.
  const NativeRequest = globalThis.Request;
  class TestRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      const { signal, ...rest } = init ?? {}; super(input, rest);
      if (signal) Object.defineProperty(this, "signal", { value: signal });
    }
  }
  vi.stubGlobal("Request", TestRequest);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }, { id: "realm", name: "Realm" }]);
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  function AddressedEntity() {
    const { rid } = useParams();
    return <><PathSpy /><EntityEditor {...DEFAULTS} kind="locations" selected={rid}
      sectionPath="/worlds/realm/locations" newHref="/worlds/realm/locations?new=1" recordHref={(id) => "/worlds/realm/locations/" + id} /></>;
  }
  const router = createMemoryRouter([{ path: "/worlds/realm/locations/:rid",
    element: <ArtNavigationProvider><AddressedEntity /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/realm/locations/saltmarch?tab=art"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of avatar" }), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("link", { name: /Back to the list/ }));
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  expect(navigationProbe.path).toBe("/worlds/realm/locations/saltmarch?tab=art");
  expect(screen.getByRole("textbox", { name: "Description of avatar" })).toHaveValue("Unsaved");
  confirm.mockRestore(); router.dispose(); vi.unstubAllGlobals();
});

test("saving entity crop updates its header", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch", has_image: true, image_focus: 20 }]);
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target, "avatar", { focus: 20 })]));
  vi.mocked(api.setRecordArtFocus).mockImplementation(async () => {
    vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch", has_image: true, image_focus: 75 }]);
    vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, [artImage(target, "avatar", { focus: 75 })]));
    return { ok: true };
  });
  const { container } = renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 1" }));
  expect(container.querySelector(".record-art-preview img")).toHaveStyle({ objectPosition: "20% 20%" });
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Adjust primary crop" }));
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "75" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(container.querySelector(".record-art-preview img")).toHaveStyle({ objectPosition: "75% 75%" }));
});

test("initial entity Art count remains unknown while metadata is loading", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(() => new Promise(() => {}));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  expect(await screen.findByRole("tab", { name: "Art" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Art" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Art" }));
  expect(screen.queryByText("No images yet")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Loading art");
});
test.each(artKinds.flatMap(kind => (["avatar", "gallery_1", null] as const).map(imageName => [kind, imageName] as const)))(
  "phone %s entry uses shared View art with %s art", async (kind, imageName) => {
  const restore = setInnerWidth(375);
  try {
    vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch" }, body: "At Saltmarch.", rev: "r1" });
    vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, imageName ? [artImage(target, imageName, { focus: 75 })] : []));
    renderEditor({ kind, selected: "saltmarch" });
    await screen.findByRole("tab", { name: imageName ? "Art 1" : "Art 0" });
    const mobile = document.querySelector<HTMLElement>(".editor-body .mobile-art-entry");
    expect(mobile).not.toBeNull();
    if (imageName) {
      expect(within(mobile!).getByRole("img", { name: "Saltmarch art preview" })).toHaveStyle({ objectPosition: "75% 75%" });
      fireEvent.click(within(mobile!).getByRole("button", { name: "View Saltmarch art" }));
      expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
      if (imageName === "gallery_1") expect(screen.queryByText("Primary")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
    }
    fireEvent.click(within(mobile!).getByRole("button", { name: "View art" }));
    expect(screen.getByRole("tab", { name: /^Art/ })).toHaveAttribute("aria-selected", "true");
    if (!imageName) expect(screen.getByRole("button", { name: "Add images" })).toBeInTheDocument();
  } finally { restore(); }
});
test.each(artKinds)("a New %s form never requests record Art", async (kind) => {
  renderEditor({ kind });
  expect(screen.getByRole("textbox", { name: "Body" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /^Art/ })).not.toBeInTheDocument();
  expect(api.readRecordArt).not.toHaveBeenCalled();
});

test("reusing the entity editor for another kind resolves that kind before reading Art", async () => {
  vi.mocked(api.readEntity).mockImplementation(async (_scope, kind, id) =>
    ({ meta: { id, name: "Saltmarch" }, body: kind === "locations" ? "At Saltmarch." : "Realm.", rev: "r1" }));
  const { rerender } = renderEditor({ kind: "locations", selected: "saltmarch" });
  await screen.findByText("At Saltmarch.");
  rerender(editorWith({ kind: "items", selected: "saltmarch" }));
  expect(await screen.findByText("Realm.", { selector: ".detail-rendered p" })).toBeInTheDocument();
  await waitFor(() => expect(api.readRecordArt).toHaveBeenLastCalledWith(
    { scope: { kind: "world", id: "realm" }, kind: "items", record_id: "saltmarch" }, expect.any(Object)));
});
test("reclassification guards an Art draft before its own confirmation and write", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of avatar" }), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockImplementation((message) => !String(message).startsWith("Discard"));
  fireEvent.change(screen.getByRole("combobox", { name: "Reclassify as" }), { target: { value: "items" } });
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Discard unsaved art changes"));
  expect(api.reclassifyEntity).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Description of avatar" })).toHaveValue("Unsaved");
  confirm.mockRestore();
});
test("removing the open record from the library returns to the grid", async () => {
  vi.mocked(api.demoteFromLibrary).mockResolvedValue({ copied_down: [], dependents: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  navigationProbe.path = "";
  render(<Wrap wid="w" kind="locations" selected="saltmarch" />);
  fireEvent.click(await screen.findByRole("button", { name: /Remove from library/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove and copy down" }));
  await waitFor(() => expect(navigationProbe.path).toBe("/section"));
});

test("library removal refuses to discard an Art draft before deleting the record", async () => {
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target));
  vi.mocked(api.demoteFromLibrary).mockImplementation(() => new Promise(() => {}));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description of avatar" }), { target: { value: "Unsaved" } });
  fireEvent.click(screen.getByRole("button", { name: /Remove from library/ }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(await screen.findByRole("button", { name: "Remove and copy down" }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(api.demoteFromLibrary).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Description of avatar" })).toHaveValue("Unsaved");
  confirm.mockRestore();
});

test("Art invalidation fresh-reads rail summaries and refuses an older initial listing", async () => {
  let finishOld!: (rows: Awaited<ReturnType<typeof api.listEntities>>) => void;
  vi.mocked(api.listEntities).mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
  vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch" }, body: "At Saltmarch.", rev: "r1" });
  const { container } = renderEditor({ kind: "locations", isNew: false });
  // The first listing is held open above, so the grid has nothing to draw yet.
  await waitFor(() => expect(api.listEntities).toHaveBeenCalled());
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch", has_image: true, image_focus: 75 }]);
  act(artChanged);
  await waitFor(() => expect(api.listEntities).toHaveBeenLastCalledWith({ kind: "world", id: "realm" }, "locations", true));
  await waitFor(() => expect(container.querySelector(".loc-row-img")).toHaveStyle({ objectPosition: "75% 75%" }));
  await act(async () => finishOld([{ id: "saltmarch", name: "Saltmarch", has_image: false }]));
  expect(container.querySelector(".loc-row-img")).toHaveStyle({ objectPosition: "75% 75%" });
});

test("a saved Art rail reload cannot reselect the departed entity", async () => {
  vi.mocked(api.readEntity).mockImplementation(async (_scope, _kind, id) => ({
    meta: { id, name: id === "saltmarch" ? "Saltmarch" : "Mara" }, body: id === "saltmarch" ? "At Saltmarch." : "Mara waited.", rev: "r1",
  }));
  vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, [artImage(target), artImage(target, "gallery_1")]));
  const view = renderEditor({ kind: "locations", selected: "saltmarch" });
  fireEvent.click(await screen.findByRole("tab", { name: "Art 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  let finish!: (rows: Awaited<ReturnType<typeof api.listEntities>>) => void;
  vi.mocked(api.listEntities).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Make gallery_1 primary" })).toBeEnabled());
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Make gallery_1 primary" }));
  confirm.mockRestore();
  await waitFor(() => expect(api.promoteRecordArt).toHaveBeenCalled());
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  view.rerender(editorWith({ kind: "locations", selected: "mara" }));
  await waitFor(() => expect(api.readEntity).toHaveBeenLastCalledWith({ kind: "world", id: "realm" }, "locations", "mara"));
  await act(async () => finish([]));
  expect(api.readEntity).toHaveBeenLastCalledWith({ kind: "world", id: "realm" }, "locations", "mara");
});

test("entity viewer returns to its visible heading when the desktop entry becomes hidden", async () => {
  vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch" }, body: "At Saltmarch.", rev: "r1" });
  vi.mocked(api.readRecordArt).mockImplementation(async target => artRead(target, [artImage(target)]));
  renderEditor({ kind: "locations", selected: "saltmarch" });
  await screen.findByRole("tab", { name: "Art 1" });
  const desktop = document.querySelector<HTMLElement>(".entity-record-head .record-art-entry")!;
  fireEvent.click(within(desktop).getByRole("button", { name: "View Saltmarch art" }));
  const restore = setInnerWidth(375);
  const nativeStyle = window.getComputedStyle.bind(window);
  const styleSpy = vi.spyOn(window, "getComputedStyle").mockImplementation(element => {
    const style = nativeStyle(element);
    if (element.closest(".entity-record-head .record-art-entry")) Object.defineProperty(style, "display", { value: "none" });
    return style;
  });
  try {
    fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
    expect(document.activeElement).toBe(document.querySelector(".entity-record-head h3"));
  } finally { styleSpy.mockRestore(); restore(); }
});


test("a committed library move reports an editor summary read failure", async () => {
  vi.mocked(api.libraryStatus).mockResolvedValue({ in_library: false, diverged: false, can_promote: true, can_push: false });
  vi.mocked(api.promoteToLibrary).mockResolvedValue(fixture({}));
  render(<Wrap wid="w" scope={{ kind: "campaign", id: "c" }} kind="items" selected="salt" />);
  await screen.findByRole("button", { name: "Publish to library" });
  vi.mocked(api.listEntities).mockRejectedValueOnce(new Error("Published, but summary unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Publish to library" }));
  expect(await screen.findByText(/Published, but summary unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("a committed library removal reports a summary read failure and retains grid navigation", async () => {
  vi.mocked(api.demoteFromLibrary).mockResolvedValue(fixture({}));
  render(<Wrap wid="w" kind="items" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  await screen.findByRole("button", { name: "Remove and copy down" });
  vi.mocked(api.listEntities).mockRejectedValueOnce(new Error("Removed, but summary unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Remove and copy down" }));
  expect(await screen.findByText(/Removed, but summary unavailable/)).toBeInTheDocument();
  expect(navigationProbe.path).toBe("/section");
});

test("a library move completing after a scope change cannot refresh the old scope into browse", async () => {
  const moving = deferred<Awaited<ReturnType<typeof api.promoteToLibrary>>>();
  vi.mocked(api.libraryStatus).mockResolvedValue({ in_library: false, diverged: false, can_promote: true, can_push: false });
  vi.mocked(api.promoteToLibrary).mockReturnValueOnce(moving.promise);
  vi.mocked(api.listEntities).mockImplementation(async (scope) => scope.id === "old"
    ? [{ id: "salt", name: "Old listed entry" }] : []);
  const { rerender } = render(<Wrap wid="w" scope={{ kind: "campaign", id: "old" }} kind="items" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: "Publish to library" }));
  rerender(<Wrap wid="w" scope={{ kind: "campaign", id: "new" }} kind="items" selected={null} isNew={false} />);
  await screen.findByText("No items yet.");
  await act(async () => { moving.resolve(fixture({})); });
  expect(screen.queryByText("Old listed entry")).not.toBeInTheDocument();
  expect(screen.getByText("No items yet.")).toBeInTheDocument();
});
