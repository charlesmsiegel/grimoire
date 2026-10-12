import { api } from "../../api/client";
import { ART_RECORD_KINDS, type ArtRecordKind, type RecordArtImage,
  type RecordArtTarget } from "../../api/types";
import { onArtChanged } from "../../appEvents";
import { createRecordArtAdapter, toImageDescriptor } from "./recordArtAdapter";

function target(kind: ArtRecordKind, scope: "world" | "campaign"): RecordArtTarget {
  const base = { scope: { kind: scope, id: "Realm #1" }, record_id: "Saltmarch #1" };
  return kind === "characters" || kind === "pcs"
    ? { ...base, kind, version: "era #1" } : { ...base, kind };
}

function image(address: RecordArtTarget): RecordArtImage {
  return { name: "gallery_1", ext: "png", v: "bytes-before", url: "/api/art?v=bytes-before",
    thumb: "/api/art?v=bytes-before&w=160", description: "A portrait", described: true,
    description_rev: "caption-before", description_available: true, provenance: "world",
    effective_description: { scope: "shared", text: "A portrait", described: true, conflicts: [] },
    placement: { surface: "record", target: address, name: "gallery_1", expected_v: "bytes-before" } };
}

function ok(value: unknown) { return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } }); }
const fetched = vi.fn<typeof fetch>();
function lastCall(): [string, RequestInit] {
  const [url, init] = fetched.mock.calls[fetched.mock.calls.length - 1];
  if (typeof url !== "string" || !init) throw new Error("Missing request");
  return [url, init];
}
function lastBody(): unknown {
  const body = lastCall()[1].body;
  if (typeof body !== "string") throw new Error("Expected JSON request body");
  return JSON.parse(body);
}

beforeEach(() => {
  fetched.mockReset().mockImplementation(() => Promise.resolve(ok({ ok: true })));
  vi.stubGlobal("fetch", fetched);
});
afterEach(() => vi.unstubAllGlobals());

test.each(ART_RECORD_KINDS.flatMap(kind => (["world", "campaign"] as const).map(scope => [kind, scope] as const)))(
  "%s in %s uses encoded addressing and captured guards", async (kind, scope) => {
    const address = target(kind, scope);
    const adapter = createRecordArtAdapter(address);
    const row = image(address);
    const prefix = `/api/${scope}s/Realm%20%231`;
    const actor = kind === "characters" || kind === "pcs";
    const base = `${prefix}/${kind}/Saltmarch%20%231${actor ? "/versions/era%20%231" : ""}/images`;
    const art = `${prefix}/record-art/${kind}/Saltmarch%20%231`;
    await adapter.read({ fresh: true });
    expect(lastCall()?.[0]).toBe(art + (actor ? "?version=era+%231" : ""));
    await adapter.remove(row);
    expect(lastCall()?.[0]).toBe(base + "/gallery_1?expected_v=bytes-before");
    await adapter.promote(row, null);
    let url = lastCall()[0];
    expect(url).toBe(base + "/gallery_1/promote");
    expect(lastBody()).toEqual({ expected_v: "bytes-before", expected_primary_v: null });
    await adapter.setFocus({ ...row, name: "avatar", placement: { ...row.placement, name: "avatar" } }, 80);
    url = lastCall()[0];
    expect(url).toBe(base + "/avatar/focus");
    expect(lastBody()).toEqual({ focus: 80, expected_v: "bytes-before" });
    await adapter.setDescription(row, "A map", "caption-before");
    url = lastCall()[0];
    expect(url).toBe(base + "/gallery_1/description");
    expect(lastBody()).toEqual({ description: "A map", expected_v: "bytes-before",
      expected_description_rev: "caption-before" });
    await adapter.finishDescription(row, "A map", "caption-after");
    expect(lastBody()).toEqual({ description: "A map",
      expected_v: "bytes-before", expected_description_rev: "caption-after", cleanup_only: true });
    await adapter.reuse(row.placement, "primary", null);
    url = lastCall()[0];
    expect(url).toBe(art + "/images/link" + (actor ? "?version=era+%231" : ""));
    expect(lastBody()).toEqual({ source: row.placement, intent: "primary", expected_primary_v: null });
    expect(scope === "world" ? typeof adapter.draftDescription : adapter.draftDescription).toBe(
      scope === "world" ? "function" : undefined);
  });

test("only confirmed partial description errors become recovery results and invalidate art", async () => {
  const address = target("characters", "world");
  const row = image(address);
  const changed = vi.fn();
  const unsubscribe = onArtChanged(changed);
  const body = { kind: "description_cleanup_incomplete", detail: "Cleanup unavailable", committed: true,
    confirmed_text: "A map", recovery: { available: false } };
  fetched.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(body), { status: 500 })));
  try {
    await expect(createRecordArtAdapter(address).setDescription(row, "A map", "caption-before"))
      .resolves.toEqual({ status: "partial", kind: body.kind, committed: true,
        confirmed_text: body.confirmed_text, recovery: body.recovery });
    expect(changed).toHaveBeenCalledTimes(1);
    // Existing standalone API callers still observe the original HTTP error.
    await expect(api.setCharacterImageDescription(address.scope, address.record_id, "era #1", row.name, "A map"))
      .rejects.toMatchObject({ status: 500, kind: "description_cleanup_incomplete" });
    expect(changed).toHaveBeenCalledTimes(2);
  } finally { unsubscribe(); }
});

test("stale description and generic failures do not emit saved-art events", async () => {
  const address = target("items", "campaign");
  const changed = vi.fn();
  const unsubscribe = onArtChanged(changed);
  try {
    for (const [status, kind] of [[409, "stale_description"], [500, "server_error"],
      [500, "description_cleanup_incomplete"]] as const) {
      fetched.mockImplementation(() => Promise.resolve(new Response(
        JSON.stringify({ kind, detail: "Refused", committed: kind === "server_error" }), { status })));
      await expect(createRecordArtAdapter(address).setDescription(image(address), "A map", "caption-before"))
        .rejects.toMatchObject({ status, kind });
    }
    expect(changed).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

test("a fresh post-write read does not join an earlier in-flight read", async () => {
  const address = target("lore", "world");
  const adapter = createRecordArtAdapter(address);
  let resolveOld!: (value: Response) => void;
  fetched.mockImplementationOnce(() => new Promise<Response>(resolve => { resolveOld = resolve; }))
    .mockResolvedValueOnce(ok({ name: "avatar" }))
    .mockResolvedValueOnce(ok({ images: [image(address)] }));
  const earlier = adapter.read();
  await adapter.append(new File(["image"], "Saltmarch.png", { type: "image/png" }));
  const fresh = await adapter.read({ fresh: true });
  resolveOld(ok({ images: [] }));
  await earlier;
  expect(fresh.images).toHaveLength(1);
  expect(fetched).toHaveBeenCalledTimes(3);
});

test("display identity names an occurrence and preserves original download addressing", () => {
  const address = target("characters", "world");
  const row = image(address);
  const descriptor = toImageDescriptor(row);
  expect(descriptor.downloadUrl).toBe("/api/art?v=bytes-before&download=1");
  expect(toImageDescriptor({ ...row, v: "new-bytes" }).key).toBe(descriptor.key);
  expect(toImageDescriptor({ ...row, name: "gallery_2", placement: { ...row.placement, name: "gallery_2" } }).key)
    .not.toBe(descriptor.key);
  expect(() => createRecordArtAdapter({ ...address, kind: "greetings" } as unknown as RecordArtTarget)).toThrow();
});

test("a read owns its abort signal without joining an unrelated shared GET", async () => {
  const address = target("groups", "world");
  const signal = new AbortController().signal;
  await createRecordArtAdapter(address).read({ signal });
  expect(lastCall()[1].signal).toBe(signal);
});

test("record and version changes cannot retarget a captured mutation", async () => {
  const address = target("characters", "world");
  const adapter = createRecordArtAdapter(address);
  const row = image(address);
  address.scope.id = "Mara";
  row.placement.target = target("pcs", "campaign");
  expect(() => adapter.remove(row)).toThrow("different record or version");
  expect(fetched).not.toHaveBeenCalled();
});

test("readable partial recovery retains effective and masked shared states", async () => {
  const address = target("characters", "world");
  const recovery = { available: true, description_rev: "caption-after",
    effective_description: { scope: "legacy", text: "A portrait", described: true, conflicts: [] },
    write_target_description: { scope: "shared", text: "A map", described: true, conflicts: [] } };
  fetched.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
    kind: "description_cleanup_incomplete", detail: "Cleanup unavailable", committed: true,
    confirmed_text: "A map", recovery,
  }), { status: 500 })));
  await expect(createRecordArtAdapter(address).setDescription(image(address), "A map", "caption-before"))
    .resolves.toMatchObject({ status: "partial", confirmed_text: "A map", recovery });
});

test("viewer descriptors display record/version identity and meaningful primary roles", () => {
  const actor = target("characters", "world"), entity = target("items", "world");
  expect(toImageDescriptor(image(actor))).toMatchObject({ recordLabel: "Saltmarch #1 / era #1" });
  const primary = (address: RecordArtTarget) => ({ ...image(address), name: "avatar",
    placement: { ...image(address).placement, name: "avatar" } });
  expect(toImageDescriptor(primary(actor))).toMatchObject({ primaryLabel: "Avatar" });
  expect(toImageDescriptor(primary(entity))).toMatchObject({ recordLabel: "Saltmarch #1", primaryLabel: "Primary image" });
});
