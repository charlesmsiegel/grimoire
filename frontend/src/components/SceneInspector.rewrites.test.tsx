import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

// ---- Turn history: stored rewrites (regex output processing, spec 6.3) ----

const REWRITTEN_REPLY = {
  index: 3,
  message: { role: "assistant" as const, content: "Mara nods.", response_id: "resp-1",
             rewritten: true, speaker: "Mara" },
};

function renderRewrites(handlers: { onSceneChanged?: () => void; onTranscriptEdited?: () => void } = {}) {
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                       onSceneChanged={handlers.onSceneChanged ?? (() => {})}
                                       onTranscriptEdited={handlers.onTranscriptEdited}
                                       rewritten={[REWRITTEN_REPLY]} /></MemoryRouter>);
}

function mockRewriteRecord() {
  vi.mocked(api.getSceneRewrites).mockResolvedValue({
    "resp-1": { original: "Mara nods. (OOC: she is lying)", rules: ["r-strip", "r-gone"],
                at: "2026-10-05T12:00:00Z" } });
  vi.mocked(api.getRegex).mockResolvedValue(fixture({
    layer: { rules: [{ id: "r-strip", name: "Strip asides" }], off: [] },
    inherited: [], warnings: {} }));
}

test("a rewritten turn shows the original and Restore calls editMessage with restore", async () => {
  mockRewriteRecord();
  vi.mocked(api.editMessage).mockResolvedValue({ ok: true });
  const onSceneChanged = vi.fn();
  const onTranscriptEdited = vi.fn();
  renderRewrites({ onSceneChanged, onTranscriptEdited });
  const row = await screen.findByRole("button", { name: /Rewritten/ });
  expect(api.getSceneRewrites).not.toHaveBeenCalled();   // read on opening, not on mount
  fireEvent.click(row);
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  expect(api.getSceneRewrites).toHaveBeenCalledWith("c", "s");
  expect(within(detail).getByText("Mara nods. (OOC: she is lying)")).toBeInTheDocument();
  expect(within(detail).getByText("Mara nods.")).toBeInTheDocument();
  // Named where a rule is still there to name, its id where it is not.
  expect(within(detail).getByText("Strip asides")).toBeInTheDocument();
  expect(within(detail).getByText("r-gone")).toBeInTheDocument();
  fireEvent.click(within(detail).getByRole("button", { name: "Restore original" }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith(
    "c", "s", 3, "Mara nods. (OOC: she is lying)", { restore: true }));
  await waitFor(() => expect(onTranscriptEdited).toHaveBeenCalledOnce());
  expect(onSceneChanged).not.toHaveBeenCalled();
});

test("a stale restore says so and reloads the scene rather than writing anything", async () => {
  mockRewriteRecord();
  vi.mocked(api.editMessage).mockRejectedValue(Object.assign(new Error("stale"), {
    status: 409, kind: "rewrite_stale",
    detail: "This message no longer matches its recorded rewrite; reload the scene." }));
  const onSceneChanged = vi.fn();
  const onTranscriptEdited = vi.fn();
  renderRewrites({ onSceneChanged, onTranscriptEdited });
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  fireEvent.click(within(detail).getByRole("button", { name: "Restore original" }));
  expect(await screen.findByText(/changed since it was rewritten/)).toBeInTheDocument();
  expect(onSceneChanged).toHaveBeenCalledOnce();
  expect(onTranscriptEdited).not.toHaveBeenCalled();
  expect(api.editMessage).toHaveBeenCalledOnce();
});

test("a rewritten post whose record is gone offers nothing to restore", async () => {
  vi.mocked(api.getSceneRewrites).mockResolvedValue({});
  vi.mocked(api.getRegex).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  renderRewrites();
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  expect(await screen.findByText(/no longer has a recorded original/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Restore original" })).not.toBeInTheDocument();
});

test("a hand-mangled record's rules do not crash the opened rewrite", async () => {
  vi.mocked(api.getSceneRewrites).mockResolvedValue(fixture({
    // @ts-expect-error -- this regression intentionally sends malformed persisted fields.
    "resp-1": { original: "Mara nods. (OOC: she is lying)", rules: "r-strip", at: 7 } }));
  vi.mocked(api.getRegex).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  renderRewrites();
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  expect(within(detail).getByText("Mara nods. (OOC: she is lying)")).toBeInTheDocument();
  expect(within(detail).getByRole("button", { name: "Restore original" })).toBeInTheDocument();
});

test("a later part of a response opens its own record, by the key the scene read names", async () => {
  vi.mocked(api.getSceneRewrites).mockResolvedValue({
    "resp-1": { original: "First part...", rules: ["r-strip"], at: "" },
    "resp-1#c-2": { original: "Second part...", rules: ["r-strip"], at: "" } });
  vi.mocked(api.getRegex).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
    rewritten={[{ index: 4, message: { role: "assistant", content: "Second part…",
      response_id: "resp-1", response_part: "c-2", rewritten: true,
      rewrite_key: "resp-1#c-2", speaker: "Mara" } }]} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 5" });
  expect(within(detail).getByText("Second part...")).toBeInTheDocument();
  expect(within(detail).queryByText("First part...")).not.toBeInTheDocument();
});

test("an opened rewrite stays selected when Turn history is collapsed and reopened", async () => {
  mockRewriteRecord();
  renderRewrites();
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  await screen.findByRole("region", { name: "Rewrite of post 4" });
  fireEvent.click(screen.getByRole("button", { name: /^Turn history/ }));
  expect(screen.queryByRole("region", { name: "Rewrite of post 4" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Turn history/ }));
  await screen.findByRole("region", { name: "Rewrite of post 4" });
});
