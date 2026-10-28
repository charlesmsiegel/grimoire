import { useParams, useSearchParams, createMemoryRouter, RouterProvider } from "react-router-dom";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { EntityEditor } from "./EntityEditor";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { DEFAULTS, editorWith, renderEditor, deferred, fail, installEntityMocks, type Props } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

describe("spec 07 group reference consent", () => {
  const scope = { kind: "campaign" as const, id: "saltmarch" };
  const context = (foreign = true) => ({
    layer: foreign ? "world" as const : "campaign" as const,
    members: foreign ? [] : ["characters:mara"], leader: foreign ? null : "characters:mara",
    filtered_members: foreign ? ["characters:mara"] : [],
    filtered_leader: foreign ? "characters:mara" : null,
    unresolved_members: foreign ? ["characters:mara"] : [],
    unresolved_leader: foreign ? "characters:mara" : null,
  });
  const record = (membership_context = context()) => ({
    meta: { id: "salt-circle", name: "Salt Circle", members: "characters:mara", leader: "characters:mara" },
    body: "A roster.", rev: "r1", membership_context,
  });
  const arrangeMembership = () => {
    vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt-circle", name: "Salt Circle" }]);
    vi.mocked(api.listCharacters).mockResolvedValue([{ id: "mara", name: "Mara", default_version: "v1", versions: [] }]);
    vi.mocked(api.listPCs).mockResolvedValue([{ id: "winifred", name: "Winifred", default_version: "v1", versions: [], tags: [] }]);
    vi.mocked(api.readEntity).mockResolvedValue(record());
  };
  const show = (extra: Partial<Props> = {}) => renderEditor({ kind: "groups", scope, selected: "salt-circle", ...extra });
  const edit = async () => fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const sent = () => vi.mocked(api.updateEntity).mock.calls[0][3];

  beforeEach(arrangeMembership);

  test("shows members chips and saves only changed fields", async () => {
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    const opened = vi.fn(); show({ onOpenOwner: opened });
    fireEvent.click((await screen.findAllByRole("button", { name: "Mara" }))[1]);
    expect(opened).toHaveBeenCalledWith("characters:mara");
    await edit();
    const members = screen.getByRole("group", { name: "Members" });
    fireEvent.click(await within(members).findByRole("checkbox", { name: "Winifred" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().fields).toEqual({ members: "characters:mara, pcs:winifred" });
    expect(sent().group_ref_bindings).toBeUndefined();
  });

  test("marks unresolved World refs even when campaign actor options match", async () => {
    show({ onOpenOwner: vi.fn() });
    await screen.findByRole("button", { name: "Edit" });
    expect(screen.getAllByText("World reference: characters:mara")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Mara" })).not.toBeInTheDocument();
    await edit();
    expect(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Bind Leader reference characters:mara" })).not.toBeChecked();
    expect(screen.getAllByText(/present or future campaign actor/).length).toBeGreaterThan(0);
  });

  test("requires separate Members and Leader opt-ins and includes unchanged opted fields", async () => {
    show(); await edit();
    const members = screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" });
    const leader = screen.getByRole("checkbox", { name: "Bind Leader reference characters:mara" });
    fireEvent.click(members);
    expect(leader).not.toBeChecked();
    fireEvent.click(leader);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().fields).toEqual({ members: "characters:mara", leader: "characters:mara" });
    expect(sent().group_ref_bindings).toEqual([
      { field: "members", actor: "characters:mara" }, { field: "leader", actor: "characters:mara" },
    ]);
    await screen.findByRole("button", { name: "Edit" });
    await edit();
    expect(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" })).not.toBeChecked();
  });

  test("a body-only edit preserves legacy Members without granting any binding", async () => {
    vi.mocked(api.readEntity).mockResolvedValue(fixture({ ...record(), meta: { ...record().meta, members: "Mara, characters:mara" } }));
    show(); await edit();
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "A revised roster." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().fields).toBeUndefined();
    expect(sent().group_ref_bindings).toBeUndefined();
  });

  test("removing and re-adding a slot discards its old opt-in", async () => {
    show(); await edit();
    fireEvent.click(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" }));
    const members = screen.getByRole("group", { name: "Members" });
    fireEvent.click(within(members).getByRole("checkbox", { name: "World reference: characters:mara" }));
    fireEvent.click(await within(members).findByRole("checkbox", { name: "Mara" }));
    expect(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().group_ref_bindings).toBeUndefined();
  });

  test("missing campaign group context blocks save and offers reload", async () => {
    const { membership_context: _context, ...missing } = record();
    vi.mocked(api.readEntity).mockResolvedValueOnce(missing);
    show(); await edit();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Reload group/ }));
    await screen.findByRole("button", { name: "Edit" }); await edit();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(api.updateEntity).not.toHaveBeenCalled();
  });

  test("identity 409 requires fresh review and never retries or offers overwrite", async () => {
    vi.mocked(api.updateEntity).mockRejectedValueOnce(fail(409, "Reload and review references", "group_identity_conflict"));
    show(); await edit();
    fireEvent.click(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Reload and review references");
    expect(api.updateEntity).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Overwrite/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reload group/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("late context and actor option replies cannot restore old scope consent", async () => {
    const oldRead = deferred<ReturnType<typeof record>>();
    const oldActors = deferred<Awaited<ReturnType<typeof api.listCharacters>>>();
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false))).mockReturnValueOnce(oldRead.promise);
    vi.mocked(api.listCharacters).mockResolvedValue([{ id: "mara", name: "Mara", default_version: "v1", versions: [] }]).mockReturnValueOnce(oldActors.promise);
    const view = show();
    view.rerender(editorWith({ kind: "groups", scope: { kind: "campaign", id: "saltmarch-2" }, selected: "salt-circle" }));
    await screen.findByRole("button", { name: "Edit" });
    await act(async () => {
      oldRead.resolve(record()); oldActors.resolve([{ id: "winifred", name: "Winifred", default_version: "v1", versions: [] }]);
    });
    await waitFor(() => expect(screen.queryByText("World reference: characters:mara")).not.toBeInTheDocument());
    await edit();
    expect(screen.queryByRole("checkbox", { name: /Bind Members/ })).not.toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Members" })).getByRole("checkbox", { name: "Mara" })).toBeChecked();
  });

  test("a late successful save cannot reload a different selected scope or its consent", async () => {
    const saving = deferred<{ ok: boolean }>();
    vi.mocked(api.updateEntity).mockReturnValueOnce(saving.promise);
    const view = show(); await edit();
    fireEvent.click(screen.getByRole("checkbox", { name: "Bind Members reference characters:mara" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    view.rerender(editorWith({ kind: "groups", scope: { kind: "campaign", id: "saltmarch-2" }, selected: "salt-circle" }));
    await screen.findByRole("button", { name: "Edit" }); await edit();
    const reads = vi.mocked(api.readEntity).mock.calls.length;
    await act(async () => { saving.resolve({ ok: true }); });
    expect(api.updateEntity).toHaveBeenCalledTimes(1);
    expect(api.readEntity).toHaveBeenCalledTimes(reads);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Bind Members/ })).not.toBeInTheDocument();
  });

  test("a stale-record reload cannot reopen the old scope after navigation", async () => {
    vi.mocked(api.updateEntity).mockRejectedValueOnce(fail(409, "changed", "stale_record", { rev: "r2" }));
    const view = show(); await edit();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const reload = await screen.findByRole("button", { name: "Discard mine and reload" });
    const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
    vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
    fireEvent.click(reload);
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    view.rerender(editorWith({ kind: "groups", scope: { kind: "campaign", id: "saltmarch-2" }, selected: "salt-circle" }));
    await screen.findByRole("button", { name: "Edit" }); await edit();
    const reads = vi.mocked(api.readEntity).mock.calls.length;
    await act(async () => { listing.resolve([]); });
    expect(api.readEntity).toHaveBeenCalledTimes(reads);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Reload group/ })).not.toBeInTheDocument();
  });

  test("a late new-group save cannot reset a different scope's draft", async () => {
    const creating = deferred<{ id: string }>();
    vi.mocked(api.createEntity).mockReturnValueOnce(creating.promise);
    const view = show({ selected: undefined });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Salt Circle" } });
    fireEvent.click(screen.getByRole("button", { name: "Create group" }));
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    view.rerender(editorWith({ kind: "groups", scope: { kind: "campaign", id: "saltmarch-2" }, selected: "salt-circle" }));
    await screen.findByRole("button", { name: "Edit" }); await edit();
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "Mara keeps this draft." } });
    await act(async () => { creating.resolve({ id: "salt-circle" }); });
    expect(screen.getByLabelText("Body")).toHaveValue("Mara keeps this draft.");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  // 17.8: the list/detail contract driven by a real row click, not `selected=`.
  const routed = () => {
    const NativeRequest = globalThis.Request;
    class TestRequest extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        const { signal, ...rest } = init ?? {}; super(input, rest);
        if (signal) Object.defineProperty(this, "signal", { value: signal });
      }
    }
    vi.stubGlobal("Request", TestRequest);
    const base = "/campaigns/saltmarch/groups";
    function AddressedGroup() {
      const { rid } = useParams();
      const [params] = useSearchParams();
      return <EntityEditor {...DEFAULTS} kind="groups" scope={scope} selected={rid ?? null}
        isNew={params.get("new") === "1"}
        sectionPath={base} newHref={`${base}?new=1`} recordHref={(id) => `${base}/${id}`} />;
    }
    const router = createMemoryRouter([{ path: base, element: <AddressedGroup /> },
      { path: `${base}/:rid`, element: <AddressedGroup /> }], { initialEntries: [base] });
    render(<RouterProvider router={router} />);
    return () => { router.dispose(); vi.unstubAllGlobals(); };
  };

  test("clicking a group row opens it read-only with its Members chips, and + New opens the form", async () => {
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    const done = routed();
    try {
      // The section root is the browse grid; the blank form is its `+ New`.
      expect(await screen.findByRole("link", { name: "Salt Circle" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Create group" })).not.toBeInTheDocument();
      fireEvent.click(await screen.findByRole("link", { name: "Salt Circle" }));
      await screen.findByRole("heading", { name: "Salt Circle" });
      expect(document.querySelector("textarea")).toBeNull();
      expect(screen.queryByRole("button", { name: "Create group" })).not.toBeInTheDocument();
      const section = screen.getByRole("heading", { name: "Members" }).closest(".side-section") as HTMLElement;
      expect(within(section).getByRole("button", { name: "Mara" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^edit$/i })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("link", { name: /Back to the list/ }));
      fireEvent.click(await screen.findByRole("link", { name: "+ New group" }));
      expect(await screen.findByRole("button", { name: "Create group" })).toBeInTheDocument();
      expect(screen.getByLabelText("Body")).toHaveValue("");
      expect(screen.getByRole("group", { name: "Members" })).toBeInTheDocument();
    } finally { done(); }
  });

  test("an owned group's dangling member renders as a not-found chip", async () => {
    vi.mocked(api.readEntity).mockResolvedValue(fixture({
      ...record({ ...context(false), members: ["characters:mara", "characters:ghost"] }),
      meta: { id: "salt-circle", name: "Salt Circle", members: "characters:mara, characters:ghost" },
    }));
    show({ onOpenOwner: vi.fn() });
    await screen.findByRole("button", { name: "Edit" });
    const section = screen.getByRole("heading", { name: "Members" }).closest(".side-section") as HTMLElement;
    await within(section).findByRole("button", { name: "Mara" });
    const dangling = within(section).getByText("characters:ghost");
    expect(dangling).toHaveClass("chip", "dangling");
    expect(dangling.tagName).toBe("SPAN");
    expect(dangling).toHaveAttribute("title", expect.stringMatching(/no longer exists here/));
  });

  test("every guarded Leader slot is marked, and a legacy second Leader is resolved by choosing one", async () => {
    vi.mocked(api.listCharacters).mockResolvedValue([
      { id: "mara", name: "Mara", default_version: "v1", versions: [] },
      { id: "seraphine", name: "Seraphine", default_version: "v1", versions: [] }]);
    vi.mocked(api.readEntity).mockResolvedValue({
      meta: { id: "salt-circle", name: "Salt Circle", leader: "characters:seraphine, characters:mara" },
      body: "A roster.", rev: "r1",
      membership_context: { ...context(), members: [], leader: "characters:seraphine", filtered_members: [],
        filtered_leader: null, unresolved_members: [], unresolved_leader: null,
        unresolved_leaders: ["characters:mara"] },
    });
    show({ onOpenOwner: vi.fn() });
    await screen.findByRole("button", { name: "Edit" });
    // The dormant second token is a World reference, not campaign Mara.
    expect(screen.getByText("World reference: characters:mara")).toBeInTheDocument();
    await edit();
    // A two-token Leader cannot be resent, so no opt-in that could only 400.
    expect(screen.queryByRole("checkbox", { name: /Bind Leader/ })).not.toBeInTheDocument();
    expect(screen.getByText(/holds more than one reference/)).toBeInTheDocument();
    const leader = screen.getByRole("radiogroup", { name: "Leader" });
    // Seraphine's radio already reads checked (the first token), so the way
    // through a radio group is None, then the one Leader to keep.
    fireEvent.click(within(leader).getByRole("radio", { name: "None" }));
    fireEvent.click(await within(leader).findByRole("radio", { name: "Seraphine" }));
    expect(screen.queryByText(/holds more than one reference/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().fields).toEqual({ leader: "characters:seraphine" });
    expect(sent().group_ref_bindings).toBeUndefined();
  });

  test("a single unresolved Leader reported only in the list keeps its opt-in", async () => {
    const listed = { ...context(), unresolved_leader: null, unresolved_leaders: ["characters:mara"] };
    vi.mocked(api.readEntity).mockResolvedValue(record(listed));
    show(); await edit();
    fireEvent.click(screen.getByRole("checkbox", { name: "Bind Leader reference characters:mara" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.updateEntity).toHaveBeenCalledTimes(1));
    expect(sent().group_ref_bindings).toEqual([{ field: "leader", actor: "characters:mara" }]);
  });

  test.each(["reclassify", "delete"])("late %s cannot reset or navigate a different scope's group", async (operation) => {
    const moving = deferred<{ id: string; campaigns: string[] }>();
    const deleting = deferred<{ ok: boolean }>();
    vi.mocked(api.reclassifyEntity).mockReturnValueOnce(moving.promise);
    vi.mocked(api.deleteEntity).mockReturnValueOnce(deleting.promise);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onReclassified = vi.fn();
    const view = show({ onReclassified });
    await screen.findByRole("button", { name: "Edit" });
    if (operation === "reclassify") {
      fireEvent.change(screen.getByLabelText("Reclassify as"), { target: { value: "lore" } });
      await waitFor(() => expect(api.reclassifyEntity).toHaveBeenCalledTimes(1));
    } else {
      await edit();
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledTimes(1));
    }
    vi.mocked(api.readEntity).mockResolvedValue(record(context(false)));
    view.rerender(editorWith({ kind: "groups", scope: { kind: "campaign", id: "saltmarch-2" }, selected: "salt-circle", onReclassified }));
    await screen.findByRole("button", { name: "Edit" }); await edit();
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "Mara keeps this draft." } });
    await act(async () => {
      if (operation === "reclassify") moving.resolve({ id: "salt-circle", campaigns: [] });
      else deleting.resolve({ ok: true });
    });
    expect(screen.getByLabelText("Body")).toHaveValue("Mara keeps this draft.");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(onReclassified).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

});
