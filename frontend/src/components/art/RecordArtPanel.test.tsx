import { useEffect, useState } from "react";
import { act, fireEvent, render, screen, waitFor, within, cleanup } from "@testing-library/react";
import { MemoryRouter, Link, useLocation, createMemoryRouter, RouterProvider } from "react-router-dom";
import { artChanged } from "../../appEvents";
import { ApiError, api } from "../../api/client";
import { ART_RECORD_KINDS } from "../../api/types";
import { artAdapter, artImage, artRead, artTarget, galleryRow } from "../../testkit/recordArt";
import * as adapters from "./recordArtAdapter";
import { useRecordArt } from "./useRecordArt";
import { ArtNavigationProvider, useArtNavigation } from "./ArtNavigationProvider";
import { RecordArtPanel } from "./RecordArtPanel";
import { RecordArtEntry, RecordArtViewer } from "./RecordArtEntry";

// jsdom's signal differs from the Node signal accepted by native Request.
const NativeRequest = globalThis.Request;
class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal });
  }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test.each(ART_RECORD_KINDS.flatMap((kind) => (["world", "campaign"] as const).map((scope) => [kind, scope] as const)))(
  "%s / %s offers the same guarded Art operations", async (kind, scope) => {
    const target = artTarget(kind, scope), adapter = artAdapter(target);
    vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const sources = [galleryRow(artImage(target, "gallery_1"))];
    vi.spyOn(api, "listWorldImages").mockResolvedValue(sources);
    vi.spyOn(api, "listCampaignGallery").mockResolvedValue(sources);
    vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
    vi.spyOn(api, "listCampaignImages").mockResolvedValue({ images: [], hidden: [] });
    const usage = vi.spyOn(api, "getImageUsage").mockResolvedValue({
      characters: [], pcs: [], entities: [], greetings: [], world_images: [], campaign_images: [], covers: [], collections: [],
    });
    function Surface() {
      const controller = useRecordArt(target, { onSaved: async () => {} });
      return <><RecordArtEntry controller={controller} name={target.record_id} onOpenArt={() => {}} />
        <RecordArtPanel controller={controller} /></>;
    }
    render(<MemoryRouter><Surface /></MemoryRouter>);
    await screen.findByRole("button", { name: "Art (2)" });
    expect(screen.queryByRole("button", { name: "Remove gallery_1" })).not.toBeInTheDocument();
    expect(usage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
    const file = new File(["png"], "Mara.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Image files"), { target: { files: [file] } });
    await waitFor(() => expect(adapter.append).toHaveBeenCalledWith(file));
    await waitFor(() => expect(screen.getByRole("button", { name: "Make gallery_1 primary" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Make gallery_1 primary" }));
    await waitFor(() => expect(adapter.promote).toHaveBeenCalledWith(artImage(target, "gallery_1"), "avatar-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Adjust primary crop" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Adjust primary crop" }));
    fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "70" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(adapter.setFocus).toHaveBeenCalledWith(artImage(target), 70));
    await waitFor(() => expect(screen.getByRole("button", { name: "Description of gallery_1" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Description of gallery_1" }), { target: { value: "A map" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(adapter.setDescription).toHaveBeenCalledWith(artImage(target, "gallery_1"), "A map", "gallery_1-caption-1"));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
    fireEvent.click(screen.getByRole("button", { name: "No description" }));
    await waitFor(() => expect(adapter.setDescription).toHaveBeenLastCalledWith(artImage(target, "gallery_1"), "", "gallery_1-caption-1"));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: "Used in\u2026" })[0]);
    await waitFor(() => expect(usage).toHaveBeenCalledWith("shared", { fresh: true }));
    fireEvent.click(screen.getByRole("button", { name: "Use existing art" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Select " + target.record_id + (target.version ? " / " + target.version : "") + " · gallery_1" }));
    fireEvent.click(screen.getByRole("button", { name: "Add selected image" }));
    await waitFor(() => expect(adapter.reuse).toHaveBeenCalledWith(artImage(target, "gallery_1").placement, "gallery", "avatar-1"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Remove gallery_1" }));
    await waitFor(() => expect(adapter.remove).toHaveBeenCalledWith(artImage(target, "gallery_1")));
    if (adapter.draftDescription) expect(adapter.draftDescription).not.toHaveBeenCalled();
  });

test("empty, failed, and gallery-only entries remain honest and reachable", async () => {
  const target = artTarget("locations"), adapter = artAdapter(target, artRead(target, []));
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() { const controller = useRecordArt(target, { onSaved: async () => {} });
    return <><RecordArtEntry controller={controller} name="Saltmarch" onOpenArt={() => {}} /><RecordArtPanel controller={controller} /></>; }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  await screen.findByRole("button", { name: "Art (0)" });
  expect(screen.getByRole("button", { name: "Add images" })).toBeEnabled();
  expect(screen.getByText("No images yet")).toBeVisible();
});

test("a description draft remains available when its placement disappears", async () => {
  const target = artTarget("items"), adapter = artAdapter(target);
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() { const controller = useRecordArt(target, { onSaved: async () => {} });
    return <RecordArtPanel controller={controller} />; }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of gallery_1" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved description" } });
  adapter.read.mockResolvedValue(artRead(target, []));
  artChanged();
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("textbox")).toHaveValue("Unsaved description");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

function mountArt(adapter: ReturnType<typeof artAdapter>) {
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const controller = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><RecordArtEntry controller={controller} name="Saltmarch" onOpenArt={() => {}} />
      <RecordArtPanel controller={controller} /><RecordArtViewer controller={controller} /></>;
  }
  render(<MemoryRouter><Surface /></MemoryRouter>);
}
test("failed counts stay unknown and gallery-only previews do not claim a primary", async () => {
  const target = artTarget("lore"), failed = artAdapter(target);
  failed.read.mockRejectedValue(new Error("offline"));
  mountArt(failed);
  await screen.findByText(/Art could not be read/);
  expect(screen.getByRole("button", { name: "Art" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Art (0)" })).not.toBeInTheDocument();
  cleanup(); vi.restoreAllMocks();
  const adapter = artAdapter(target, artRead(target, [artImage(target, "gallery_2"), artImage(target, "gallery_10")]));
  mountArt(adapter);
  await screen.findByRole("button", { name: "Art (2)" });
  expect(screen.getByRole("button", { name: "View Saltmarch art" })).toBeVisible();
  expect(screen.queryByText("Primary")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  expect(screen.queryByRole("button", { name: "Adjust primary crop" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /^View Gallery image/ }).map((node) => node.getAttribute("aria-label")))
    .toEqual(["View Gallery image 2", "View Gallery image 10"]);
});
test("world originals are viewable with no destination management controls", async () => {
  const target = artTarget("locations", "campaign"), data = artRead(target);
  data.world_originals = [artImage(artTarget("locations"), "avatar", { provenance: "world-original" })];
  const adapter = artAdapter(target, data);
  mountArt(adapter);
  const originals = await screen.findByRole("region", { name: "World originals" });
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  expect(within(originals).queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
  fireEvent.click(within(originals).getByRole("button", { name: "View Primary image" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeVisible();
  expect(adapter.remove).not.toHaveBeenCalled();
});
test("masked shared value is visible before any caption rebase or save", async () => {
  const target = artTarget("items", "campaign"), image = artImage(target, "avatar", {
    effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] },
  });
  const adapter = artAdapter(target, artRead(target, [image]));
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  expect(screen.getByText("Shared description").nextElementSibling).toHaveTextContent("A map");
  expect(screen.getByRole("textbox")).toHaveValue("A portrait");
  expect(adapter.setDescription).not.toHaveBeenCalled();
});
test("confirmed partial caption keeps its editor and exposes cleanup without rewriting shared text", async () => {
  const target = artTarget("items"), adapter = artAdapter(target);
  const recovery = { available: true as const, description_rev: "recovery-1",
    effective_description: { scope: "legacy" as const, text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared" as const, text: "A map", described: true, conflicts: [] } };
  adapter.setDescription.mockResolvedValue({ status: "partial", kind: "description_cleanup_incomplete",
    committed: true, confirmed_text: "A map", recovery });
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A map" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("The shared description saved, but the local override could not be cleared");
  expect(screen.getByRole("textbox")).toHaveValue("A map");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved description" } });
  fireEvent.click(screen.getByRole("button", { name: "Finish applying saved description" }));
  await waitFor(() => expect(adapter.finishDescription).toHaveBeenCalledWith(artImage(target), "A map", "recovery-1"));
  expect(adapter.setDescription).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox")).toHaveValue("Unsaved description");
});

test("unavailable description metadata stays unknown and cannot open a guarded editor", async () => {
  const target = artTarget("items"), image = artImage(target, "avatar", { description_available: false,
    description_rev: null, effective_description: undefined, write_target_description: undefined, description: "", described: false });
  const adapter = artAdapter(target, artRead(target, [image]));
  mountArt(adapter);
  await screen.findByText("Description metadata unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Manage art" }));
  expect(screen.getByRole("button", { name: "Description of avatar" })).toBeDisabled();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("removed viewer selection returns focus to the surviving Art entry", async () => {
  const adapter = artAdapter();
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "View Primary image" }));
  expect(screen.getByRole("dialog")).toBeVisible();
  adapter.read.mockResolvedValue(artRead(adapter.target, []));
  artChanged();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Art (0)" })).toHaveFocus();
});

test("confirmed partial invalidates other uses and a later shared writer survives cleanup retry", async () => {
  const firstTarget = artTarget("items"), secondTarget = artTarget("characters");
  let shared = "A portrait", revision = "original-1";
  const writes: { description: unknown; cleanup: boolean }[] = [];
  const snapshot = () => ({ description_rev: revision,
    effective_description: { scope: "legacy" as const, text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared" as const, text: shared, described: true, conflicts: [] } });
  const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json" } }));
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (init?.method === "PUT") {
      if (typeof init.body !== "string") throw new Error("Expected JSON request body");
      const parsed: unknown = JSON.parse(init.body);
      if (!parsed || typeof parsed !== "object") throw new Error("Expected description payload");
      const payload = parsed as Record<string, unknown>, cleanup = payload.cleanup_only === true;
      writes.push({ description: payload.description, cleanup });
      if (cleanup) return response({ kind: "stale_description", detail: "Description changed", ...snapshot() }, 409);
      shared = String(payload.description); revision = "saved-1";
      return response({ kind: "description_cleanup_incomplete", detail: "Cleanup failed", committed: true,
        confirmed_text: shared, recovery: { available: true, ...snapshot() } }, 500);
    }
    const first = url.includes("/items/");
    if (!first && !url.includes("/characters/")) throw new Error("Unexpected Art read");
    const target = first ? firstTarget : secondTarget;
    const states = snapshot();
    const image = artImage(target, "avatar", { description: first ? "A portrait" : shared, description_rev: revision,
      effective_description: first ? states.effective_description : states.write_target_description,
      write_target_description: states.write_target_description });
    return response(artRead(target, [image]));
  });
  function Surface() {
    const first = useRecordArt(firstTarget, { onSaved: async () => {} });
    const second = useRecordArt(secondTarget, { onSaved: async () => {} });
    return <><section aria-label="Saltmarch art"><RecordArtPanel controller={first} /></section>
      <section aria-label="Seraphine art"><RecordArtPanel controller={second} /></section></>;
  }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  const first = screen.getByRole("region", { name: "Saltmarch art" });
  const second = screen.getByRole("region", { name: "Seraphine art" });
  fireEvent.click(await within(first).findByRole("button", { name: "Manage art" }));
  fireEvent.click(within(first).getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(within(first).getByRole("textbox"), { target: { value: "A map" } });
  fireEvent.click(within(first).getByRole("button", { name: "Save" }));
  await within(second).findByText("A map");
  await within(first).findByText("The shared description saved, but the local override could not be cleared");
  expect(within(first).getByRole("textbox")).toHaveValue("A map");
  shared = "A banner"; revision = "writer-2"; artChanged();
  await within(second).findByText("A banner");
  fireEvent.click(within(first).getByRole("button", { name: "Finish applying saved description" }));
  await within(first).findByText("The description changed. Review it before saving.");
  expect(shared).toBe("A banner");
  expect(writes).toEqual([{ description: "A map", cleanup: false }, { description: "A map", cleanup: true }]);
});

test.each(ART_RECORD_KINDS)("reopening %s Art reads metadata fresh while keeping the record controller mounted", async (kind) => {
  const target = artTarget(kind), adapter = artAdapter(target);
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const c = useRecordArt(target, { onSaved: async () => {} }), [open, setOpen] = useState(true);
    return <><button onClick={() => setOpen(!open)}>{open ? "Details" : "Open art"}</button>
      {open && <RecordArtPanel controller={c} />}</>;
  }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  await screen.findByRole("button", { name: "View Primary image" });
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  adapter.read.mockResolvedValue(artRead(target, [artImage(target, "avatar", { description: "A map" })]));
  fireEvent.click(screen.getByRole("button", { name: "Open art" }));
  await screen.findByText("A map");
  expect(adapter.read).toHaveBeenLastCalledWith(expect.objectContaining({ fresh: true }));
});

test.each(["shared", "legacy"] as const)("caption editor explains its captured %s write scope", async (scope) => {
  const target = artTarget("locations"), image = artImage(target, "avatar", {
    write_target_description: { scope, text: "A portrait", described: true, conflicts: [] },
  });
  const adapter = artAdapter(target, artRead(target, [image]));
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  expect(screen.getByText(scope === "shared"
    ? "Shared description edits apply wherever this image is used."
    : "This description applies only to this placement.")).toBeVisible();
  adapter.read.mockResolvedValue(artRead(target, [artImage(target, "avatar")]));
  artChanged();
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  // The save baseline remains the draft's captured scope until explicit reload.
  expect(screen.getByText(scope === "shared"
    ? "Shared description edits apply wherever this image is used."
    : "This description applies only to this placement.")).toBeVisible();
});

test("caption editor explains clearing a local mask while saving shared text", async () => {
  const target = artTarget("items"), image = artImage(target, "avatar", {
    effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
  });
  mountArt(artAdapter(target, artRead(target, [image])));
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  expect(screen.getByText("Saving also clears this placement's local description override.")).toBeVisible();
});

test.each(ART_RECORD_KINDS)("reopening %s Art retries a failed initial metadata read", async (kind) => {
  const target = artTarget(kind), adapter = artAdapter(target);
  adapter.read.mockRejectedValueOnce(new Error("offline"));
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const c = useRecordArt(target, { onSaved: async () => {} }), [open, setOpen] = useState(true);
    return <><button onClick={() => setOpen(!open)}>{open ? "Details" : "Open art"}</button>
      {open && <RecordArtPanel controller={c} />}</>;
  }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  fireEvent.click(screen.getByRole("button", { name: "Open art" }));
  await screen.findByRole("button", { name: "View Primary image" });
  expect(adapter.read).toHaveBeenCalledTimes(2);
  expect(adapter.read).toHaveBeenLastCalledWith(expect.objectContaining({ fresh: true }));
});

test("changed primary crop offers recovery inside its modal and retains the local slider", async () => {
  const adapter = artAdapter();
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Adjust primary crop" }));
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "75" } });
  const row = artImage(adapter.target, "avatar", { v: "changed", url: "/api/art/avatar?v=changed",
    placement: { ...artImage(adapter.target).placement, expected_v: "changed" }, focus: 10 });
  adapter.read.mockResolvedValue(artRead(adapter.target, [row]));
  artChanged();
  const keep = await screen.findByRole("button", { name: "Keep crop for current image" });
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByAltText("Current primary art")).toHaveAttribute("src", row.url);
  fireEvent.click(keep);
  expect(screen.getByLabelText("Crop position")).toHaveValue("75");
  expect(screen.getByAltText("avatar full view")).toHaveAttribute("src", row.url);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(adapter.setFocus).toHaveBeenCalledWith(row, 75));
});

test.each(ART_RECORD_KINDS)("%s panel reentry begins in View after leaving Manage", async kind => {
  const adapter = artAdapter(artTarget(kind));
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const controller = useRecordArt(adapter.target, { onSaved: async () => {} });
    const [art, setArt] = useState(true);
    return <><button onClick={() => controller.transition(() => setArt(!art))}>Change tab</button>
      {art && <RecordArtPanel controller={controller} />}</>;
  }
  render(<MemoryRouter><Surface /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Change tab" }));
  fireEvent.click(screen.getByRole("button", { name: "Change tab" }));
  expect(screen.getByRole("button", { name: "Manage art" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Remove gallery_1" })).not.toBeInTheDocument();
});

test.each(["inherited", "campaign"] as const)("%s removal identifies the record and discloses campaign-only hiding", async provenance => {
  const target = artTarget("items", "campaign");
  const adapter = artAdapter(target, artRead(target, [artImage(target, "avatar", { provenance })]));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove avatar" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/Saltmarch.*primary.*only in this campaign.*world/i));
  expect(adapter.remove).not.toHaveBeenCalled();
});


test("accepted same-owner route discard retires the draft after Art unmounts", async () => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  function Surface() {
    const location = useLocation(), c = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><Link to="?tab=details">Details link</Link><Link to="?tab=art">Art link</Link>
      {location.search === "?tab=art" && <RecordArtPanel controller={c} />}</>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }],
    { initialEntries: ["/?tab=art"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved" } });
  fireEvent.click(screen.getByRole("link", { name: "Details link" }));
  await waitFor(() => expect(router.state.location.search).toBe("?tab=details"));
  fireEvent.click(screen.getByRole("link", { name: "Art link" }));
  await waitFor(() => expect(router.state.location.search).toBe("?tab=art"));
  expect(confirm).toHaveBeenCalledOnce();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  router.dispose();
});

test("retrying one upload preserves the other failed file and its retry action", async () => {
  const adapter = artAdapter();
  adapter.append.mockRejectedValueOnce(new Error("First failure")).mockRejectedValueOnce(new Error("Second failure"));
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  const files = ["Mara.png", "Saltmarch.png"].map(name => new File(["png"], name, { type: "image/png" }));
  fireEvent.change(screen.getByLabelText("Image files"), { target: { files } });
  await screen.findByRole("button", { name: "Retry Saltmarch.png" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry Mara.png" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Retry Mara.png" }));
  await waitFor(() => expect(adapter.append).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry Saltmarch.png" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Retry Saltmarch.png" }));
  await waitFor(() => expect(adapter.append).toHaveBeenLastCalledWith(files[1]));
});

test.each([false, true])("same-record Back closes an Art viewer (starts in Art: %s)", async (startsInArt) => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const location = useLocation(), c = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><RecordArtEntry controller={c} name="Seraphine" onOpenArt={() => {}} />
      <RecordArtViewer controller={c} />
      {new URLSearchParams(location.search).get("tab") === "art" && <RecordArtPanel controller={c} />}</>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/Realm/characters/Seraphine" + (startsInArt ? "?tab=art" : "")] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "View Seraphine art" }));
  fireEvent.click(screen.getByRole("button", { name: "Open record" }));
  await waitFor(() => expect(new URLSearchParams(router.state.location.search).get("tab")).toBe("art"));
  fireEvent.click(await screen.findByRole("button", { name: "View Gallery image 1" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  await act(() => router.navigate(-1));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  router.dispose();
});

test("accepted same-record Open record leaves management", async () => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Surface() {
    const c = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><RecordArtEntry controller={c} name="Seraphine" onOpenArt={() => {}} />
      <RecordArtViewer controller={c} /><RecordArtPanel controller={c} /></>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/Realm/characters/Seraphine?tab=art"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "View Gallery image 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Open record" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Manage art" })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Remove gallery_1" })).not.toBeInTheDocument();
  router.dispose();
});

test.each(["legacy", "shared"] as const)("initial %s caption editing exposes pending shared alternatives before Save", async scope => {
  const target = artTarget("locations");
  const row = artImage(target, "avatar", {
    effective_description: { scope, text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true,
      conflicts: [{ from: "Realm", text: "A different map" }, { from: "Mara", text: "A narrow map" }] },
  });
  const adapter = artAdapter(target, artRead(target, [row]));
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  expect(screen.getByText("From Realm: A different map")).toBeInTheDocument();
  expect(screen.getByText("From Mara: A narrow map")).toBeInTheDocument();
  expect(adapter.setDescription).not.toHaveBeenCalled();
});

test.each([false, true])("same-record Back retires the reuse picker only when accepted (%s)", async allowed => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  vi.spyOn(api, "listWorldImages").mockResolvedValue([]);
  vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
  const guard = vi.fn(() => true);
  function Surface() {
    const navigation = useArtNavigation();
    useEffect(() => navigation.registerDraftGuard(guard), [navigation]);
    const c = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <RecordArtPanel controller={c} />;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/Realm/characters/Seraphine?tab=art", "/worlds/Realm/characters/Seraphine?tab=art&v=main"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Use existing art" }));
  expect(await screen.findByRole("dialog", { name: "Use existing art" })).toBeInTheDocument();
  guard.mockReturnValue(allowed);
  await act(() => router.navigate(-1));
  if (allowed) {
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Use existing art" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Manage art" })).toBeInTheDocument();
  } else {
    expect(screen.getByRole("dialog", { name: "Use existing art" })).toBeInTheDocument();
    expect(router.state.location.search).toBe("?tab=art&v=main");
  }
  router.dispose();
});

test("failed stale-crop refresh can retry inside its modal while keeping the slider", async () => {
  const adapter = artAdapter();
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Adjust primary crop" }));
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "75" } });
  adapter.setFocus.mockRejectedValueOnce(new ApiError(409, "Image changed", "stale_image"));
  adapter.read.mockRejectedValueOnce(new Error("offline"));
  const readsBeforeSave = adapter.read.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(readsBeforeSave + 1));
  const dialog = screen.getByRole("dialog");
  const retry = await within(dialog).findByRole("button", { name: "Reload art" });
  expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
  const row = artImage(adapter.target, "avatar", { v: "changed", url: "/api/art/avatar?v=changed", placement: { ...artImage(adapter.target).placement, expected_v: "changed" } });
  adapter.read.mockResolvedValueOnce(artRead(adapter.target, [row]));
  fireEvent.click(retry);
  fireEvent.click(await within(dialog).findByRole("button", { name: "Keep crop for current image" }));
  expect(screen.getByLabelText("Crop position")).toHaveValue("75");
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(adapter.setFocus).toHaveBeenLastCalledWith(row, 75));
});

test("slow sequential upload displays the active file and completed total", async () => {
  const adapter = artAdapter();
  let finishFirst!: (value: Awaited<ReturnType<typeof adapter.append>>) => void;
  let finishSecond!: (value: Awaited<ReturnType<typeof adapter.append>>) => void;
  adapter.append.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve; }))
    .mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve; }));
  mountArt(adapter);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  const files = ["Mara.png", "Seraphine.png"].map(name => new File(["png"], name, { type: "image/png" }));
  fireEvent.change(screen.getByLabelText("Image files"), { target: { files } });
  expect(await screen.findByText(/Uploading Mara.png.*0 of 2/)).toBeInTheDocument();
  await act(async () => { finishFirst({ name: "gallery_2", ext: "png", v: "new", image_id: "new" }); });
  expect(await screen.findByText(/Uploading Seraphine.png.*1 of 2/)).toBeInTheDocument();
  await act(async () => { finishSecond({ name: "gallery_3", ext: "png", v: "new", image_id: "new" }); });
  await screen.findByText("Added 2 of 2 images");
  expect(screen.queryByText(/^Uploading /)).not.toBeInTheDocument();
});


test.each(["shared", "legacy"] as const)("initial %s description target is labelled with its real scope", async scope => {
  const target = artTarget("locations");
  const row = artImage(target, "avatar", {
    effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope, text: "A map", described: true, conflicts: [] },
  });
  mountArt(artAdapter(target, artRead(target, [row])));
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Description of avatar" }));
  expect(screen.getByText(scope === "shared" ? "Shared description" : "Description for this placement")).toBeInTheDocument();
  if (scope === "legacy") expect(screen.queryByText("Shared description")).not.toBeInTheDocument();
});


test.each(["version", "record", "route"] as const)("pending upload permits a %s change without publishing the old batch", async change => {
  vi.stubGlobal("Request", TestRequest);
  const first = artAdapter(), second = artAdapter(change === "record" ? artTarget("pcs") : { scope: first.target.scope, kind: "characters", record_id: first.target.record_id, version: "era" });
  vi.spyOn(adapters, "createRecordArtAdapter").mockImplementation(target =>
    target.kind === "pcs" || target.version === "era" ? second : first);
  let finish!: () => void;
  first.append.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ name: "gallery_2", ext: "png", v: "new", image_id: "new" }); }));
  let controller!: ReturnType<typeof useRecordArt>;
  function Surface() {
    const location = useLocation();
    const target = location.search === "?next" && change !== "route" ? second.target : first.target;
    controller = useRecordArt(target, { onSaved: async () => {} });
    return <Link to="?next">Next context</Link>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }], { initialEntries: ["/"] });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(controller.status).toBe("ready"));
  let writing!: Promise<void>;
  act(() => { writing = controller.upload([new File(["png"], "Mara.png"), new File(["png"], "Seraphine.png")]); });
  expect(controller.busy).toBe("upload");
  fireEvent.click(screen.getByRole("link", { name: "Next context" }));
  await waitFor(() => expect(router.state.location.search).toBe("?next"));
  if (change === "route") {
    await act(() => controller.promote(artImage(first.target, "gallery_1")));
    expect(first.promote).not.toHaveBeenCalled();
  }
  await act(async () => { finish(); await writing; });
  expect(first.append).toHaveBeenCalledOnce();
  expect(controller.batch).toEqual([]);
  expect(controller.message).toBeNull();
  router.dispose();
});

test.each([false, true])("pending caption navigation honors the dirty decision (%s) and cannot retire a later editor", async allow => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(allow);
  let finish!: () => void;
  adapter.setDescription.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ status: "ok" }); }));
  let controller!: ReturnType<typeof useRecordArt>;
  function Surface() {
    controller = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <Link to="?next">Next context</Link>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }], { initialEntries: ["/"] });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(controller.status).toBe("ready"));
  act(() => controller.editDescription(artImage(adapter.target)));
  act(() => controller.changeDescription("A map"));
  let writing!: Promise<void>;
  act(() => { writing = controller.saveDescription(); });
  fireEvent.click(screen.getByRole("link", { name: "Next context" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  expect(router.state.location.search).toBe(allow ? "?next" : "");
  if (allow) {
    act(() => controller.editDescription(artImage(adapter.target, "gallery_1")));
    act(() => controller.changeDescription("A later draft"));
  }
  await act(async () => { finish(); await writing; });
  if (allow) expect(controller.draft?.kind === "description" && controller.draft.text).toBe("A later draft");
  else expect(controller.draft).toBeNull();
  expect(adapter.setDescription).toHaveBeenCalledOnce();
  router.dispose();
});


test("pending reuse permits route navigation and cannot close a later picker", async () => {
  vi.stubGlobal("Request", TestRequest);
  const adapter = artAdapter(), source = artImage(adapter.target, "gallery_1");
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  vi.spyOn(api, "listWorldImages").mockResolvedValue([galleryRow(source)]);
  vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
  let finish!: () => void;
  adapter.reuse.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ status: "ok", created: { name: "gallery_2", ext: "png", v: "new", image_id: "new" } }); }));
  function Surface() {
    const c = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><Link to="?next">Next context</Link><RecordArtPanel controller={c} /></>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }], { initialEntries: ["/"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manage art" }));
  fireEvent.click(screen.getByRole("button", { name: "Use existing art" }));
  fireEvent.click(await screen.findByRole("radio", { name: "Select Seraphine / main \u00b7 gallery_1" }));
  fireEvent.click(screen.getByRole("button", { name: "Add selected image" }));
  await waitFor(() => expect(adapter.reuse).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("link", { name: "Next context" }));
  await waitFor(() => expect(router.state.location.search).toBe("?next"));
  expect(screen.queryByRole("dialog", { name: "Use existing art" })).not.toBeInTheDocument();
  await act(async () => finish());
  expect(adapter.reuse).toHaveBeenCalledOnce();
  expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  router.dispose();
});


test.each(["version", "record", "route"] as const)("a %s change retires the pending read and requests a current snapshot", async change => {
  vi.stubGlobal("Request", TestRequest);
  const first = artAdapter(), second = artAdapter(change === "record" ? artTarget("pcs") : { scope: first.target.scope, kind: "characters", record_id: first.target.record_id, version: "era" });
  vi.spyOn(adapters, "createRecordArtAdapter").mockImplementation(target => target.kind === "pcs" || target.version === "era" ? second : first);
  let finish!: (value: ReturnType<typeof artRead>) => void;
  first.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let controller!: ReturnType<typeof useRecordArt>;
  function Surface() {
    const location = useLocation();
    controller = useRecordArt(location.search === "?next" && change !== "route" ? second.target : first.target, { onSaved: async () => {} });
    return <Link to="?next">Next context</Link>;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }], { initialEntries: ["/"] });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(first.read).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("link", { name: "Next context" }));
  await waitFor(() => expect(controller.status).toBe("ready"));
  await act(async () => finish(artRead(first.target, [artImage(first.target, "avatar", { v: "retired" })])));
  expect(router.state.location.search).toBe("?next");
  expect(controller.primary?.v).toBe("avatar-1");
  expect(controller.target).toEqual(change === "route" ? first.target : second.target);
  router.dispose();
});


test("viewing art during a pending upload preserves the entire submitted batch", async () => {
  const adapter = artAdapter();
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  let finish!: () => void;
  adapter.append.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ name: "gallery_2", ext: "png", v: "new", image_id: "new" }); }));
  let controller!: ReturnType<typeof useRecordArt>;
  function Surface() {
    controller = useRecordArt(adapter.target, { onSaved: async () => {} });
    return <><RecordArtPanel controller={controller} /><RecordArtViewer controller={controller} /></>;
  }
  render(<MemoryRouter><ArtNavigationProvider><Surface /></ArtNavigationProvider></MemoryRouter>);
  await waitFor(() => expect(controller.status).toBe("ready"));
  const files = [new File(["png"], "Mara.png"), new File(["png"], "Seraphine.png")];
  let writing!: Promise<void>;
  act(() => { writing = controller.upload(files); });
  fireEvent.click(screen.getByRole("button", { name: "View Primary image" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  await act(async () => { finish(); await writing; });
  expect(adapter.append.mock.calls.map(([file]) => file.name)).toEqual(files.map(file => file.name));
  expect(controller.batch.map(row => row.status)).toEqual(["saved", "saved"]);
  expect(controller.message).toBe("Added 2 of 2 images");
});
