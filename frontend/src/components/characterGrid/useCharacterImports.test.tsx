import { act, renderHook, waitFor } from "@testing-library/react";
import type { ChangeEvent } from "react";
import { api, type ChubImportResult, type EntityScope } from "../../api/client";
import type { LocalizeEvent } from "../../api/stream";
import { fixture } from "../../testkit/fixture";
import { useCharacterImports } from "./useCharacterImports";
import { useCharacterRoster } from "./useCharacterRoster";

vi.mock("../../api/client", () => ({ api: {
  listCharacters: vi.fn(), rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(),
  importCharacter: vi.fn(), importCharacterFromChub: vi.fn(), readCharacter: vi.fn(),
  localizeImages: vi.fn(), importCharacterBook: vi.fn(),
} }));

const WORLD: EntityScope = { kind: "world", id: "realm" };
const OTHER: EntityScope = { kind: "world", id: "saltmarch" };
const navigate = vi.fn();
const choice = { into: null, versionName: "", url: "" };
const summary = { total: 3, localized: 2, skipped: 1, failed: 0, capped: false };
const chub = (character = "mara"): ChubImportResult => ({
  character, version: "default", updated: false,
  gallery: { attempted: 1, stored: 1 }, lore: { lorebooks_found: 1, created: [] },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  return renderHook(({ scope }) => {
    const roster = useCharacterRoster(scope);
    const imports = useCharacterImports({
      wid: scope.id, scope, reload: roster.reload, setError: roster.setError,
      navigate, fence: roster.fence,
    });
    return { ...imports, error: roster.error };
  }, { initialProps: { scope: WORLD } });
}

function pick(files = [new File(["{}"], "card.json")]): ChangeEvent<HTMLInputElement> {
  return fixture({ target: { files, value: "card.json" } });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.importCharacter).mockResolvedValue({ character: "mara", version: "default" });
  vi.mocked(api.importCharacterFromChub).mockImplementation(async (_wid, url) => chub(url));
  vi.mocked(api.readCharacter).mockImplementation(async (_scope, cid) =>
    fixture({ meta: { name: cid === "mara" ? "Mara" : "Winifred" } }));
  vi.mocked(api.localizeImages).mockImplementation(async (_w, _c, _v, cb) => cb({ summary }));
  vi.mocked(api.importCharacterBook).mockResolvedValue({ created: [] });
});

test("a committed URL survives failed refresh and the remaining URLs complete with taglines", async () => {
  const { result } = harness();
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
  vi.mocked(api.listCharacters).mockRejectedValueOnce(new Error("listing unavailable"));
  let rejected: unknown;
  await act(async () => {
    await result.current.runBulkUrlImport(["mara", "winifred"]).catch((err: unknown) => { rejected = err; });
  });
  expect(api.importCharacterFromChub).toHaveBeenCalledTimes(2);
  expect(rejected).toBeUndefined();
  expect(result.current.bulkUrl).toBeNull();
  expect(result.current.importMsg).toContain("Added 2/2 characters");
  expect(result.current.importMsg).toContain("4 images localized");
  expect(result.current.importMsg).toMatch(/refresh.*listing unavailable/i);
  expect(result.current.importMsg).not.toContain("· failed —");
  expect(result.current.taglineQueue).toEqual([{ cid: "mara", name: "Mara" },
    { cid: "winifred", name: "Winifred" }]);
  expect(api.localizeImages).toHaveBeenCalledTimes(2);
  expect(api.importCharacterBook).toHaveBeenCalledTimes(2);
});

test("a committed file still localizes, queues its tagline and opens after failed refresh", async () => {
  const { result } = harness();
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
  vi.mocked(api.listCharacters).mockRejectedValueOnce(new Error("listing unavailable"));
  act(() => result.current.onPickFiles(pick()));
  await act(async () => { await result.current.confirmImport(choice); });
  expect(api.importCharacter).toHaveBeenCalledTimes(1);
  expect(api.localizeImages).toHaveBeenCalledWith("realm", "mara", "default", expect.any(Function));
  expect(result.current.pendingImport).toBeNull();
  expect(result.current.importMsg).toMatch(/refresh.*listing unavailable/i);
  expect(result.current.taglineQueue).toEqual([{ cid: "mara", name: "Mara" }]);
  expect(navigate).toHaveBeenCalledWith("/worlds/realm/characters/mara?v=default", {
    state: { characterImportNotice: { scope: WORLD, character: "mara", version: "default",
      warning: "Roster refresh failed — Error: listing unavailable" } },
  });
});

test("an actual file failure reports the import error without optional work or navigation", async () => {
  vi.mocked(api.importCharacter).mockRejectedValue(new Error("not a card"));
  const { result } = harness();
  act(() => result.current.onPickFiles(pick()));
  await act(async () => { await result.current.confirmImport(choice); });
  expect(result.current.error).toEqual(new Error("not a card"));
  expect(result.current.importMsg).toBeNull();
  expect(api.localizeImages).not.toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
  expect(result.current.taglineQueue).toEqual([]);
});

test("a multi-file drop retains its import failures and localize summary after refresh failure", async () => {
  const { result } = harness();
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
  vi.mocked(api.listCharacters).mockRejectedValueOnce(new Error("listing unavailable"));
  vi.mocked(api.importCharacter).mockRejectedValueOnce(new Error("bad card"));
  act(() => result.current.onPickFiles(pick([new File(["{}"], "bad.json"), new File(["{}"], "good.json")])));
  await waitFor(() => expect(result.current.importMsg).toContain("Localized 2 images across 1 cards"));
  expect(result.current.error).toContain("bad.json: Error: bad card");
  expect(result.current.importMsg).toMatch(/refresh.*listing unavailable/i);
  expect(result.current.bulkLocalize).toBeNull();
  expect(api.importCharacter).toHaveBeenCalledTimes(2);
});

test("per-URL import, localize and lore failures retain committed counts and names", async () => {
  vi.mocked(api.importCharacterFromChub).mockRejectedValueOnce(new Error("bad URL"));
  vi.mocked(api.localizeImages).mockRejectedValueOnce(new Error("image unreachable"));
  vi.mocked(api.importCharacterBook).mockRejectedValueOnce(new Error("invalid book"));
  const { result } = harness();
  await act(async () => { await result.current.runBulkUrlImport(["bad", "mara", "winifred"]); });
  expect(result.current.importMsg).toContain("Added 2/3 characters");
  expect(result.current.importMsg).toContain("bad: Error: bad URL");
  expect(result.current.importMsg).toContain("Mara: localize failed (Error: image unreachable)");
  expect(result.current.importMsg).toContain("Mara: lorebook import failed (Error: invalid book)");
  expect(result.current.taglineQueue.map((c) => c.cid)).toEqual(["mara", "winifred"]);
});

test("URL reports include both backend and embedded lore and use the committed id if naming fails", async () => {
  vi.mocked(api.importCharacterFromChub).mockResolvedValue({
    ...chub(), lore: { lorebooks_found: 1, created: [{ kind: "lore", id: "tide" }] },
  });
  vi.mocked(api.readCharacter).mockRejectedValue(new Error("name unavailable"));
  vi.mocked(api.importCharacterBook).mockResolvedValue({ created: [{ kind: "lore", id: "shore" }] });
  const { result } = harness();
  await act(async () => { await result.current.runBulkUrlImport(["mara"]); });
  expect(result.current.importMsg).toContain("Added 1/1 character");
  expect(result.current.importMsg).toContain("1 gallery image");
  expect(result.current.importMsg).toContain("2 lore entries imported");
  expect(result.current.taglineQueue).toEqual([{ cid: "mara", name: "mara" }]);
});

test.each(["import", "name", "localize", "book", "refresh"] as const)(
  "a retired URL batch stops after its pending %s and cannot alter a returned visit", async (stage) => {
    const gate = deferred<void>();
    let frames: ((event: LocalizeEvent) => void) | undefined;
    const { result, rerender } = harness();
    await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
    if (stage === "import") vi.mocked(api.importCharacterFromChub).mockImplementationOnce(async () => {
      await gate.promise; return chub();
    });
    if (stage === "name") vi.mocked(api.readCharacter).mockImplementationOnce(async () => {
      await gate.promise; return fixture({ meta: { name: "Mara" } });
    });
    if (stage === "localize") vi.mocked(api.localizeImages).mockImplementationOnce(async (_w, _c, _v, cb) => {
      frames = cb; await gate.promise;
    });
    if (stage === "book") vi.mocked(api.importCharacterBook).mockImplementationOnce(async () => {
      await gate.promise; return { created: [] };
    });
    if (stage === "refresh") vi.mocked(api.listCharacters).mockImplementationOnce(async () => {
      await gate.promise; return [];
    });
    let run!: Promise<void>;
    act(() => { run = result.current.runBulkUrlImport(["mara", "winifred"]); });
    await waitFor(() => {
      if (stage === "import") expect(api.importCharacterFromChub).toHaveBeenCalled();
      if (stage === "name") expect(api.readCharacter).toHaveBeenCalled();
      if (stage === "localize") expect(frames).toBeDefined();
      if (stage === "book") expect(api.importCharacterBook).toHaveBeenCalled();
      if (stage === "refresh") expect(api.listCharacters).toHaveBeenCalledTimes(2);
    });
    rerender({ scope: OTHER });
    expect(result.current.bulkUrl).toBeNull();
    rerender({ scope: WORLD });
    await act(async () => { frames?.({ summary }); gate.resolve(); await run; });
    expect(result.current.bulkUrl).toBeNull();
    expect(result.current.importMsg).toBeNull();
    expect(result.current.taglineQueue).toEqual([]);
    expect(api.importCharacterFromChub).toHaveBeenCalledTimes(1);
    if (stage === "import" || stage === "name") expect(api.localizeImages).not.toHaveBeenCalled();
    if (stage !== "book" && stage !== "refresh") expect(api.importCharacterBook).not.toHaveBeenCalled();
    expect(api.listCharacters).toHaveBeenCalledTimes(stage === "refresh" ? 4 : 3);
    expect(navigate).not.toHaveBeenCalled();
  },
);

test.each(["import", "refresh", "localize", "name"] as const)(
  "a file completion pending at %s cannot navigate or queue a tagline after a world switch", async (stage) => {
    const gate = deferred<void>();
    const { result, rerender } = harness();
    await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
    if (stage === "import") vi.mocked(api.importCharacter).mockImplementationOnce(async () => {
      await gate.promise; return { character: "mara", version: "default" };
    });
    if (stage === "refresh") vi.mocked(api.listCharacters).mockImplementationOnce(async () => {
      await gate.promise; return [];
    });
    if (stage === "localize") vi.mocked(api.localizeImages).mockImplementationOnce(() => gate.promise);
    if (stage === "name") vi.mocked(api.readCharacter).mockImplementationOnce(async () => {
      await gate.promise; return fixture({ meta: { name: "Mara" } });
    });
    act(() => result.current.onPickFiles(pick()));
    let run!: Promise<void>;
    act(() => { run = result.current.confirmImport(choice); });
    await waitFor(() => {
      if (stage === "import") expect(api.importCharacter).toHaveBeenCalled();
      if (stage === "refresh") expect(api.listCharacters).toHaveBeenCalledTimes(2);
      if (stage === "localize") expect(api.localizeImages).toHaveBeenCalled();
      if (stage === "name") expect(api.readCharacter).toHaveBeenCalled();
    });
    rerender({ scope: OTHER });
    await act(async () => { gate.resolve(); await run; });
    expect(result.current.taglineQueue).toEqual([]);
    expect(result.current.importMsg).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
    if (stage === "import" || stage === "refresh") expect(api.localizeImages).not.toHaveBeenCalled();
    expect(api.importCharacter).toHaveBeenCalledTimes(1);
  },
);

test("unmount retires pending imports before they dispatch optional work or the next URL", async () => {
  const gate = deferred<ChubImportResult>();
  vi.mocked(api.importCharacterFromChub).mockReturnValueOnce(gate.promise);
  const { result, unmount } = harness();
  let run!: Promise<void>;
  act(() => { run = result.current.runBulkUrlImport(["mara", "winifred"]); });
  unmount();
  await act(async () => { gate.resolve(chub()); await run; });
  expect(api.importCharacterFromChub).toHaveBeenCalledTimes(1);
  expect(api.localizeImages).not.toHaveBeenCalled();
  expect(api.importCharacterBook).not.toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
});

test.each([
  ["switch", "success"], ["switch", "failure"], ["unmount", "success"], ["unmount", "failure"],
])("a pending file %s/%s cannot navigate or report an obsolete failure", async (leave, outcome) => {
  const gate = deferred<{ character: string; version: string }>();
  vi.mocked(api.importCharacter).mockReturnValueOnce(gate.promise);
  const { result, rerender, unmount } = harness();
  act(() => result.current.onPickFiles(pick()));
  let run!: Promise<void>;
  act(() => { run = result.current.confirmImport(choice); });
  if (leave === "switch") rerender({ scope: OTHER });
  else unmount();
  await act(async () => {
    if (outcome === "success") gate.resolve({ character: "mara", version: "default" });
    else gate.reject(new Error("obsolete failure"));
    await run;
  });
  expect(result.current.error).toBeNull();
  expect(navigate).not.toHaveBeenCalled();
  expect(api.localizeImages).not.toHaveBeenCalled();
});

test("a retired multi-file drop stops before the next file or localize pass", async () => {
  const gate = deferred<{ character: string; version: string }>();
  vi.mocked(api.importCharacter).mockReturnValueOnce(gate.promise);
  const { result, rerender } = harness();
  act(() => result.current.onPickFiles(pick([new File(["{}"], "a.json"), new File(["{}"], "b.json")])));
  rerender({ scope: OTHER });
  await act(async () => { gate.resolve({ character: "mara", version: "default" }); });
  expect(api.importCharacter).toHaveBeenCalledTimes(1);
  expect(api.localizeImages).not.toHaveBeenCalled();
  expect(result.current.bulkLocalize).toBeNull();
  expect(result.current.importMsg).toBeNull();
});

test("a retired bulk localize ignores late frames and stops before the next card", async () => {
  const gate = deferred<void>();
  let frames!: (event: LocalizeEvent) => void;
  vi.mocked(api.localizeImages).mockImplementationOnce((_w, _c, _v, cb) => { frames = cb; return gate.promise; });
  const { result, rerender } = harness();
  act(() => result.current.onPickFiles(pick([new File(["{}"], "a.json"), new File(["{}"], "b.json")])));
  await waitFor(() => expect(result.current.bulkLocalize).not.toBeNull());
  rerender({ scope: OTHER });
  expect(result.current.bulkLocalize).toBeNull();
  await act(async () => { frames({ summary }); gate.resolve(); });
  expect(api.localizeImages).toHaveBeenCalledTimes(1);
  expect(result.current.importMsg).toBeNull();
});

test("visit changes clear pending dialogs and queued taglines, and obsolete commands cannot act", async () => {
  const { result, rerender } = harness();
  await act(async () => { await result.current.runBulkUrlImport(["mara"]); });
  act(() => result.current.onPickFiles(pick()));
  const obsolete = result.current;
  rerender({ scope: OTHER });
  expect(result.current.pendingImport).toBeNull();
  expect(result.current.taglineQueue).toEqual([]);
  expect(result.current.importMsg).toBeNull();
  await act(async () => { await result.current.runBulkUrlImport(["winifred"]); });
  const newFile = new File(["{}"], "new.json");
  act(() => result.current.onPickFiles(pick([newFile])));
  await act(async () => {
    obsolete.cancelPendingImport(); obsolete.nextTagline(); obsolete.onPickFiles(pick());
    await obsolete.confirmImport(choice);
    await obsolete.runBulkUrlImport(["winifred"]);
  });
  expect(result.current.pendingImport).toBe(newFile);
  expect(result.current.taglineQueue).toEqual([{ cid: "winifred", name: "Winifred" }]);
  expect(api.importCharacter).not.toHaveBeenCalled();
  expect(api.importCharacterFromChub).toHaveBeenCalledTimes(2);
});
