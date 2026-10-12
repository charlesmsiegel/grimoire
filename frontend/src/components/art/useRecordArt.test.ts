import { act, renderHook, waitFor } from "@testing-library/react";
import { ApiError } from "../../api/client";
import { artChanged } from "../../appEvents";
import { artAdapter, artImage, artRead, artTarget } from "../../testkit/recordArt";
import * as adapters from "./recordArtAdapter";
import { useRecordArt } from "./useRecordArt";
import { EDIT_FIRST, IN_COLLECTION, REMOVE_FAILED } from "./removal";

afterEach(() => vi.restoreAllMocks());
function setup(adapter = artAdapter()) {
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  const saved = vi.fn(async () => {});
  const hook = renderHook(({ target }) => useRecordArt(target, { onSaved: saved }), { initialProps: { target: adapter.target } });
  return { ...hook, adapter, saved };
}
test("invalidation preserves the draft's original byte and description revisions", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const image = artImage(adapter.target);
  act(() => result.current.editDescription(image));
  act(() => result.current.changeDescription("Unsaved description"));
  adapter.read.mockResolvedValue(artRead(adapter.target, [artImage(adapter.target, "avatar", { description_rev: "changed" })]));
  act(artChanged);
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  expect(result.current.draft?.kind === "description" && result.current.draft.revision).toBe("avatar-caption-1");
  await act(() => result.current.saveDescription());
  expect(adapter.setDescription).toHaveBeenCalledWith(image, "Unsaved description", "avatar-caption-1");
});
test("saved write followed by failed refresh reports a landed save", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.read.mockRejectedValue(new Error("offline"));
  await act(() => result.current.promote(artImage(adapter.target, "gallery_1")));
  expect(adapter.promote).toHaveBeenCalledOnce();
  expect(result.current.message).toBe("Saved, but the gallery could not be refreshed");
});
test("batch tries all files sequentially and keeps each failure for deliberate retry", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.append.mockRejectedValueOnce(new Error("bad image"));
  const files = ["Mara.png", "Seraphine.png", "Saltmarch.png"].map((name) => new File(["png"], name, { type: "image/png" }));
  await act(() => result.current.upload(files));
  expect(adapter.append.mock.calls.map(([file]) => file.name)).toEqual(files.map((file) => file.name));
  expect(result.current.batch.map((row) => row.status)).toEqual(["failed", "saved", "saved"]);
  await act(() => result.current.upload([files[0]]));
  expect(adapter.append).toHaveBeenCalledTimes(4);
});
test("confirmed partial save keeps text, and cleanup uses confirmed text even after editing", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const image = artImage(adapter.target);
  adapter.setDescription.mockResolvedValue({ status: "partial", kind: "description_cleanup_incomplete", committed: true,
    confirmed_text: "A map", recovery: { available: true, description_rev: "recovery-1",
      effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
      write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] } } });
  act(() => { result.current.editDescription(image); });
  act(() => result.current.changeDescription("A map"));
  await act(() => result.current.saveDescription());
  expect(result.current.draft?.kind).toBe("description");
  expect(result.current.message).toBe("The shared description saved, but the local override could not be cleared");
  act(() => result.current.changeDescription("Unsaved description"));
  await act(() => result.current.finishDescription());
  expect(adapter.finishDescription).toHaveBeenCalledWith(image, "A map", "recovery-1");
  expect(adapter.setDescription).toHaveBeenCalledOnce();
  expect(result.current.draft?.kind === "description" && result.current.draft.text).toBe("Unsaved description");
});
test("unavailable recovery never authorizes cleanup", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.setDescription.mockResolvedValue({ status: "partial", kind: "description_cleanup_incomplete", committed: true,
    confirmed_text: "", recovery: { available: false } });
  act(() => result.current.editDescription(artImage(adapter.target)));
  await act(() => result.current.saveDescription());
  await act(() => result.current.finishDescription());
  expect(adapter.finishDescription).not.toHaveBeenCalled();
});
test("old read and write results cannot follow a changed target", async () => {
  const first = artAdapter(), second = artAdapter(artTarget("locations"));
  let finish!: () => void;
  first.append.mockImplementation(() => new Promise((resolve) => { finish = () => resolve({ name: "avatar", ext: "png", v: "new", image_id: "new" }); }));
  vi.spyOn(adapters, "createRecordArtAdapter").mockImplementation((target) => target.kind === "locations" ? second : first);
  const { result, rerender } = renderHook(({ target }) => useRecordArt(target, { onSaved: async () => {} }), { initialProps: { target: first.target } });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  let writing!: Promise<void>;
  act(() => { writing = result.current.upload([new File(["png"], "Mara.png")]); });
  rerender({ target: second.target });
  await waitFor(() => expect(result.current.target?.kind).toBe("locations"));
  await act(async () => { finish(); await writing; });
  expect(result.current.batch).toEqual([]);
  expect(result.current.message).toBeNull();
});

test("ambiguous disconnect refreshes without replaying a mutation", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.promote.mockRejectedValue(new TypeError("disconnected"));
  await act(() => result.current.promote(artImage(adapter.target, "gallery_1")));
  expect(adapter.promote).toHaveBeenCalledOnce();
  expect(adapter.read).toHaveBeenCalledTimes(2);
  expect(result.current.error).toContain("disconnected");
});
test("stale caption requires deliberate reconciliation before updating its revision", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const snapshot = { description_rev: "shared-2",
    effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] } };
  adapter.setDescription.mockRejectedValueOnce(new ApiError(409, "Description changed", "stale_description", snapshot));
  act(() => result.current.editDescription(artImage(adapter.target)));
  act(() => result.current.changeDescription("Unsaved description"));
  await act(() => result.current.saveDescription());
  expect(result.current.draft?.kind === "description" && result.current.draft.revision).toBe("avatar-caption-1");
  await act(() => result.current.saveDescription());
  expect(adapter.setDescription).toHaveBeenCalledOnce();
  act(() => result.current.reconcileDescription("draft"));
  await act(() => result.current.saveDescription());
  expect(adapter.setDescription).toHaveBeenLastCalledWith(expect.anything(), "Unsaved description", "shared-2");
});
test("partial reuse retries promotion of the created slot without appending again", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const created = { name: "gallery_2", ext: "png", v: "created-1", image_id: "new" };
  adapter.reuse.mockResolvedValue({ status: "partial", created, error: { kind: "promotion_failed", detail: "busy" } });
  adapter.read.mockResolvedValue(artRead(adapter.target, [
    artImage(adapter.target, "avatar", { v: "primary-2" }), artImage(adapter.target, "gallery_2", { v: "created-1" })]));
  await act(() => result.current.reuse(artImage(adapter.target, "gallery_1").placement, "primary", "avatar-1"));
  await act(() => result.current.retryPromotion());
  expect(adapter.reuse).toHaveBeenCalledOnce(); expect(adapter.append).not.toHaveBeenCalled();
  expect(adapter.promote).toHaveBeenCalledWith(expect.objectContaining({ name: "gallery_2", v: "created-1" }), "avatar-1");
});
test("an unselected record remains idle and issues no image read", () => {
  const factory = vi.spyOn(adapters, "createRecordArtAdapter");
  const { result } = renderHook(() => useRecordArt(null, { onSaved: async () => {} }));
  expect(result.current.status).toBe("idle"); expect(result.current.count).toBeNull();
  expect(factory).not.toHaveBeenCalled();
});

test("a greeting bridge action guards the draft and owns the pending-write lifecycle", async () => {
  const { result, saved } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(result.current.target!)));
  act(() => result.current.changeDescription("Unsaved"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const action = vi.fn(async () => {});
  await act(() => result.current.runRelated("copy greeting art", action));
  expect(action).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  await act(() => result.current.runRelated("copy greeting art", action));
  expect(action).toHaveBeenCalledOnce();
  expect(saved).toHaveBeenCalledOnce();
  expect(result.current.draft).toBeNull();
  expect(result.current.busy).toBeNull();
});

test("a nested Art entry and tab transition confirms a dirty draft once", async () => {
  const { result } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(result.current.target!)));
  act(() => result.current.changeDescription("Unsaved"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const action = vi.fn();
  act(() => result.current.transition(() => result.current.transition(action)));
  expect(action).toHaveBeenCalledOnce();
  expect(confirm).toHaveBeenCalledOnce();
});

test("description reload belongs to the captured editor, even after cancel and reopen", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(adapter.target, "gallery_1")));
  let finish!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let reading!: Promise<void>;
  act(() => { reading = result.current.reloadDescription(); });
  act(() => result.current.cancelDraft());
  act(() => result.current.editDescription(artImage(adapter.target, "gallery_1")));
  await act(async () => {
    finish(artRead(adapter.target, [artImage(adapter.target, "gallery_1", { description_rev: "new" })]));
    await reading;
  });
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict).toBeNull();
});

test("a landed upload followed by failed refresh withdraws the former empty count", async () => {
  const adapter = artAdapter(artTarget("items"), artRead(artTarget("items"), []));
  const { result } = setup(adapter);
  await waitFor(() => expect(result.current.status).toBe("empty"));
  adapter.read.mockRejectedValue(new Error("offline"));
  await act(() => result.current.upload([new File(["png"], "Saltmarch.png")]));
  expect(result.current.count).toBeNull();
  expect(result.current.status).toBe("failed");
  expect(result.current.message).toContain("Saved");
});

test("leaving the Art panel retires Manage without affecting an open viewer", async () => {
  const { result } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.setManage(true));
  act(() => result.current.openImage(artImage(result.current.target!), document.createElement("button")));
  expect(result.current.manage).toBe(true);
  act(() => result.current.closePanel());
  expect(result.current.manage).toBe(false);
});

test.each(["description", "focus"] as const)("changed bytes require explicit %s draft recovery", async kind => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const old = artImage(adapter.target);
  act(() => kind === "description" ? result.current.editDescription(old) : result.current.editFocus(old));
  act(() => kind === "description" ? result.current.changeDescription("Unsaved") : result.current.changeFocus(70));
  const fresh = artImage(adapter.target, "avatar", { v: "changed", description_rev: "changed-caption",
    placement: { ...old.placement, expected_v: "changed" }, focus: 20 });
  adapter.read.mockResolvedValue(artRead(adapter.target, [fresh]));
  await act(() => result.current.refresh());
  expect(result.current.draft?.image.v).toBe(old.v);
  act(() => result.current.rebaseDraft());
  expect(result.current.draft?.image).toEqual(fresh);
  if (kind === "description") {
    await act(() => result.current.saveDescription());
    expect(adapter.setDescription).toHaveBeenCalledWith(fresh, "Unsaved", "changed-caption");
  } else {
    expect(result.current.draft?.kind === "focus" && result.current.draft.initial).toBe(20);
    await act(() => result.current.saveFocus());
    expect(adapter.setFocus).toHaveBeenCalledWith(fresh, 70);
  }
});

test("a former A write cannot complete into a later A controller after A-B-A", async () => {
  const first = artAdapter(), second = artAdapter(artTarget("items"));
  let finish!: (result: { status: "ok" }) => void;
  first.setDescription.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  vi.spyOn(adapters, "createRecordArtAdapter").mockImplementation(target => target.kind === "items" ? second : first);
  const { result, rerender } = renderHook(({ target }) => useRecordArt(target, { onSaved: async () => {} }),
    { initialProps: { target: first.target } });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(first.target)));
  act(() => result.current.changeDescription("Old write"));
  let writing!: Promise<void>;
  act(() => { writing = result.current.saveDescription(); });
  rerender({ target: second.target });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  rerender({ target: first.target });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(first.target)));
  act(() => result.current.changeDescription("New draft"));
  await act(async () => { finish({ status: "ok" }); await writing; });
  expect(result.current.draft?.kind === "description" && result.current.draft.text).toBe("New draft");
  expect(result.current.message).toBeNull();
});

test("an ambiguous mutation explicitly reports that its write may have landed", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.promote.mockRejectedValue(new TypeError("Disconnected"));
  await act(() => result.current.promote(artImage(adapter.target, "gallery_1")));
  expect(result.current.message).toMatch(/may have landed.*before retrying/i);
  expect(adapter.promote).toHaveBeenCalledOnce();
});

test("explicit-empty intent survives stale caption reconciliation", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.setDescription.mockRejectedValueOnce(new ApiError(409, "Description changed", "stale_description", {
    description_rev: "shared-2",
    effective_description: { scope: "shared", text: "A map", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] },
  }));
  act(() => result.current.editDescription(artImage(adapter.target)));
  await act(() => result.current.saveDescription(""));
  expect(result.current.draft?.kind === "description" && result.current.draft.text).toBe("");
  act(() => result.current.reconcileDescription("draft"));
  await act(() => result.current.saveDescription());
  expect(adapter.setDescription).toHaveBeenLastCalledWith(expect.anything(), "", "shared-2");
});

test("departed reuse does not authorize a picker completion callback", async () => {
  const first = artAdapter(), second = artAdapter(artTarget("items"));
  let finish!: (value: Awaited<ReturnType<typeof first.reuse>>) => void;
  first.reuse.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.spyOn(adapters, "createRecordArtAdapter").mockImplementation(target => target.kind === "items" ? second : first);
  const { result, rerender } = renderHook(({ target }) => useRecordArt(target, { onSaved: async () => {} }),
    { initialProps: { target: first.target } });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  let writing!: ReturnType<typeof result.current.reuse>;
  act(() => { writing = result.current.reuse(artImage(first.target, "gallery_1").placement, "gallery", null); });
  rerender({ target: second.target });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  await act(async () => { finish({ status: "ok", created: { name: "gallery_2", ext: "png", v: "saved", image_id: "saved" } }); });
  expect(await writing).toBeUndefined();
});

test("successful ordinary promotion retires matching partial reuse recovery", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const created = { name: "gallery_2", ext: "png", v: "created-1", image_id: "new" };
  adapter.reuse.mockResolvedValue({ status: "partial", created, error: { kind: "promotion_failed", detail: "busy" } });
  const row = artImage(adapter.target, "gallery_2", { v: "created-1" });
  adapter.read.mockResolvedValue(artRead(adapter.target, [artImage(adapter.target), row]));
  await act(() => result.current.reuse(artImage(adapter.target, "gallery_1").placement, "primary", "avatar-1"));
  expect(result.current.partialReuse).not.toBeNull();
  await act(() => result.current.promote(row));
  expect(result.current.partialReuse).toBeNull();
  expect(adapter.reuse).toHaveBeenCalledOnce();
});

test.each([false, true])("an older caption reload cannot replace newer reconciliation (reconciled: %s)", async reconciled => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(adapter.target)));
  let finishOlder!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finishOlder = resolve; }));
  let older!: Promise<void>;
  act(() => { older = result.current.reloadDescription(); });
  adapter.read.mockResolvedValueOnce(artRead(adapter.target, [artImage(adapter.target, "avatar", { description_rev: "newer-caption" })]));
  await act(() => result.current.reloadDescription());
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict?.description_rev).toBe("newer-caption");
  if (reconciled) act(() => result.current.reconcileDescription("draft"));
  await act(async () => {
    finishOlder(artRead(adapter.target, [artImage(adapter.target, "avatar", { description_rev: "older-caption" })]));
    await older;
  });
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict?.description_rev).toBe(reconciled ? undefined : "newer-caption");
  expect(result.current.draft?.kind === "description" && result.current.draft.revision).toBe(reconciled ? "newer-caption" : "avatar-caption-1");
  expect(result.current.error).toBeNull();
});

test("a superseded art read returns no snapshot to callers", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  let finishOlder!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finishOlder = resolve; }));
  let older!: ReturnType<typeof result.current.refresh>;
  act(() => { older = result.current.refresh(); });
  await act(() => result.current.refresh());
  let snapshot: Awaited<typeof older> = null;
  await act(async () => { finishOlder(artRead(adapter.target)); snapshot = await older; });
  expect(snapshot).toBeNull();
});

test("an explicit caption choice retires a reload already in flight", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(adapter.target)));
  await act(() => result.current.reloadDescription());
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict).toBeTruthy();
  let finish!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let reading!: Promise<void>;
  act(() => { reading = result.current.reloadDescription(); });
  act(() => result.current.reconcileDescription("draft"));
  await act(async () => {
    finish(artRead(adapter.target, [artImage(adapter.target, "avatar", { description_rev: "later-caption" })]));
    await reading;
  });
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict).toBeNull();
  expect(result.current.draft?.kind === "description" && result.current.draft.revision).toBe("avatar-caption-1");
});

test.each(["Done", "another image"])("a refused explicit-empty review guards %s", async action => {
  const adapter = artAdapter();
  const empty = { scope: "shared" as const, text: "", described: false, conflicts: [] };
  const image = artImage(adapter.target, "avatar", { description: "", described: false, effective_description: empty, write_target_description: empty });
  adapter.read.mockResolvedValue(artRead(adapter.target, [image, artImage(adapter.target, "gallery_1")]));
  const { result } = setup(adapter);
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.setDescription.mockRejectedValueOnce(new ApiError(409, "Description changed", "stale_description", {
    description_rev: "shared-2",
    effective_description: { scope: "shared", text: "A map", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] },
  }));
  act(() => { result.current.setManage(true); result.current.editDescription(image); });
  await act(() => result.current.saveDescription(""));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  act(() => {
    if (action === "Done") result.current.setManage(false);
    else result.current.openImage(artImage(adapter.target, "gallery_1"), document.createElement("button"));
  });
  expect(confirm).toHaveBeenCalledOnce();
  expect(result.current.draft?.kind === "description" && result.current.draft.text).toBe("");
  expect(result.current.draft?.kind === "description" && result.current.draft.conflict?.description_rev).toBe("shared-2");
  expect(result.current.manage).toBe(true);
  expect(result.current.viewerProps).toBeNull();
});

test.each([false, true])("superseded post-save art refresh reports only real failures (record failed: %s)", async recordFailed => {
  const { result, adapter, saved } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  let finish!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  if (recordFailed) saved.mockRejectedValueOnce(new Error("offline"));
  let saving!: Promise<unknown>;
  act(() => { saving = result.current.promote(artImage(adapter.target, "gallery_1")); });
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  await act(async () => { artChanged(); await Promise.resolve(); });
  await act(async () => { finish(artRead(adapter.target)); await saving; });
  expect(result.current.readError).toBeNull();
  expect(result.current.message).toBe(recordFailed ? "Saved, but the record could not be refreshed" : "Saved");
});


test.each([false, true])("a recovered primary reuse retires retry promotion (former primary: %s)", async hadPrimary => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const created = { name: "gallery_2", ext: "png", v: "created-1", image_id: "new" };
  adapter.reuse.mockResolvedValue({ status: "partial", created, error: { kind: "promotion_failed", detail: "busy" } });
  const recovered = artImage(adapter.target, "avatar", { v: created.v, image_id: created.image_id });
  const former = artImage(adapter.target, created.name, { v: "former-1", image_id: "former" });
  adapter.read.mockResolvedValue(artRead(adapter.target, hadPrimary ? [recovered, former] : [recovered]));
  await act(() => result.current.reuse(artImage(adapter.target, "gallery_1").placement, "primary", hadPrimary ? "avatar-1" : null));
  expect(result.current.partialReuse).toBeNull();
  expect(result.current.message).toBe("Saved");
  await act(() => result.current.retryPromotion());
  expect(adapter.promote).not.toHaveBeenCalled();
});

test("retry recognizes primary reuse recovered by a later art refresh", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const created = { name: "gallery_2", ext: "png", v: "created-1", image_id: "new" };
  adapter.reuse.mockResolvedValue({ status: "partial", created, error: { kind: "promotion_failed", detail: "busy" } });
  adapter.read.mockResolvedValue(artRead(adapter.target, [artImage(adapter.target), artImage(adapter.target, created.name, created)]));
  await act(() => result.current.reuse(artImage(adapter.target, "gallery_1").placement, "primary", "avatar-1"));
  expect(result.current.partialReuse).not.toBeNull();
  adapter.read.mockResolvedValue(artRead(adapter.target, [artImage(adapter.target, "avatar", { v: created.v, image_id: created.image_id })]));
  await act(() => result.current.refresh());
  await act(() => result.current.retryPromotion());
  expect(result.current.partialReuse).toBeNull();
  expect(result.current.message).toBe("Saved");
  expect(adapter.promote).not.toHaveBeenCalled();
});

test("keeping a draft for replacement bytes retires an older caption reload", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.editDescription(artImage(adapter.target)));
  act(() => result.current.changeDescription("Unsaved description"));
  const replacement = artImage(adapter.target, "avatar", { v: "replacement-1", image_id: "replacement", description_rev: "replacement-caption" });
  adapter.read.mockResolvedValue(artRead(adapter.target, [replacement]));
  await act(() => result.current.refresh());
  let finish!: (data: ReturnType<typeof artRead>) => void;
  adapter.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let reading!: Promise<void>;
  act(() => { reading = result.current.reloadDescription(); });
  act(() => result.current.rebaseDraft());
  await act(async () => { finish(artRead(adapter.target, [replacement])); await reading; });
  expect(result.current.error).toBeNull();
  expect(result.current.draft?.kind === "description" && result.current.draft.text).toBe("Unsaved description");
  await act(() => result.current.saveDescription());
  expect(adapter.setDescription).toHaveBeenLastCalledWith(replacement, "Unsaved description", "replacement-caption");
});

// ---- removal from the viewer (2026-10-12 viewer removal design, §1.2) ----

const RECHECK = "Could not check this picture again. Reload the Art panel and try again.";
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
test("viewer descriptors carry the record's removal question, never a world original's", async () => {
  const target = artTarget("items", "campaign"), data = artRead(target);
  data.world_originals = [artImage(artTarget("items"), "avatar", { provenance: "world-original" })];
  const { result } = setup(artAdapter(target, data));
  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.describe(artImage(target, "gallery_1")).removal).toEqual({
    question: "Hide Saltmarch's gallery 1 only in this campaign? Any inherited world image in this slot is hidden too." });
  expect(result.current.describe(data.world_originals[0]).removal).toBeUndefined();
  expect(result.current.viewerProps).toBeNull();
});
test("a world-scoped actor's question names its avatar, and a read without remove offers none", async () => {
  const target = artTarget("characters"), data = artRead(target);
  const { result, adapter } = setup(artAdapter(target, data));
  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.describe(artImage(target)).removal).toEqual({ question: "Remove Seraphine's avatar?" });
  adapter.read.mockResolvedValue({ ...data, operations: data.operations.filter((operation) => operation !== "remove") });
  await act(() => result.current.refresh(true));
  expect(result.current.describe(artImage(target)).removal).toBeUndefined();
});
test("the open viewer is handed removeFromViewer as onRemove", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.openImage(artImage(adapter.target, "gallery_1"), document.createElement("button")));
  expect(result.current.viewerProps?.onRemove).toBe(result.current.removeFromViewer);
});
test("removeFromViewer refuses while a draft is held and sends nothing", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const image = artImage(adapter.target, "gallery_1");
  act(() => result.current.editDescription(image));
  await expect(result.current.removeFromViewer(result.current.describe(image).key)).rejects.toThrow(EDIT_FIRST);
  expect(adapter.remove).not.toHaveBeenCalled();
});
test("removeFromViewer refuses while another mutation is pending and sends nothing", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const promotion = deferred<{ ok: true }>();
  adapter.promote.mockReturnValue(promotion.promise);
  let promoting!: Promise<void>;
  act(() => { promoting = result.current.promote(artImage(adapter.target, "gallery_1")); });
  await expect(result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key))
    .rejects.toThrow(EDIT_FIRST);
  expect(adapter.remove).not.toHaveBeenCalled();
  await act(async () => { promotion.resolve({ ok: true }); await promoting; });
});
test("removeFromViewer holds the pending exclusion for the call and releases it when it settles", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const removal = deferred<{ ok: true }>();
  adapter.remove.mockReturnValue(removal.promise);
  const key = result.current.describe(artImage(adapter.target, "gallery_1")).key;
  let removing!: Promise<string>;
  act(() => { removing = result.current.removeFromViewer(key); });
  expect(adapter.remove).toHaveBeenCalledWith(artImage(adapter.target, "gallery_1"));
  expect(result.current.busy).toBe("remove");
  await act(() => result.current.promote(artImage(adapter.target, "gallery_1")));
  expect(adapter.promote).not.toHaveBeenCalled();
  await expect(result.current.removeFromViewer(key)).rejects.toThrow(EDIT_FIRST);
  expect(adapter.remove).toHaveBeenCalledOnce();
  await act(async () => { removal.resolve({ ok: true }); await removing; });
  expect(result.current.busy).toBeNull();
  await act(() => result.current.promote(artImage(adapter.target, "gallery_1")));
  expect(adapter.promote).toHaveBeenCalledOnce();
});
test("removeFromViewer resolves removed before its refresh lands, which then runs onSaved", async () => {
  const { result, adapter, saved } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  const reread = deferred<ReturnType<typeof artRead>>();
  adapter.read.mockReturnValue(reread.promise);
  let outcome: string | undefined;
  await act(async () => { outcome = await result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key); });
  expect(outcome).toBe("removed");
  expect(adapter.read).toHaveBeenCalledTimes(2);
  expect(adapter.read).toHaveBeenLastCalledWith(expect.objectContaining({ fresh: true }));
  expect(saved).not.toHaveBeenCalled();
  await act(async () => { reread.resolve(artRead(adapter.target, [artImage(adapter.target)])); });
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(result.current.images.map((image) => image.name)).toEqual(["avatar"]);
});
test.each([
  ["still present", [artImage(artTarget(), "avatar"), artImage(artTarget(), "gallery_1", { v: "gallery_1-2" })], "changed"],
  ["already gone", [artImage(artTarget(), "avatar")], "removed"],
] as const)("a stale removal re-reads and settles by presence (%s)", async (_case, rows, expected) => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.remove.mockRejectedValue(new ApiError(409, "This image changed.", "stale_image"));
  const reread = deferred<ReturnType<typeof artRead>>();
  adapter.read.mockReturnValue(reread.promise);
  let settled = false, outcome: string | undefined;
  let removing!: Promise<void>;
  act(() => { removing = result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key)
    .then((value) => { settled = true; outcome = value; }); });
  await act(async () => {});
  expect(settled).toBe(false);
  await act(async () => { reread.resolve(artRead(adapter.target, [...rows])); await removing; });
  expect(outcome).toBe(expected);
  expect(adapter.read).toHaveBeenLastCalledWith(expect.objectContaining({ fresh: true }));
  expect(result.current.busy).toBeNull();
});
test("a stale removal whose re-read fails asks for a reload instead of guessing", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.remove.mockRejectedValue(new ApiError(409, "This image changed.", "stale_image"));
  adapter.read.mockRejectedValue(new Error("offline"));
  let removing!: Promise<string>;
  act(() => { removing = result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key); });
  await act(async () => { await expect(removing).rejects.toThrow(RECHECK); });
});
test("a stale removal whose re-read is superseded asks for a reload instead of guessing", async () => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.remove.mockRejectedValue(new ApiError(409, "This image changed.", "stale_image"));
  const reread = deferred<ReturnType<typeof artRead>>();
  adapter.read.mockReturnValueOnce(reread.promise);
  let removing!: Promise<string>;
  act(() => { removing = result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key); });
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(2));
  act(artChanged);
  await waitFor(() => expect(adapter.read).toHaveBeenCalledTimes(3));
  reread.resolve(artRead(adapter.target, [artImage(adapter.target)]));
  await act(async () => { await expect(removing).rejects.toThrow(RECHECK); });
});
test.each([
  ["a refusal's own sentence", new ApiError(409, "This slot is locked."), "This slot is locked."],
  ["a collection refusal", new ApiError(409, "image_in_collection"), IN_COLLECTION],
  ["a transport failure", new TypeError("disconnected"), REMOVE_FAILED],
] as const)("any other failure rejects with %s and re-reads nothing", async (_case, error, message) => {
  const { result, adapter } = setup();
  await waitFor(() => expect(result.current.status).toBe("ready"));
  adapter.remove.mockRejectedValue(error);
  let removing!: Promise<string>;
  act(() => { removing = result.current.removeFromViewer(result.current.describe(artImage(adapter.target, "gallery_1")).key); });
  await act(async () => { await expect(removing).rejects.toThrow(message); });
  expect(adapter.read).toHaveBeenCalledOnce();
  expect(result.current.busy).toBeNull();
});
