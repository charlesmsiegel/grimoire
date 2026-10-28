import { createMemoryRouter, RouterProvider, useParams, useSearchParams } from "react-router-dom";
import { EntityEditor } from "./EntityEditor";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, navigationProbe, deferred, fail, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

// ---- external edits: the save precondition (#35) ----

async function openForEdit() {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
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
  vi.mocked(api.updateEntity).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/changed on disk while you had it open/i)).toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("mine"); // still the user's text
});

test("overwrite retries against the rev the refusal reported", async () => {
  vi.mocked(api.updateEntity).mockRejectedValueOnce(
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
  vi.mocked(api.updateEntity).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  vi.mocked(api.readEntity).mockResolvedValue(
    { meta: { id: "salt", name: "Salt", keys: "pact" }, body: "theirs", rev: "r2" });
  fireEvent.click(await screen.findByRole("button", { name: /discard mine and reload/i }));

  await waitFor(() => expect(screen.getByText("theirs")).toBeInTheDocument());
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a record deleted underneath offers no overwrite", async () => {
  vi.mocked(api.updateEntity).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: null }));
  await openForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/has been deleted/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /overwrite with mine/i })).toBeNull();
});

test("a failure that is not a conflict still reaches the error banner", async () => {
  vi.mocked(api.updateEntity).mockRejectedValue(fail(400, "bad fields", "fields"));
  await openForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  expect(await screen.findByText("bad fields")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

// ---- reclassify (#119) ----

test("the detail sidebar offers every kind but this one", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const picker = screen.getByLabelText<HTMLSelectElement>("Reclassify as");
  expect([...picker.options].map((o) => o.value))
    .toEqual(["", "locations", "items", "groups", "creatures"]);
});

test("reclassifying sends the record's rev and reports where it went", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<Wrap wid="w" kind="lore" selected="salt" onReclassified={onReclassified} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() =>
    expect(api.reclassifyEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      "locations", "r1"));
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt"));
});

test("the id the server hands back is the one navigated to", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.reclassifyEntity).mockResolvedValue({ id: "salt-2", campaigns: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<Wrap wid="w" kind="lore" selected="salt" onReclassified={onReclassified} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt-2"));
});

test("declining the confirm reclassifies nothing", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  await waitFor(() => expect(api.reclassifyEntity).not.toHaveBeenCalled());
});

test("a stale record refuses the move and offers to make it anyway", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.reclassifyEntity).mockRejectedValueOnce(
    fail(409, "record changed", "stale_record", { rev: "r2" }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<Wrap wid="w" kind="lore" selected="salt" onReclassified={onReclassified} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  const anyway = await screen.findByRole("button", { name: /reclassify anyway/i });
  expect(onReclassified).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /overwrite with mine/i })).toBeNull();
  confirm.mockClear();
  vi.mocked(api.reclassifyEntity).mockResolvedValue({ id: "salt", campaigns: [] });
  fireEvent.click(anyway);
  // the on-disk rev, not the one the editor loaded -- and no second confirm
  await waitFor(() =>
    expect(api.reclassifyEntity).toHaveBeenLastCalledWith({ kind: "world", id: "w" }, "lore",
      "salt", "locations", "r2"));
  expect(confirm).not.toHaveBeenCalled();
  await waitFor(() => expect(onReclassified).toHaveBeenCalledWith("locations", "salt"));
});

test("a refused move reports the error and leaves the record where it is", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.reclassifyEntity).mockRejectedValue(fail(400, "already a lore record"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReclassified = vi.fn();
  render(<Wrap wid="w" kind="lore" selected="salt" onReclassified={onReclassified} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  expect(await screen.findByText("already a lore record")).toBeInTheDocument();
  expect(onReclassified).not.toHaveBeenCalled();
});

test("the campaign hint says the world keeps its own copy", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const { container } = render(
    <Wrap wid="w" scope={{ kind: "campaign", id: "c1" }} kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/the world keeps its own/i)).toBeInTheDocument();
});

test("moving out of locations warns that scenes lose their setting", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<Wrap wid="w" kind="locations" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "lore" } });
  expect(confirm.mock.calls[0][0]).toMatch(/no longer show a setting/);
});

test("moving into locations says nothing about settings", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "locations" } });
  expect(confirm.mock.calls[0][0]).not.toMatch(/setting/);
});

// Cancel is a read, so an outage must leave the unsaved draft available.
test("Cancel reread failure keeps the draft and reports the read error", async () => {
  await openForEdit();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "unsaved" } });
  vi.mocked(api.readEntity).mockRejectedValueOnce(new Error("Cannot reload Salt"));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByText(/Cannot reload Salt/)).toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("unsaved");
});

test("a failed Cancel from a departed selection leaves the new draft alone", async () => {
  const pending = deferred();
  const { rerender } = render(<Wrap wid="w" kind="items" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  vi.mocked(api.readEntity).mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  rerender(<Wrap wid="w" kind="items" selected={null} />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "new draft" } });
  await act(async () => { pending.reject(new Error("Old Cancel failed")); });
  expect(screen.queryByText(/Old Cancel failed/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("new draft");
});

test("a rejected Delete retains the record and editable draft", async () => {
  await openForEdit();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.deleteEntity).mockRejectedValueOnce(new Error("Cannot delete Salt"));
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/Cannot delete Salt/)).toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("keep this draft");
});

test("Delete can remove a bookmarked record absent from its summary list", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<Wrap wid="w" kind="items" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", "salt"));
  expect(navigationProbe.path).toBe("/section");
});

test.each([false, true])("a departed Delete cannot change the new view, rejected=%s", async (reject) => {
  const pending = deferred<{ ok: boolean }>();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.deleteEntity).mockReturnValueOnce(pending.promise);
  const { rerender } = render(<Wrap wid="w" kind="items" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  rerender(<Wrap wid="w" kind="items" selected={null} />);
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "new draft" } });
  await act(async () => { if (reject) pending.reject(new Error("Old delete failed")); else pending.resolve({ ok: true }); });
  expect(screen.queryByText(/Old delete failed/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("new draft");
});

function RoutedItems() {
  const { id } = useParams();
  const [params] = useSearchParams();
  return <EntityEditor wid="w" kind="items" selected={id ?? null} isNew={params.has("new")} sectionPath="/items"
    newHref="/items?new=1" recordHref={(record) => `/items/${record}`} />;
}

test("a committed Delete leaves the record address and reports a failed summary refresh", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const router = createMemoryRouter([{ path: "/items/:id?", element: <RoutedItems /> }], { initialEntries: ["/items/salt"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  vi.mocked(api.listEntities).mockRejectedValueOnce(new Error("Deleted, but summary unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/items"));
  expect(await screen.findByText(/Deleted, but summary unavailable/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
});

// The destination must already have rendered before readback rejects: an
// immediate rejection can beat the route effect and hide this ownership gap.
test.each(["delete", "demotion"] as const)("routed %s reports readback failure after its grid has settled", async (action) => {
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.demoteFromLibrary).mockResolvedValue(fixture({}));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const router = createMemoryRouter([{ path: "/items/:id?", element: <RoutedItems /> }], { initialEntries: ["/items/salt"] });
  render(<RouterProvider router={router} />);
  if (action === "delete") fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  else {
    fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
    await screen.findByRole("button", { name: "Remove and copy down" });
  }
  vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  fireEvent.click(screen.getByRole("button", { name: action === "delete" ? "Delete" : "Remove and copy down" }));
  await screen.findByRole("link", { name: "+ New item" });
  expect(router.state.location.pathname).toBe("/items");
  await act(async () => { listing.reject(new Error("Removed, but summary unavailable")); });
  expect(await screen.findByText(/Removed, but summary unavailable/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Body")).not.toBeInTheDocument();
});

test.each(["delete", "demotion"] as const)("routed %s readback cannot report on a later New form", async (action) => {
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.demoteFromLibrary).mockResolvedValue(fixture({}));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const router = createMemoryRouter([{ path: "/items/:id?", element: <RoutedItems /> }], { initialEntries: ["/items/salt"] });
  render(<RouterProvider router={router} />);
  if (action === "delete") fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  else {
    fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
    await screen.findByRole("button", { name: "Remove and copy down" });
  }
  vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  fireEvent.click(screen.getByRole("button", { name: action === "delete" ? "Delete" : "Remove and copy down" }));
  fireEvent.click(await screen.findByRole("link", { name: "+ New item" }));
  fireEvent.change(await screen.findByLabelText("Body"), { target: { value: "new draft" } });
  await act(async () => { listing.reject(new Error("Old removed summary failed")); });
  expect(screen.queryByText(/Old removed summary failed/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("new draft");
  expect(router.state.location.search).toBe("?new=1");
});
