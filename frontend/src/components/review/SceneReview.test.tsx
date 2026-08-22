// The end-of-scene review, driven through the page it is a mode of.
//
// These tests moved out of `routes/CampaignView.test.tsx` with the code they
// cover (#378): the review's state and requests are `useSceneReview`, its
// screens are `ReviewColumn` / `ReviewPanel` / `AbsorbEditRow`. They still
// render the whole `CampaignView`, and deliberately so — a review is reached by
// ending a scene, and what it commits is scoped by which scene and which
// campaign the page is on, so a suite that mounted the panel alone would prove
// none of the things this one exists to prove.
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";

// The mocks and the per-test defaults are shared with the play-view suite; see
// `src/testkit/campaignHarness`. A `vi.mock` factory is hoisted above every import
// and can close over nothing, hence the dynamic imports.
vi.mock("../CastPanel", async () =>
  (await import("../../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../NewSceneChooser", async () =>
  (await import("../../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../CalendarConfig", async () =>
  (await import("../../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../ReplayPanel", async () =>
  (await import("../../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../ResponsePresetPicker", async () =>
  (await import("../../testkit/campaignMocks")).componentStubs.ResponsePresetPicker());
vi.mock("../../api/client", async () =>
  (await import("../../testkit/campaignMocks")).campaignApiMock());
vi.mock("../../api/models", () => ({ getModels: vi.fn() }));
import { api } from "../../api/client";
import {
  absorbs, installCampaignMocks, ONE_SCENE, openScene, PHASES_NONE_CUT, playRoutes,
  renderCampaign, reviewResult, withPalette,
} from "../../testkit/campaignHarness";

beforeEach(installCampaignMocks);

/** The review shows one store's proposals at a time, chosen in its column.
 *  Open drawers until `present()` finds what the test is after — which is what
 *  a reviewer does, and saves every test from having to know which store each
 *  edit kind is filed under. */
/** The proposal card whose label matches — approval is a standing verdict on
 *  the card now, not a checkbox inside it. */
/** The review's own column. Named, because the transcript pane beside it is a
 *  `complementary` too. */
export const reviewColumn = () => within(screen.getByRole("complementary", { name: /proposals/i }));

export function cardFor(label: RegExp): HTMLElement {
  const find = () => Array.from(document.querySelectorAll(".absorb-edit"))
    .find((el) => label.test(el.textContent ?? ""));
  showProposal(find);
  const card = find();
  if (!card) throw new Error(`no proposal card matching ${label}`);
  return card as HTMLElement;
}

export function showProposal(present: () => unknown) {
  if (present()) return;
  // Re-queried each pass: clicking a drawer re-renders the column, so a
  // NodeList captured up front holds elements React has already replaced.
  const drawers = () =>
    Array.from(document.querySelectorAll(".context-column .column-row")) as HTMLElement[];
  for (let i = 0; i < drawers().length; i++) {
    fireEvent.click(drawers()[i]);
    if (present()) return;
  }
}

test("End scene fetches a preview, edits, and saves the chronicle", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi"); // scene loaded → activeId set → button enabled
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const summary = await screen.findByLabelText("Scene summary");
  expect((summary as HTMLTextAreaElement).value).toContain("A met B.");
  fireEvent.change(summary, { target: { value: "Edited summary." } });
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ summary: "Edited summary.", one_line: "They met." })));
});

test("End scene review sends approved edits with the summary", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Seraphine — current state");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({
      edits: [expect.objectContaining({ id: "character_state:seraphine", after: "Loyal now." })] })));
});

test("re-absorbing a scene asks for confirmation, then retries with force", async () => {
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any)
    .mockRejectedValueOnce(new ApiError(409, "this scene has already been absorbed",
                                        "already_absorbed"))
    .mockResolvedValueOnce(reviewResult({
      one_line: "Again.", summary: "s", keywords: [], timeline_events: [],
      cast: [], location: "", date: "", edits: [],
      mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
      commit_token: "tok",
      dossiers: { status: "skipped", reason: null, proposed: [], failed: [] },
      voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
      phases: PHASES_NONE_CUT }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(api.absorbScene).toHaveBeenCalledTimes(2));
  // the FIRST attempt must be unforced -- otherwise the guard is bypassed outright
  expect((api.absorbScene as any).mock.calls[0][2]).toBeFalsy();
  // The fourth argument is the generation callback the panel uses to offer
  // Stop while the absorb runs; what this test is about is `force`.
  expect((api.absorbScene as any).mock.calls[1].slice(0, 3)).toEqual(["run", "s1", true]);
  expect(confirm).toHaveBeenCalled();
  expect(await screen.findByLabelText("Scene one-line")).toHaveValue("Again.");
  confirm.mockRestore();
});

test("declining the re-absorb confirmation leaves the scene alone", async () => {
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockRejectedValue(
    new ApiError(409, "this scene has already been absorbed", "already_absorbed"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  expect(api.absorbScene).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("Scene one-line")).toBeNull();
  confirm.mockRestore();
});

test("double-clicking Save summary commits once", async () => {
  // PUT /chronicle is replayable and plot movements append a beat per apply, so a
  // second commit of the same review duplicates them (#235).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  let release: (v: any) => void = () => {};
  (api.saveChronicle as any).mockReturnValue(new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const save = await screen.findByRole("button", { name: /Save chronicle/ });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(api.saveChronicle).toHaveBeenCalledTimes(1);
  release({ id: "s1", one_line: "o", summary: "s", keywords: [], cast: [], location: "",
            date: "", absorbed: "t", applied: [], failures: [] });
  await waitFor(() => expect(screen.queryByLabelText("Scene summary")).toBeNull());
});

test("a review saves to the scene it was absorbed from, not the selected one", async () => {
  // Switching scenes leaves the review panel open, so a save issued afterwards
  // would otherwise be routed at the newly selected scene (#235).
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("Scene summary");
  await openScene(/Two/);                        // switch scenes
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  expect((api.saveChronicle as any).mock.calls[0][1]).toBe("s1");
});

test("a failed save offers a retry that saves, not one that generates a reply", async () => {
  // The shared error banner's Retry calls api.retry (chat generation). Routing a
  // save failure there would invite the user to generate another reply with the
  // unsaved review still open.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.saveChronicle as any).mockRejectedValueOnce(
    Object.assign(new Error("boom"), { detail: "disk full" }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save chronicle/ }));
  const again = await screen.findByRole("button", { name: /Try saving again/ });
  (api.saveChronicle as any).mockResolvedValueOnce({
    id: "s1", one_line: "o", summary: "s", keywords: [], cast: [], location: "",
    date: "", absorbed: "t", applied: [], failures: [] });
  fireEvent.click(again);
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  // the same token both times, so a first PUT that landed cannot commit twice
  const tokens = (api.saveChronicle as any).mock.calls.map((c: any) => c[2].commit_token);
  expect(tokens).toEqual(["tok", "tok"]);
});

test("a failed save keeps the review open and shows the error", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.saveChronicle as any).mockRejectedValue(
    Object.assign(new Error("boom"), { detail: "disk full" }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save chronicle/ }));
  expect(await screen.findByText(/disk full/)).toBeTruthy();
  expect(screen.getByLabelText("Scene summary")).toBeTruthy();  // review survives to retry
});

// The default absorb mock stages one lore edit, so these drive #111's whole
// review loop: a save refused because the target moved, then keep / replace /
// merge on the row that moved.
const LORE_REVIEW = {
  one_line: "They met.", summary: "A met B.", keywords: [], timeline_events: [],
  cast: [], location: "", date: "",
  mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
  commit_token: "tok",
  dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
  voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [],
           failed: [], skipped: [] },
  phases: PHASES_NONE_CUT,
  edits: [{ id: "lore:the-pact", kind: "lore", target: { kind: "lore", id: "the-pact" },
    label: "The Pact — lore", field: "body", authored: false,
    before: "Signed at dusk.", after: "Signed at dusk.\n\nBroken by morning." }],
};
const PACT_CONFLICT = {
  id: "lore:the-pact", label: "The Pact — lore", kind: "lore", field: "body",
  before: "Signed at dusk.", after: "Signed at dusk.\n\nBroken by morning.",
  stored: "Witnessed by the watch.",
  reason: "this record changed since the scene was absorbed",
  mergeable: true, merged: "Witnessed by the watch.\n\nBroken by morning.",
  index: 0,
};

/** Absorb the scene, hit Save, and have the server refuse the batch. */
async function reviewIntoConflict() {
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs(LORE_REVIEW);
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [PACT_CONFLICT] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save chronicle/ }));
  await screen.findByText(/no longer match/);
}

test("a row a later scene already answered wears a badge naming it (#78)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    ...LORE_REVIEW,
    contradictions: [{ id: "lore:the-pact", scene: "002--the-long-quay",
                       label: "The quay burned.", source: "citation" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  // Advisory: it names the later scene and nothing about the save changes --
  // the row is still approvable, and Save is still the same button.
  const badge = await screen.findByText("later scene disagrees");
  expect(badge.getAttribute("title")).toContain("The quay burned.");
  expect(screen.getByRole("button", { name: /Save chronicle/ })).toBeTruthy();
});

test("the ordinary end-of-scene review carries no contradiction badges", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({ ...LORE_REVIEW, contradictions: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("Scene summary");
  expect(screen.queryByText("later scene disagrees")).toBeNull();
});

test("a refused save keeps the review open and shows what the record now says", async () => {
  await reviewIntoConflict();
  expect(screen.getByText("Witnessed by the watch.")).toBeTruthy();
  expect(screen.getByText(/this record changed since the scene was absorbed/)).toBeTruthy();
  // The review survives untouched -- nothing was written, so it is savable again.
  expect(screen.getByLabelText("Scene summary")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Keep stored The Pact/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Replace stored The Pact/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Merge stored The Pact/ })).toBeTruthy();
});

test("Replace authorizes the staged text and the next save carries it", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([
    expect.objectContaining({ id: "lore:the-pact", resolve: "replace",
                              // the value that was on screen, so a record that
                              // moves again is refused rather than overwritten
                              resolve_from: "Witnessed by the watch.",
                              after: "Signed at dusk.\n\nBroken by morning." })]);
});

test("answering one row leaves its duplicate-id sibling unanswered", async () => {
  // `materialize` dedupes only plot threads, so two lore proposals naming one
  // entry can share an edit id. Answering by id would silently answer both.
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  const twin = { ...LORE_REVIEW.edits[0], after: "Signed at dusk.\n\nSealed at noon." };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    ...LORE_REVIEW, edits: [LORE_REVIEW.edits[0], twin] });
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [PACT_CONFLICT, { ...PACT_CONFLICT, after: twin.after, index: 1 }] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save chronicle/ }));
  await screen.findByText(/2 proposed changes no longer match/);

  fireEvent.click(screen.getAllByRole("button", { name: /Replace stored The Pact/ })[0]);

  // one answered, one still waiting -- not both
  expect(await screen.findByText(/One proposed change no longer matches/)).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /Replace stored The Pact/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  const sent = (api.saveChronicle as any).mock.calls[1][2].edits;
  expect(sent.map((e: any) => e.resolve)).toEqual(["replace", undefined]);
});

test("a conflict on the later of two same-id rows lands on that row", async () => {
  // The server drops the rows that were fine, so the conflict list is not
  // positionally aligned with the edits. Matching on id alone put the second
  // row's verdict on the first — answering a proposal nobody looked at while
  // the drifted one stayed unanswered.
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  const twin = { ...LORE_REVIEW.edits[0], after: "Signed at dusk.\n\nSealed at noon." };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    ...LORE_REVIEW, edits: [LORE_REVIEW.edits[0], twin] });
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    // only the SECOND row conflicts; the first was fine and is not in the list
    { conflicts: [{ ...PACT_CONFLICT, after: twin.after, index: 1,
                    merged: "Witnessed by the watch.\n\nSealed at noon." }] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save chronicle/ }));
  await screen.findByText(/One proposed change no longer matches/);

  fireEvent.click(screen.getByRole("button", { name: /Merge stored The Pact/ }));

  // the merged draft went into the SECOND row's box, not the first's
  const boxes = screen.getAllByLabelText("After The Pact — lore");
  expect((boxes[0] as HTMLTextAreaElement).value).toBe("Signed at dusk.\n\nBroken by morning.");
  expect((boxes[1] as HTMLTextAreaElement).value).toBe("Witnessed by the watch.\n\nSealed at noon.");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits.map((e: any) => e.resolve))
    .toEqual([undefined, "merge"]);
});

test("a row that moves again after being answered comes back for a second answer", async () => {
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [{ ...PACT_CONFLICT, stored: "Rewritten by hand.",
                    reason: "this changed again after you answered — the value you were "
                            + "shown is not what is stored now",
                    merged: "Rewritten by hand.\n\nBroken by morning." }] }));

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));

  expect(await screen.findByText(/changed again after you answered/)).toBeTruthy();
  expect(screen.getByText("Rewritten by hand.")).toBeTruthy();
  // answering again re-stamps the snapshot with what is on screen NOW
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(3));
  expect((api.saveChronicle as any).mock.calls[2][2].edits).toEqual([
    expect.objectContaining({ resolve: "replace", resolve_from: "Rewritten by hand." })]);
});

test("Merge prefills the editable text with the server's draft", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Merge stored The Pact/ }));
  expect(screen.getByLabelText("After The Pact — lore")).toHaveValue(
    "Witnessed by the watch.\n\nBroken by morning.");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([
    expect.objectContaining({ id: "lore:the-pact", resolve: "merge",
                              after: "Witnessed by the watch.\n\nBroken by morning." })]);
});

test("Keep stored drops the row from the batch entirely", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Keep stored The Pact/ }));
  expect(screen.queryByText("Witnessed by the watch.")).toBeNull();       // answered
  expect(screen.queryByText(/no longer match/)).toBeNull();               // and counted as such
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([]);
});

test("a staged dossier is editable and sent with the save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "ok", reason: null, proposed: ["seraphine"], failed: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "dossier:seraphine", kind: "dossier",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — campaign dossier",
      field: "dossier", authored: false,
      before: "Seraphine is wary.", after: "Seraphine now rides with the party." }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After Seraphine — campaign dossier");
  fireEvent.change(ta, { target: { value: "Seraphine rides ahead." } });
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "dossier:seraphine", after: "Seraphine rides ahead." })] })));
});

test("rejecting an edit excludes it from the save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  // Rejecting is a verdict now, not the absence of a tick: the footer counts
  // what is still unjudged, so "I looked at this and said no" has to be
  // something the reviewer can actually say.
  fireEvent.click(await screen.findByLabelText("Reject Seraphine — current state"));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [] })));
});

test("character_state row renders a multi-section knowledge body in its textarea", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", authored: false,
      before: "Wary.", after: "## Current state\nHurt.\n\n## Knows\nmap is fake" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After Seraphine — current state");
  expect((ta as HTMLTextAreaElement).value).toContain("## Knows");
  expect((ta as HTMLTextAreaElement).value).toContain("map is fake");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "character_state:seraphine", after: "## Current state\nHurt.\n\n## Knows\nmap is fake" })] })));
});

test("plot rows are editable and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "plot:the-map", kind: "plot",
      target: { kind: "plot", id: "the-map" }, label: "The map — advanced",
      field: "beat", before: "open — Elara got it.", after: "It is a forgery.",
      authored: false, payload: { id: "the-map", title: "The map", status: "advanced", scene: "s1" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After The map — advanced");
  expect((ta as HTMLTextAreaElement).value).toBe("It is a forgery.");
  fireEvent.change(ta, { target: { value: "It is a clever forgery." } });
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "plot:the-map", after: "It is a clever forgery.",
        payload: expect.objectContaining({ status: "advanced" }) })]) })));
});

test("new_character proposal renders editable card and provenance fields and saves them", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_character:old-bram", kind: "new_character",
      target: { kind: "characters", id: "" }, label: "New character — Old Bram",
      field: "description", before: "", after: "[character(\"Old Bram\") {}]", authored: false,
      payload: { name: "Old Bram", sd_prompt: "an old innkeeper",
        personality: "gruff but kind", mes_example: "<START>\n{{user}}: A room?\n{{char}}: Aye.",
        evidence: "Bram rented the party a room.", confidence: "thin",
        open_questions: "Why does he fear the pier?" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const nameInput = await screen.findByLabelText("Name New character — Old Bram");
  expect((nameInput as HTMLInputElement).value).toBe("Old Bram");
  const desc = await screen.findByLabelText("After New character — Old Bram");
  expect((desc as HTMLTextAreaElement).value).toBe("[character(\"Old Bram\") {}]");
  const personality = await screen.findByLabelText("Personality New character — Old Bram");
  expect((personality as HTMLTextAreaElement).value).toBe("gruff but kind");
  const dialogue = await screen.findByLabelText("Example dialogue New character — Old Bram");
  expect((dialogue as HTMLTextAreaElement).value).toBe("<START>\n{{user}}: A room?\n{{char}}: Aye.");
  const prompt = await screen.findByLabelText("Suggested image prompt New character — Old Bram");
  expect((prompt as HTMLInputElement).value).toBe("an old innkeeper");
  const evidence = await screen.findByLabelText(/Evidence New character.*Old Bram/);
  expect((evidence as HTMLTextAreaElement).value).toBe("Bram rented the party a room.");
  const confidence = await screen.findByLabelText(/Confidence New character.*Old Bram/);
  expect((confidence as HTMLSelectElement).value).toBe("thin");
  const questions = await screen.findByLabelText(/Open questions New character.*Old Bram/);
  expect((questions as HTMLTextAreaElement).value).toBe("Why does he fear the pier?");
  fireEvent.change(nameInput, { target: { value: "Old Man Bram" } });
  fireEvent.change(personality, { target: { value: "gruff, secretly gentle" } });
  fireEvent.change(prompt, { target: { value: "a grizzled innkeeper" } });
  fireEvent.change(evidence, { target: { value: "Bram warned the party away from the pier." } });
  fireEvent.change(confidence, { target: { value: "sketched" } });
  fireEvent.change(questions, { target: { value: "Who pays Bram for rumors?" } });
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "new_character:old-bram",
      payload: { name: "Old Man Bram", sd_prompt: "a grizzled innkeeper",
        personality: "gruff, secretly gentle",
        mes_example: "<START>\n{{user}}: A room?\n{{char}}: Aye.",
        evidence: "Bram warned the party away from the pier.",
        confidence: "sketched",
        open_questions: "Who pays Bram for rumors?" } })] })));
});

test("new_location shows the setting checkbox only when the scene has no location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const setting = await screen.findByLabelText("This is where the scene happened New location — The Crypt");
  fireEvent.click(setting);
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      payload: expect.objectContaining({ current_setting: true }) })] })));
});

test("new_location hides the setting checkbox when the scene already has a location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "Old Dock", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("After New location — The Crypt");
  expect(screen.queryByLabelText("This is where the scene happened New location — The Crypt")).toBeNull();
});

test("relationship rows are read-only and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "feeling:characters:a->characters:b", kind: "relationship",
      target: { kind: "relationships", id: "characters:a->characters:b" }, label: "Ann → Bo",
      field: "feeling", before: "trust 1, affection 1, tension 3", after: "trust 4, affection 3, tension 1",
      authored: false, payload: { from: "characters:a", to: "characters:b", trust: 4, affection: 3, tension: 1, note: "" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Ann → Bo");
  expect(screen.queryByLabelText("After Ann → Bo")).toBeNull();
  expect(screen.getByText(/trust 4, affection 3, tension 1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "feeling:characters:a->characters:b",
        payload: expect.objectContaining({ trust: 4 }) })]) })));
});

const SHEET_EDIT = { id: "sheet:characters:mara:hp", kind: "sheet",
  target: { kind: "characters", id: "mara" }, label: "Mara — HP", field: "hp",
  before: "hp 6/10", after: "hp 4/10", authored: false, payload: { note: "took a hit" } };

test("mechanics: warnings render with a ⚠ prefix; a clean run shows the hint instead", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: ["Mara claimed a hit with no roll"], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  const { unmount } = renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("⚠ Mara claimed a hit with no roll");
  expect(screen.queryByText("mechanics audited clean")).toBeNull();
  unmount();

  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("mechanics audited clean");
});

test("skipped mechanics renders no mechanics section", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText("mechanics audited clean")).toBeNull();
  expect(screen.queryByText(/⚠/)).toBeNull();
  expect(screen.queryByText(/Mechanics validation failed/)).toBeNull();
  expect(screen.queryByText(/could not be validated/)).toBeNull();
});

test("failed mechanics shows a notice with Retry validation; retry replaces sheet rows and clears the notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    edits: [SHEET_EDIT] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/Mechanics validation failed/)).toBeNull());
  expect(await screen.findByText("Mara — HP")).toBeInTheDocument();
  // the signal is how releasing the review reaches the server, so it is part
  // of the call, not incidental
  expect(api.retryAudit).toHaveBeenCalledWith("run", "s1", expect.any(AbortSignal));
});

test("a rejected retryAudit surfaces an error and leaves the mechanics notice/rows untouched", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  (api.retryAudit as any).mockRejectedValue({ detail: "audit retry blew up" });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await screen.findByText("audit retry blew up");
  // the failed-mechanics panel state is untouched by the rejection
  expect(screen.getByText("Mechanics validation failed: boom")).toBeInTheDocument();
  expect(screen.queryByText("Mara — HP")).toBeNull();
});

test("unapproved non-sheet rows survive Retry validation without duplicating", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  const LORE_EDIT = { id: "lore:old-dock", kind: "lore",
    target: { kind: "lore", id: "old-dock" }, label: "Old Dock — lore",
    field: "body", before: "quiet.", after: "quiet, but watched.", authored: false };
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [LORE_EDIT] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    edits: [SHEET_EDIT] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  expect(cardFor(/Old Dock/)).toHaveClass("approved");
  fireEvent.click(screen.getByLabelText(`Reject ${LORE_EDIT.label}`));
  expect(cardFor(/Old Dock/)).not.toHaveClass("approved");
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/Mechanics validation failed/)).toBeNull());
  showProposal(() => screen.queryByText("Mara — HP"));
  expect(screen.getByText("Mara — HP")).toBeInTheDocument();
  showProposal(() => screen.queryByLabelText(`Reject ${LORE_EDIT.label}`));
  expect(screen.getAllByLabelText(`Reject ${LORE_EDIT.label}`)).toHaveLength(1);
  expect(cardFor(/Old Dock/)).not.toHaveClass("approved");
});

test("degraded mechanics shows a notice listing dropped findings", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "degraded", reason: null, warnings: [],
      dropped: [{ id: "characters:mara", field: "athletics", reason: "static tamper" }] },
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Some mechanics findings could not be validated");
  expect(screen.getByText(/characters:mara athletics: static tamper/)).toBeInTheDocument();
});

const absorbWithDossiers = (dossiers: unknown) =>
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers,
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT, edits: [] });

const absorbWithVoice = (voice: unknown) =>
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT, voice, edits: [] });

test("failed dossier refreshes are listed per NPC instead of passing silently", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "degraded", reason: "some dossiers could not be prepared",
    proposed: ["mara"], failed: [{ id: "winifred", reason: "rate limited" }], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Some NPC dossiers could not be prepared");
  expect(screen.getByText(/winifred: rate limited/)).toBeInTheDocument();
});

test("dossiers the absorb budget skipped are named, not silently missing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "degraded",
    reason: "the absorb time budget ran out before the rest could be prepared",
    proposed: ["mara"], failed: [], skipped: ["winifred"] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/the absorb time budget ran out/);
  expect(screen.getByText(/skipped: winifred/)).toBeInTheDocument();
});

const DOSSIER_EDIT = { id: "dossier:winifred", kind: "dossier",
  target: { kind: "characters", id: "winifred" }, label: "Winifred — campaign dossier",
  field: "dossier", before: "Quiet.", after: "Quiet, and newly armed.", authored: false };

/** A dossier phase the clock cut short, in the two shapes the panel reads it
 *  from: the block itself and the phase row projected from it. */
const CUT_DOSSIERS = { status: "failed",
  reason: "the absorb time budget ran out before any dossier could be prepared",
  proposed: [], failed: [], skipped: ["winifred"],
  attempted: false, budget_exhausted: true };
/** `over` folds in another phase's block, for the tests that need a second
 *  retry on the same review to fail over the first. */
const absorbCutShortOnDossiers = (over: any = {}) =>
  absorbWithPhases(phasesFor({ dossiers: CUT_DOSSIERS, ...over }),
                   { dossiers: CUT_DOSSIERS, ...over });

test("a cut-short dossier phase offers Retry dossiers; it stages the rows and clears the notice",
     async () => {
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [DOSSIER_EDIT] });
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  await waitFor(() => expect(screen.queryByText(/No NPC dossier was prepared/)).toBeNull());
  expect(await screen.findByText("Winifred — campaign dossier")).toBeInTheDocument();
  expect(api.retryDossiers).toHaveBeenCalledWith("run", "s1", expect.any(AbortSignal));
});

test("a successful dossier retry clears the budget notice it was offered for", async () => {
  // The phase row is a projection of the block, so it has to move with it —
  // otherwise the panel keeps warning about a step this retry has since run.
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [] });
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));
  await waitFor(() => expect(screen.queryByText(/only partly absorbed/)).toBeNull());
});

test("a rejected retryDossiers surfaces an error and leaves the notice and rows untouched",
     async () => {
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockRejectedValue({ detail: "dossier retry blew up" });
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  await screen.findByText("dossier retry blew up");
  expect(screen.getByText(/No NPC dossier was prepared/)).toBeInTheDocument();
  expect(screen.queryByText("Winifred — campaign dossier")).toBeNull();
});

test("releasing a review aborts the retry request, not just its answer", async () => {
  // The generation guard stops a stale ANSWER from landing; it does nothing
  // about the WORK. The endpoint runs one LLM call per present NPC on a fresh
  // absorb budget, and `absorb_budget = 0` makes that unbounded — so a retry
  // nobody is waiting for goes on spending time and credits. Cancel is offered
  // as the way out of exactly that, so it has to reach the server.
  absorbCutShortOnDossiers();
  let signal: AbortSignal | undefined;
  (api.retryDossiers as any).mockImplementation((_c: string, _s: string, sig: AbortSignal) => {
    signal = sig;
    return new Promise(() => {});   // never resolves; only Cancel ends it
  });
  await openAbsorb();
  fireEvent.click(await screen.findByRole("button", { name: /Retry dossiers/ }));
  await waitFor(() => expect(signal).toBeDefined());
  expect(signal!.aborted).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Cancel absorb" }));

  await waitFor(() => expect(signal!.aborted).toBe(true));
});

/** Opens a review, fails its dossier retry, and leaves the banner on screen. */
const failedDossierRetry = async () => {
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockRejectedValue({ detail: "dossier retry blew up" });
  await openAbsorb();
  fireEvent.click(await screen.findByRole("button", { name: /Retry dossiers/ }));
  await screen.findByText("dossier retry blew up");
};

test("cancelling a review takes the scoped retry failure with it", async () => {
  // The banner reports on a review; once that review is gone it is reporting on
  // nothing, and its text ("the dossier retry failed") describes an operation
  // the reader can no longer see or repeat.
  await failedDossierRetry();

  fireEvent.click(screen.getByRole("button", { name: "Cancel absorb" }));

  await waitFor(() => expect(screen.queryByText("dossier retry blew up")).toBeNull());
});

test("saving a review takes the scoped retry failure with it", async () => {
  // Same fact from the other exit: a save that lands closes the review, so the
  // failure of one of its steps must not outlive it either.
  await failedDossierRetry();

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));

  await waitFor(() => expect(screen.queryByText("dossier retry blew up")).toBeNull());
});

test("an absorb that lands after a campaign switch is not installed", async () => {
  // The `[cid]` effect clears the review state it can see, but an absorb ALREADY
  // in flight is not state — and it is the slowest request in the app, several
  // LLM calls, so there is ample room to leave. Installing it would put A's
  // summary, timeline and staged edits in front of B, and Save would post them
  // to B: scene ids repeat across campaigns and a fresh commit token matches, so
  // nothing further down refuses them.
  let land: (v: any) => void = () => {};
  (api.absorbScene as any).mockImplementation(() => new Promise((r) => { land = r; }));
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "End scene" }));
  await waitFor(() => expect(api.absorbScene).toHaveBeenCalled());

  // Wait for B to be the campaign on screen BEFORE A's absorb lands. Resolving
  // it while the switch is still in React's queue lets B's own `[cid]` effect
  // clear the install a moment later, so the test passes with or without the
  // guard — an earlier draft did exactly that and proved nothing.
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(api.getCampaign).toHaveBeenCalledWith("other"));

  // `act` so the continuation actually RUNS before the assertions. Resolving
  // bare leaves it queued as a microtask, and asserting "the panel is absent"
  // against a continuation that has not run yet is a test that passes for the
  // wrong reason — the second way an earlier draft of this test proved nothing.
  await act(async () => {
    land(reviewResult({
      one_line: "A's one-liner", summary: "A's summary", keywords: [],
      timeline_events: [], edits: [], commit_token: "t-a",
    }, "gen-a"));
  });

  // B must not be showing a review it never asked for. Asserted on the panel
  // itself rather than on the summary text: the summary lands in a textarea's
  // *value*, which `queryByText` cannot see — an earlier draft of this test
  // passed with the guard removed for exactly that reason.
  expect(screen.queryByText("Review scene summary")).toBeNull();
  expect(screen.queryByRole("button", { name: /Save chronicle/ })).toBeNull();
});

test("Cancel absorb stops a pending retry, and End scene is not there to race it", async () => {
  // End scene used to sit beside the open review, one mis-click from discarding
  // every proposal already judged and starting a second expensive pipeline over
  // the same scene. The review replaces the scene now (4c), so that bar is gone
  // and Cancel is the way out — which still has to stop the retry it leaves
  // behind, for the reason End scene did: a wedged retry on an unbounded budget
  // is exactly when the reader needs out.
  absorbCutShortOnDossiers();
  let signal: AbortSignal | undefined;
  (api.retryDossiers as any).mockImplementation((_c: string, _s: string, sig: AbortSignal) => {
    signal = sig;
    return new Promise(() => {});
  });
  await openAbsorb();
  fireEvent.click(await screen.findByRole("button", { name: /Retry dossiers/ }));
  await waitFor(() => expect(signal).toBeDefined());
  (api.absorbScene as any).mockClear();

  expect(screen.queryByRole("button", { name: "End scene" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Cancel absorb/ }));

  expect(signal!.aborted).toBe(true);
  // And nothing was absorbed on the way out — Cancel discards, it does not
  // re-run the pipeline the way End scene would have.
  expect(api.absorbScene).not.toHaveBeenCalled();
});

test("leaving the campaign section aborts a retry that is still running", async () => {
  // Unmount, not a `cid` change: the `[cid]` effect does not re-run, so its
  // `releaseRetries` never fires. SPA navigation does not cancel a fetch either,
  // so without a cleanup the request outlives the screen — and with it the
  // server-side work, which only stops when it sees the disconnect.
  absorbCutShortOnDossiers();
  let signal: AbortSignal | undefined;
  (api.retryDossiers as any).mockImplementation((_c: string, _s: string, sig: AbortSignal) => {
    signal = sig;
    return new Promise(() => {});
  });
  const view = await openAbsorb();
  fireEvent.click(await screen.findByRole("button", { name: /Retry dossiers/ }));
  await waitFor(() => expect(signal).toBeDefined());
  expect(signal!.aborted).toBe(false);

  view.unmount();

  expect(signal!.aborted).toBe(true);
});

test("a scoped retry failure does not follow the reader into another campaign", async () => {
  // The route has no `key`, so React Router reuses this component for A -> B.
  // The banner is not campaign-scoped state on its own, so the cid effect's
  // `releaseRetries` is the only thing that keeps A's failure out of B.
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockRejectedValue({ detail: "dossier retry blew up" });
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  fireEvent.click(await screen.findByRole("button", { name: /Retry dossiers/ }));
  await screen.findByText("dossier retry blew up");

  fireEvent.click(screen.getByText("switch campaign"));

  await waitFor(() => expect(screen.queryByText("dossier retry blew up")).toBeNull());
});

test("cancelling a review leaves an unrelated banner standing", async () => {
  // The other half of the scoping: the banner is shared, and a failure with no
  // `from` belongs to whatever raised it -- here a rename whose relist failed,
  // raised while the review happened to be open. Closing the review must not
  // take that report down with it, nor the Retry the reader still needs.
  await openAbsorb();
  (api.listScenes as any).mockRejectedValue(new Error("relist failed"));
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await screen.findByText(/could not be refreshed/);

  fireEvent.click(screen.getByRole("button", { name: "Cancel absorb" }));

  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());
  expect(screen.getByText(/could not be refreshed/)).toBeInTheDocument();
});

test("non-dossier rows survive Retry dossiers with their approval intact", async () => {
  // The whole point of a scoped retry: what the reviewer has already decided
  // about the rest of the batch is not collateral damage.
  const LORE_EDIT = { id: "lore:old-dock", kind: "lore",
    target: { kind: "lore", id: "old-dock" }, label: "Old Dock — lore",
    field: "body", before: "quiet.", after: "quiet, but watched.", authored: false };
  absorbWithPhases(phasesFor({ dossiers: CUT_DOSSIERS }),
                   { dossiers: CUT_DOSSIERS, edits: [LORE_EDIT] });
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [DOSSIER_EDIT] });
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByLabelText(`Reject ${LORE_EDIT.label}`));   // the reviewer says no
  expect(cardFor(/Old Dock/)).not.toHaveClass("approved");

  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));
  // The retry stages the dossier row asynchronously; wait for the column to
  // grow its drawer before hunting through the drawers for it.
  await waitFor(() => {
    showProposal(() => screen.queryByText("Winifred — campaign dossier"));
    expect(screen.getByText("Winifred — campaign dossier")).toBeInTheDocument();
  });
  showProposal(() => screen.queryByLabelText(`Reject ${LORE_EDIT.label}`));
  const lore = screen.getAllByLabelText(`Reject ${LORE_EDIT.label}`);
  expect(lore).toHaveLength(1);                    // not duplicated by the rebuild
  expect(cardFor(/Old Dock/)).not.toHaveClass("approved");
});

test("a retry that fails for an NPC keeps that NPC's proposal from the first pass", async () => {
  // Codex P2. The backend reports per-NPC failures inside a 200, so an
  // unconditional rebuild turns "retry the one we missed" into a net loss:
  // mara's good proposal is deleted and nothing replaces it.
  const MARA_DOSSIER = { id: "dossier:mara", kind: "dossier",
    target: { kind: "characters", id: "mara" }, label: "Mara — campaign dossier",
    field: "dossier", before: "Steady.", after: "Steady, and owed a favour.", authored: false };
  const partial = { status: "degraded",
    reason: "the absorb time budget ran out before the rest could be prepared",
    proposed: ["mara"], failed: [], skipped: ["winifred"],
    attempted: true, budget_exhausted: true };
  absorbWithPhases(phasesFor({ dossiers: partial }),
                   { dossiers: partial, edits: [MARA_DOSSIER] });
  // winifred now succeeds; mara, re-run alongside her, fails this time
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "degraded", reason: "some dossiers could not be prepared",
                proposed: ["winifred"], failed: [{ id: "mara", reason: "rate limited" }],
                skipped: [], attempted: true, budget_exhausted: false },
    edits: [DOSSIER_EDIT] });
  await openAbsorb();

  await screen.findByText("Mara — campaign dossier");
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  expect(await screen.findByText("Winifred — campaign dossier")).toBeInTheDocument();
  // mara was not re-proposed, so her first-pass row stands rather than vanishing
  showProposal(() => screen.queryByText("Mara — campaign dossier"));
  expect(screen.getByText("Mara — campaign dossier")).toBeInTheDocument();
});

test("an NPC the retry did repropose is replaced, not duplicated", async () => {
  // The other half of the rule: `proposed` names who this run answered for, and
  // for them the fresh proposal wins outright.
  const STALE = { id: "dossier:winifred", kind: "dossier",
    target: { kind: "characters", id: "winifred" }, label: "Winifred — campaign dossier",
    field: "dossier", before: "Quiet.", after: "A first, worse draft.", authored: false };
  const failed = { status: "failed", reason: "no dossier could be prepared",
    proposed: [], failed: [{ id: "winifred", reason: "rate limited" }], skipped: [],
    attempted: true, budget_exhausted: false };
  absorbWithPhases(phasesFor({ dossiers: failed }), { dossiers: failed, edits: [STALE] });
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [DOSSIER_EDIT] });
  await openAbsorb();

  await screen.findByText("A first, worse draft.");
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  expect(await screen.findByText("Quiet, and newly armed.")).toBeInTheDocument();
  expect(screen.queryByText("A first, worse draft.")).toBeNull();
  expect(screen.getAllByLabelText("Approve Winifred — campaign dossier")).toHaveLength(1);
});

test("a dossier retry that lands after its review is gone leaves the new review alone",
     async () => {
  // Codex P1. A scoped retry gets its own budget, so it can still be in flight
  // when the reviewer discards and absorbs another scene. Applying it then
  // stages scene A's dossiers into scene B's review — and B's save commits them.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbCutShortOnDossiers();
  let land: (v: unknown) => void = () => {};
  (api.retryDossiers as any).mockReturnValue(new Promise((resolve) => { land = resolve; }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  // …the reviewer gives up on this one (Cancel clears the review) and absorbs
  // the next scene instead
  fireEvent.click(screen.getByRole("button", { name: /^Cancel absorb$/ }));
  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());
  await openScene(/Two/);
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  absorbs({
    one_line: "second", summary: "s", keywords: [], timeline_events: [], cast: [],
    location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    dossiers: { status: "ok", reason: null, proposed: [], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [],
             failed: [], skipped: [], attempted: false, budget_exhausted: false },
    commit_token: "tok-second", phases: PHASES_NONE_CUT, edits: [] });
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByDisplayValue("second");

  land({ dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [],
                     skipped: [], attempted: true, budget_exhausted: false },
         edits: [DOSSIER_EDIT] });

  // scene A's dossier never reaches scene B's review, and B's clean phase report
  // is not overwritten by A's
  await waitFor(() => expect(screen.getByDisplayValue("second")).toBeInTheDocument());
  expect(screen.queryByText("Winifred — campaign dossier")).toBeNull();
  expect(screen.queryByText(/No NPC dossier was prepared/)).toBeNull();
});

test("a second click cannot start an overlapping dossier retry", async () => {
  // Codex P2 (round two): two retries of the SAME review carry the same
  // `commit_token`, so the stale-review guard passes for both and a first
  // request answering second overwrites the fresher generation on screen. The
  // latch is what stops the pair ever existing — and it doubles as the feedback
  // a call that can run for the whole absorb budget otherwise never gives.
  absorbCutShortOnDossiers();
  let land: (v: unknown) => void = () => {};
  (api.retryDossiers as any).mockReturnValue(new Promise((resolve) => { land = resolve; }));
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  const pending = await screen.findByRole("button", { name: /Retrying…/ });
  expect(pending).toBeDisabled();
  fireEvent.click(pending);                       // the impatient second click
  expect(api.retryDossiers).toHaveBeenCalledTimes(1);

  land({ dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [],
                     skipped: [], attempted: true, budget_exhausted: false },
         edits: [DOSSIER_EDIT] });
  // the latch releases, so a genuinely later retry is still possible
  await waitFor(() => expect(screen.queryByText("Retrying…")).toBeNull());
});

test("Retry validation latches the same way", async () => {
  // Same exposure, same fix — the two retries are kept symmetric so neither
  // grows a guard the other lacks.
  const over = {
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockReturnValue(new Promise(() => {}));  // never lands
  await openAbsorb();

  await screen.findByText(/Mechanics validation failed/);
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));

  const pending = await screen.findByRole("button", { name: /Retrying…/ });
  expect(pending).toBeDisabled();
  fireEvent.click(pending);
  expect(api.retryAudit).toHaveBeenCalledTimes(1);
});

test("a dossier retry that succeeds clears the previous attempt's error", async () => {
  // Codex, round five. The failure banner is global and nothing else clears it
  // here, so a recovery would read as a second failure: the notice goes away
  // while the page still reports the retry that went wrong.
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockRejectedValueOnce({ detail: "dossier retry blew up" });
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));
  await screen.findByText("dossier retry blew up");

  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: ["winifred"], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [DOSSIER_EDIT] });
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  expect(await screen.findByText("Winifred — campaign dossier")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText("dossier retry blew up")).toBeNull());
});

test("an abandoned dossier retry that rejects does not drop a banner on what replaced it",
     async () => {
  // Codex, round six. Cancel stays enabled during a retry by design, so the
  // request outlives its review — and the catch published the failure anyway.
  absorbCutShortOnDossiers();
  let reject: (e: unknown) => void = () => {};
  (api.retryDossiers as any).mockReturnValue(new Promise((_r, rj) => { reject = rj; }));
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Cancel absorb$/ }));
  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());

  // flushed inside act, so the rejection is fully handled before the assertion
  // — asserting on a promise that has not settled yet would pass either way
  await act(async () => { reject({ detail: "dossier retry blew up" }); });

  expect(screen.queryByText("dossier retry blew up")).toBeNull();
});

test("starting a dossier retry leaves another retry's error banner alone", async () => {
  // The banner is global; the failures it carries are not interchangeable, which
  // is what `from` tags them for. One retry clearing the banner unconditionally
  // would erase the OTHER retry's failure and leave the reviewer believing that
  // phase came back clean.
  //
  // Raised off the audit retry rather than off the composer: the composer
  // belongs to the scene, and the review replaces the scene now (4c), so a chat
  // error can no longer be raised from underneath an open review at all. Two
  // review-scoped retries failing over each other is the same invariant and is
  // the shape it actually takes on this screen.
  absorbCutShortOnDossiers({
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
  });
  (api.retryAudit as any).mockRejectedValue({ detail: "the audit fell over" });
  (api.retryDossiers as any).mockReturnValue(new Promise(() => {}));
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await screen.findByText("the audit fell over");

  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  // still there, and still offering the recovery that belongs to it
  await waitFor(() => expect(screen.getByText("the audit fell over")).toBeInTheDocument());
});

test("switching campaigns discards the open review rather than repointing it", async () => {
  // Codex P1. The route carries no `key`, so React Router reuses this component
  // for campaign A -> B (browser Back between two campaigns does it); without
  // this the review, its scene id and every request they drive — the retries
  // and the SAVE — would follow `cid` to B, and scene ids repeat across
  // campaigns so those requests succeed rather than 404.
  //
  // Navigated from inside the router on purpose: re-rendering a fresh
  // MemoryRouter would REMOUNT CampaignView, which clears the review for a
  // reason that has nothing to do with the fix and would pass either way.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbCutShortOnDossiers();
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">to the other campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("link", { name: /to the other campaign/ }));

  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());
});

test("a failed End scene does not offer the banner's generate-a-reply Retry", async () => {
  // The same defect as the scoped retries', on the operation that opens the
  // review rather than one inside it: answering "the absorb failed" with a
  // button that writes one more reply into the scene the user was finishing.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockRejectedValue({ detail: "absorb blew up" });
  renderCampaign();
  await screen.findByText("hi");

  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  await screen.findByText("absorb blew up");
  expect(screen.queryByRole("button", { name: /^Retry$/ })).toBeNull();
  // End scene is the recovery, and it is usable again
  expect(screen.getByRole("button", { name: /End scene/ })).toBeEnabled();
});

test("cancelling a review frees the next review's Retry dossiers button", async () => {
  // Codex, round three. The latch is component-wide, so an abandoned retry that
  // never answers — `absorb_budget = 0` makes it unbounded — would keep the NEXT
  // review's button disabled for as long as it hung.
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockReturnValue(new Promise(() => {}));  // never lands
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));
  await screen.findByRole("button", { name: /Retrying…/ });

  fireEvent.click(screen.getByRole("button", { name: /^Cancel absorb$/ }));
  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  // the new review's button is live immediately, not waiting on the dead request
  const button = await screen.findByRole("button", { name: /Retry dossiers/ });
  expect(button).toBeEnabled();
});

test("a save latches the scoped retries, and a retry latches the save", async () => {
  // `saveAbsorb` resolves the server's conflict indices against `editRows` as
  // the array the batch was built from, which only holds while nothing else
  // rewrites the rows mid-flight. A clean save is just as bad: it would commit
  // the pre-retry batch and then clear the rows the retry had just staged.
  absorbCutShortOnDossiers();
  let landSave: (v: unknown) => void = () => {};
  (api.saveChronicle as any).mockReturnValue(new Promise((resolve) => { landSave = resolve; }));
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Retry dossiers/ })).toBeDisabled());

  landSave({ failures: [] });
  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());
});

test("a pending dossier retry latches Save summary", async () => {
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockReturnValue(new Promise(() => {}));
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Save chronicle/ })).toBeDisabled());
  // …but Cancel stays live: the retry runs on the absorb budget, which is
  // unbounded at 0, so this is the only way out of a request that never answers
  expect(screen.getByRole("button", { name: /^Cancel absorb$/ })).toBeEnabled();
});

test("a failed dossier retry does not offer the banner's generate-a-reply Retry", async () => {
  // That button runs the CHAT retry: it would extend the very scene whose
  // end-of-scene review is open, and not re-run the dossiers at all.
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockRejectedValue({ detail: "dossier retry blew up" });
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared/);
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  await screen.findByText("dossier retry blew up");
  expect(screen.queryByRole("button", { name: /^Retry$/ })).toBeNull();
  // the scoped button is the recovery, and it is usable again
  expect(screen.getByRole("button", { name: /Retry dossiers/ })).toBeEnabled();
});

test("Retry dossiers targets the review's scene, not whichever is on screen", async () => {
  // A review outlives a scene switch, so reading the rail would build dossiers
  // from the scene the user has since opened — the bug #282 fixed for the audit
  // retry, which this one must not reintroduce.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbCutShortOnDossiers();
  (api.retryDossiers as any).mockResolvedValue({
    dossiers: { status: "ok", reason: null, proposed: [], failed: [], skipped: [],
                attempted: true, budget_exhausted: false },
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/No NPC dossier was prepared/);

  await openScene(/Two/);                        // switch scenes
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  fireEvent.click(screen.getByRole("button", { name: /Retry dossiers/ }));

  await waitFor(() => expect(api.retryDossiers).toHaveBeenCalled());
  expect((api.retryDossiers as any).mock.calls[0][1]).toBe("s1");
});

test("a partly-prepared dossier phase does not call itself failed", async () => {
  // mara's dossier was prepared; only winifred's was dropped. Calling that
  // "refresh failed" contradicts the edit sitting in the list beside it.
  absorbWithPhases(
    phasesFor({ dossiers: { status: "degraded",
                            reason: "the absorb time budget ran out before the rest could be prepared",
                            attempted: true, budget_exhausted: true } }),
    { dossiers: { status: "degraded",
                  reason: "the absorb time budget ran out before the rest could be prepared",
                  proposed: ["mara"], failed: [], skipped: ["winifred"],
                  attempted: true, budget_exhausted: true } });
  await openAbsorb();

  await screen.findByText(/Some NPC dossiers were not prepared: the absorb time budget ran out/);
  expect(screen.queryByText(/dossier refresh failed/)).toBeNull();
});

test("every NPC failing reads as total failure, not partial", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "failed", reason: "no dossier could be prepared",
    proposed: [], failed: [{ id: "winifred", reason: "LLMError: rate limited" }], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("No NPC dossier could be prepared");
  expect(screen.queryByText(/Some NPC dossiers/)).toBeNull();
  expect(screen.getByText(/winifred: LLMError: rate limited/)).toBeInTheDocument();
});

test("a whole-phase dossier failure shows its reason", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "failed", reason: "could not read the scene cast: boom",
    proposed: [], failed: [], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("NPC dossier refresh failed: could not read the scene cast: boom");
});

test("clean and skipped dossier phases render no notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "ok", reason: null, proposed: ["mara"], failed: [], skipped: [] });
  const { unmount } = renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText(/dossier/i)).toBeNull();
  unmount();

  absorbWithDossiers({ status: "skipped", reason: "no npcs present", proposed: [], failed: [], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText(/dossier/i)).toBeNull();
});

const absorbWithPhases = (phases: unknown, over: Record<string, unknown> = {}) =>
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [],
                 attempted: false, budget_exhausted: false },
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [],
                attempted: false, budget_exhausted: false },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [],
             failed: [], skipped: [], attempted: false, budget_exhausted: false },
    commit_token: "tok", phases, edits: [], ...over });

const openAbsorb = async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  const view = renderCampaign();   // returned for the unmount tests; others ignore it
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  return view;
};

test("an absorb the budget cut short names the steps that never ran", async () => {
  // The reported failure mode: extraction eats the clock, so the review panel
  // shows fewer proposed changes and nothing says why.
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
    { name: "audit", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
  ]);
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  expect(screen.getByText(/NPC dossiers, mechanics audit/)).toBeInTheDocument();
});

test("a phase that ran and failed on its own merits is not blamed on the clock", async () => {
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "audit", status: "failed", reason: "audit failed: boom",
      attempted: true, budget_exhausted: false },
  ], { mechanics: { status: "failed", reason: "audit failed: boom", warnings: [], dropped: [],
                    attempted: true, budget_exhausted: false } });
  await openAbsorb();

  await screen.findByText("Mechanics validation failed: audit failed: boom");
  expect(screen.queryByText(/only partly absorbed/)).toBeNull();
});

/** Phase rows that agree with the blocks, the way the backend's projection
 *  guarantees — a row claiming the clock while its block claims otherwise is a
 *  state the API cannot produce, so no test should assert against it. */
const phasesFor = (over: Record<string, any>) =>
  [{ name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
   { name: "dossiers", ...(over.dossiers ?? { status: "ok", reason: null, attempted: true, budget_exhausted: false }) },
   { name: "audit", ...(over.mechanics ?? { status: "ok", reason: null, attempted: true, budget_exhausted: false }) }]
    .map(({ name, status, reason, attempted, budget_exhausted }) =>
      ({ name, status, reason, attempted, budget_exhausted }));

test("a budget-cut audit reads as never run, and still offers the retry", async () => {
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  await openAbsorb();

  await screen.findByText(/Mechanics validation never ran: the absorb time budget ran out/);
  expect(screen.queryByText(/Mechanics validation failed/)).toBeNull();
  expect(screen.getByRole("button", { name: /Retry validation/ })).toBeInTheDocument();
});

test("a successful audit retry clears the budget notice it was offered for", async () => {
  // Retry replaces `mechanics`; the phase row it was projected from has to
  // move with it, or the panel keeps warning about a step that has since run.
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/only partly absorbed/)).toBeNull());
});

test("Retry validation audits the review's scene, not whichever is on screen", async () => {
  // A review outlives a scene switch (only Discard and a successful save clear
  // it), so the retry has to follow `absorbSid` the way `saveAbsorb` already
  // does — otherwise it audits the scene the user has since opened and writes
  // that verdict, its sheet edits and its phase row into the other scene's
  // review.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  await openScene(/Two/);                        // switch scenes
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));

  await waitFor(() => expect(api.retryAudit).toHaveBeenCalled());
  expect((api.retryAudit as any).mock.calls[0][1]).toBe("s1");
});

test("renaming the reviewed scene moves the review's id with it", async () => {
  // A scene's id is derived from its title, so a rename mints a new one. The
  // open review still points at the old id — and both the retry and the save
  // would POST a scene that no longer exists. `renameScene` already migrates
  // `seedPrompt.sid` for this reason; the review id belongs in that list.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  const over = {
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(api.retryAudit).toHaveBeenCalled());
  expect((api.retryAudit as any).mock.calls[0][1]).toBe("s1-renamed");
});

test("renaming the reviewed scene repoints its staged plot edits too", async () => {
  // `payload.scene` is embedded by absorb.materialize and handed straight to
  // plot.set_movement on save. It lives only in this browser, so the server's
  // scene_refs.repoint pass cannot reach it — a rename that moved only
  // `absorbSid` would save beats pointing at a scene id that no longer exists.
  const PLOT_EDIT = {
    id: "plot:the-siege", kind: "plot", target: { kind: "plot", id: "the-siege" },
    label: "The Siege", field: "status", before: "open", after: "escalating",
    authored: false, payload: { id: "the-siege", title: "The Siege", status: "escalating", scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [PLOT_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s",
    keywords: [], cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const saved = (api.saveChronicle as any).mock.calls[0][2];
  expect(saved.edits[0].payload.scene).toBe("s1-renamed");
});

test("renaming the reviewed scene repoints a commitment row's conflict basis", async () => {
  // `conflicts.commitment_line` ends `[N beats, last moved in <scene>]`, and the
  // server's scene_refs.repoint rewrites that id in the stored record. A staged
  // row left holding the old id no longer matches what the store says, so the
  // save reports a conflict on a commitment nobody touched.
  const COMMITMENT_EDIT = {
    id: "commitment:the-debt", kind: "commitment",
    target: { kind: "commitments", id: "the-debt" },
    label: "Repay Winifred — promise, open", field: "beat",
    before: "promise, open — She swore it. [1 beat, last moved in s1]",
    after: "She missed a payment.", authored: false,
    resolve_from: "promise, open — She swore it. [1 beat, last moved in s1]",
    payload: { id: "the-debt", title: "Repay Winifred", kind: "", status: "",
               due: null, scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [COMMITMENT_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s",
    keywords: [], cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const saved = (api.saveChronicle as any).mock.calls[0][2].edits[0];
  expect(saved.payload.scene).toBe("s1-renamed");
  expect(saved.before).toBe("promise, open — She swore it. [1 beat, last moved in s1-renamed]");
  expect(saved.resolve_from).toBe(
    "promise, open — She swore it. [1 beat, last moved in s1-renamed]");
});

test("renaming the reviewed scene repoints an UNANSWERED conflict snapshot", async () => {
  // The conflict the server returned carries the same fingerprint, and it is the
  // value Replace copies into `resolve_from`. The server's own repoint has
  // already moved the stored record onto the new id, so a stale snapshot here
  // means the retry is refused as changed again — the reviewer answering a
  // conflict that no longer exists, twice. It is also what the panel shows them.
  const STALE = "promise, open — She swore it. [1 beat, last moved in s1]";
  const COMMITMENT_EDIT = {
    id: "commitment:the-debt", kind: "commitment",
    target: { kind: "commitments", id: "the-debt" },
    label: "Repay Winifred — promise, open", field: "beat",
    before: STALE, after: "She missed a payment.", authored: false,
    payload: { id: "the-debt", title: "Repay Winifred", kind: "", status: "",
               due: null, scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [COMMITMENT_EDIT] });
  // First save comes back as a conflict, so the review sits holding one.
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  (api.saveChronicle as any)
    .mockRejectedValueOnce(new ApiError(
      409, "some proposed changes no longer match what is stored", "edit_conflicts",
      { conflicts: [{ id: "commitment:the-debt", label: "Repay Winifred — promise, open",
                      kind: "commitment", field: "beat", before: STALE,
                      after: "She missed a payment.", stored: STALE,
                      reason: "this commitment changed since the scene was absorbed",
                      mergeable: false, merged: "", index: 0 }] }))
    .mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s", keywords: [],
      cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await screen.findByText(/no longer match(es)? what is stored/i);

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  // What the reviewer is shown moved with the rename — the conflict's `stored`
  // panel and the row's own `before` both carry the fingerprint, so both move.
  const moved = "promise, open — She swore it. [1 beat, last moved in s1-renamed]";
  await waitFor(() => expect(screen.getAllByText(moved).length).toBeGreaterThan(0));

  // ...and so does what Replace sends as the value they answered over.
  fireEvent.click(screen.getByRole("button", { name: /Replace stored/i }));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect((api.saveChronicle as any).mock.calls.length).toBe(2));
  const saved = (api.saveChronicle as any).mock.calls[1][2].edits[0];
  expect(saved.resolve_from).toBe(moved);
});

test("the budget notice never sends the reviewer back through End scene", async () => {
  // End scene posts the *active* scene and replaces the review wholesale, so
  // advising it here would tell the user to discard the edits this very notice
  // has just told them are complete.
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
    { name: "audit", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  ]);
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  // Still the advice for a step with no scoped Retry of its own (the voice
  // check) — now mid-sentence, since #286 gave the dossier phase one.
  expect(screen.getByText(/raise the absorb budget/i)).toBeInTheDocument();
  expect(screen.queryByText(/end the scene again/i)).toBeNull();
});

test("a budget-cut dossier phase reads as never prepared, not as a failure", async () => {
  const over = {
    dossiers: { status: "failed",
                reason: "the absorb time budget ran out before any dossier could be prepared",
                proposed: [], failed: [], skipped: ["winifred"],
                attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared: the absorb time budget ran out/);
  expect(screen.queryByText("No NPC dossier could be prepared")).toBeNull();
  expect(screen.getByText(/skipped: winifred/)).toBeInTheDocument();
});

test("sheet edits render read-only with the note and survive save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t",
    applied: ["sheet:characters:mara:hp"], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mara — HP");
  expect(screen.getByText("hp 6/10")).toBeInTheDocument();
  expect(screen.getByText("hp 4/10")).toBeInTheDocument();
  expect(screen.getByText("took a hit")).toBeInTheDocument();
  expect(screen.queryByLabelText("After Mara — HP")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  expect(screen.queryByText(/did not apply/)).toBeNull();
});

test("failures from save render a notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t", applied: [],
    failures: [{ id: "sheet:characters:mara:hp", reason: "changed", kind: "conflict" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mara — HP");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await screen.findByText("1 change did not apply");
  expect(screen.getByText(/Mara — HP/)).toBeInTheDocument();
  expect(screen.getByText("Mara — HP: changed (conflict)")).toBeInTheDocument();

  // A stale failures notice must not survive into the next scene's
  // absorb panel -- opening a new one (End scene) clears it immediately.
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(screen.queryByText(/did not apply/)).toBeNull());
});

test("a voice_drift row is approvable and sent on save (#59)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "ok", reason: null, checked: ["seraphine"], flagged: ["seraphine"],
             unjudged: [], failed: [], skipped: [] },
    edits: [{ id: "voice_drift:seraphine", kind: "voice_drift",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — voice drift",
      field: "voice_drift", authored: false,
      before: "", after: "She used contractions; Seraphine never does." }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After Seraphine — voice drift");
  expect((ta as HTMLTextAreaElement).value).toContain("never does");
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({ id: "voice_drift:seraphine" })] })));
});

test("a failed voice check is reported, never silently swallowed (#59)", async () => {
  // Silence would read as "everyone stayed in voice", which is the one thing a
  // failed check does NOT establish.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithVoice({ status: "failed", reason: "no voice check could be run", checked: [],
    flagged: [], unjudged: [],
    failed: [{ id: "seraphine", reason: "unreadable verdict from the voice judge" }],
    skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  expect(await screen.findByText("No voice check could be run")).toBeTruthy();
  expect(screen.getByText(/seraphine: unreadable verdict from the voice judge/)).toBeTruthy();
});

test("voice checks the absorb budget never reached are named (#59)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithVoice({ status: "degraded",
    reason: "the absorb time budget ran out before the rest could be checked",
    checked: ["mara"], flagged: [], unjudged: [], failed: [], skipped: ["winifred"] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  expect(await screen.findByText("Some voice checks could not be run")).toBeTruthy();
  expect(screen.getByText(/Never attempted, skipped: winifred/)).toBeTruthy();
});

test("clean and skipped voice phases render no notice (#59)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  for (const voice of [
    { status: "ok", reason: null, checked: ["mara"], flagged: ["mara"], unjudged: [],
      failed: [], skipped: [] },
    { status: "ok", reason: null, checked: ["mara"], flagged: [], unjudged: ["mara"],
      failed: [], skipped: [] },
    { status: "skipped", reason: "no anchored npcs present", checked: [], flagged: [],
      unjudged: [], failed: [], skipped: [] },
  ]) {
    absorbWithVoice(voice);
    const view = renderCampaign();
    await screen.findByText("hi");
    fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
    await screen.findByRole("button", { name: /Save chronicle/ });
    expect(screen.queryByText(/voice check/i)).toBeNull();
    view.unmount();
  }
});

test("renaming the reviewed scene repoints its staged commitment edits too", async () => {
  // Same browser-only `payload.scene` as the plot case above: apply_edits hands
  // it straight to commitments.set_movement, so a rename that moved only
  // `absorbSid` would append a beat pointing at a scene id that is gone (#115).
  const COMMITMENT_EDIT = {
    id: "commitment:the-debt", kind: "commitment",
    target: { kind: "commitments", id: "the-debt" },
    label: "Repay Winifred — promise, open", field: "beat",
    before: "", after: "She swore it aloud.", authored: false,
    payload: { id: "the-debt", title: "Repay Winifred", kind: "promise",
               status: "open", due: "before the thaw", scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [COMMITMENT_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s",
    keywords: [], cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const saved = (api.saveChronicle as any).mock.calls[0][2];
  expect(saved.edits[0].payload.scene).toBe("s1-renamed");
});

test("renaming the reviewed scene repoints its staged fact edits too", async () => {
  // The third `payload.scene` kind (#114): apply_edits hands it to facts.record,
  // so a rename that moved only `absorbSid` would file the fact under a scene id
  // that is gone. Nothing else on the row needs repointing — a fact's staged
  // `before` is a `conflicts.fact_line`, which carries no scene id at all.
  const FACT_EDIT = {
    id: "fact:f1", kind: "fact", target: { kind: "facts", id: "f1" },
    label: "Fact superseded", field: "text",
    before: "active — The bridge stands.", after: "The bridge is rubble.",
    authored: false,
    payload: { text: "The bridge is rubble.", date: "", supersedes: "f1", scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [FACT_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s",
    keywords: [], cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const saved = (api.saveChronicle as any).mock.calls[0][2];
  expect(saved.edits[0].payload.scene).toBe("s1-renamed");
  expect(saved.edits[0].before).toBe("active — The bridge stands.");   // untouched
});

/** A review whose three rows land in the three bands. */
const ROUTED_REVIEW = {
  ...LORE_REVIEW,
  edits: [
    { id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" },
      label: "Seraphine — current state", field: "current_state",
      before: "Wary.", after: "Bleeding.", authored: false,
      review: { certainty: 0.95, quote: "She pressed a hand to her side.",
                speaker: "Grimoire", authority: "narration", score: 0.95,
                band: "high" } },
    { id: "lore:the-pact", kind: "lore", target: { kind: "lore", id: "the-pact" },
      label: "The Pact — lore", field: "body", authored: false,
      before: "Signed at dusk.", after: "Signed at dusk.\n\nBroken by morning.",
      review: { certainty: 0.6, quote: "They broke it by morning.",
                speaker: "Mara", authority: "other", score: 0.3, band: "medium" } },
    { id: "plot:the-forged-map", kind: "plot", target: { kind: "plot", id: "the-forged-map" },
      label: "The forged map — open", field: "beat", authored: false,
      before: "", after: "Somebody forged it.",
      payload: { id: "the-forged-map", title: "The forged map", status: "open", scene: "s1" },
      review: { certainty: 0.9, quote: "I drew it myself.", speaker: "The Harbourmaster",
                authority: "unattributed", score: 0.27, band: "low" } },
  ],
};

async function openRoutedReview(review: any = ROUTED_REVIEW) {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbs(review);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/still to judge/i);
}

test("a low-confidence proposal is filed apart and starts unapproved", async () => {
  await openRoutedReview();
  // Out of the store's drawer, but counted out loud in the column — a withheld
  // approval the reviewer cannot see is a silent drop, which is the failure
  // this must not become. The count in the column is what says so now; it used
  // to be a collapsed "Show 1 low-confidence change" section nested inside
  // another drawer, which said it less plainly.
  const column = reviewColumn();
  expect(column.getByRole("button", { name: /low confidence/i })).toHaveTextContent("1");
  // It is in no store drawer — being low is what files it apart.
  expect(column.queryByRole("button", { name: /plot & commitments/i })).toBeNull();
  fireEvent.click(column.getByRole("button", { name: /character state/i }));
  expect(screen.queryByLabelText(/Approve The forged map/)).toBeNull();

  fireEvent.click(column.getByRole("button", { name: /low confidence/i }));
  expect(cardFor(/The forged map/)).not.toHaveClass("approved");
  expect(screen.getByText(/transcript does not clearly support/i)).toBeInTheDocument();

  // ...and the other two are pre-approved exactly as every row was before.
  fireEvent.click(column.getByRole("button", { name: /character state/i }));
  expect(cardFor(/Seraphine/)).toHaveClass("approved");
});

test("a low-confidence proposal the reviewer approves is saved like any other", async () => {
  await openRoutedReview();
  fireEvent.click(reviewColumn().getByRole("button", { name: /low confidence/i }));
  fireEvent.click(screen.getByLabelText(/Approve The forged map/));
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const sent = (api.saveChronicle as any).mock.calls[0][2].edits;
  expect(sent.map((e: any) => e.id)).toEqual(
    ["character_state:seraphine", "lore:the-pact", "plot:the-forged-map"]);
});

test("an unticked low-confidence proposal is never sent", async () => {
  await openRoutedReview();
  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  expect((api.saveChronicle as any).mock.calls[0][2].edits.map((e: any) => e.id))
    .toEqual(["character_state:seraphine", "lore:the-pact"]);
});

test("each routed row shows its band, why it was banded, and its citation", async () => {
  await openRoutedReview();
  const column = reviewColumn();
  fireEvent.click(column.getByRole("button", { name: /character state/i }));
  expect(screen.getByText(/high · narrated/)).toBeTruthy();
  expect(screen.getByText("She pressed a hand to her side.")).toBeTruthy();
  expect(screen.getByText(/— Grimoire/)).toBeTruthy();

  fireEvent.click(column.getByRole("button", { name: /world records & cards/i }));
  expect(screen.getByText(/medium · said by someone else/)).toBeTruthy();

  fireEvent.click(column.getByRole("button", { name: /low confidence/i }));
  expect(screen.getByText(/low · no one speaker matches/)).toBeTruthy();
});

test("rows the extraction did not stage carry no band and stay pre-approved", async () => {
  // Dossier, voice and sheet proposals are staged after the extraction and rest
  // on no citation. Absent routing must read as "unrated", not as "low".
  await openRoutedReview({ ...ROUTED_REVIEW, edits: [
    { id: "dossier:mara", kind: "dossier", target: { kind: "characters", id: "mara" },
      label: "Mara — dossier", field: "body", before: "", after: "A fortune-teller.",
      authored: false }] });
  expect(cardFor(/Mara/)).toHaveClass("approved");
  expect(screen.queryByText(/low-confidence/)).toBeNull();
});

test("a conflict on a row in another drawer opens that drawer so it can be answered", async () => {
  // The save is refused whole. Left in a drawer nobody is looking at, the panel
  // would insist something is unanswered with nothing on screen to answer.
  const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [{ id: "plot:the-forged-map", label: "The forged map — open",
                    kind: "plot", field: "beat", before: "", after: "Somebody forged it.",
                    stored: "open — someone else forged it",
                    reason: "this plot thread changed since the scene was absorbed",
                    mergeable: false, merged: "Somebody forged it.", index: 2 }] }));
  await openRoutedReview();
  fireEvent.click(reviewColumn().getByRole("button", { name: /low confidence/i }));
  fireEvent.click(screen.getByLabelText(/Approve The forged map/));
  // …then leave that drawer, so the conflicted row is off screen when the save
  // comes back refusing the whole batch.
  fireEvent.click(reviewColumn().getByRole("button", { name: /character state/i }));
  expect(screen.queryByLabelText(/Approve The forged map/)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /Save chronicle/ }));
  await screen.findByText(/no longer match/);
  expect(screen.getByRole("button", { name: /Keep stored The forged map/ })).toBeTruthy();
});

test("the review's column counts what is judged and what is left", async () => {
  await openRoutedReview();
  const column = reviewColumn();
  expect(column.getByText("3 edits")).toBeInTheDocument();
  // Two pre-approved by band, the low one left for a person.
  expect(column.getByText(/2 approved · 0 rejected · 1 left/)).toBeInTheDocument();
  expect(screen.getByText(/1 still to judge/i)).toBeInTheDocument();
});

test("the progress bar fills with work judged, not work approved", async () => {
  // Rejecting everything is a finished review; a bar that read that as no
  // progress would be lying about the only thing it measures.
  await openRoutedReview();
  const bar = () => reviewColumn().getByRole("img", { name: /of 3 judged/i });
  expect(bar()).toHaveAccessibleName("2 of 3 judged");
  fireEvent.click(reviewColumn().getByRole("button", { name: /low confidence/i }));
  fireEvent.click(screen.getByLabelText(/Reject The forged map/));
  expect(bar()).toHaveAccessibleName("3 of 3 judged");
  expect(reviewColumn().getByText(/2 approved · 1 rejected · 0 left/)).toBeInTheDocument();
});

test("approving a row folds it to a line you can undo, and undoing brings it back", async () => {
  await openRoutedReview();
  fireEvent.click(reviewColumn().getByRole("button", { name: /low confidence/i }));
  fireEvent.click(screen.getByLabelText(/Approve The forged map/));

  // Folded, not hidden: a decision you cannot see is one you cannot revisit.
  expect(screen.getByText(/APPROVED · The forged map/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/Reject The forged map/)).toBeNull();

  fireEvent.click(screen.getByLabelText(/Undo The forged map/));
  expect(screen.getByLabelText(/Reject The forged map/)).toBeInTheDocument();
});

test("a row that arrived pre-approved is not folded away", async () => {
  // Rows arrive approved by band. Folding those would hide the bulk of a good
  // absorb behind an Undo apiece — the collapse clears what you have finished
  // with, not what you have not started.
  await openRoutedReview();
  fireEvent.click(reviewColumn().getByRole("button", { name: /character state/i }));
  expect(cardFor(/Seraphine/)).toHaveClass("approved");
  expect(screen.getByLabelText(/Reject Seraphine/)).toBeInTheDocument();
  expect(screen.queryByText(/APPROVED · Seraphine/)).toBeNull();
});

test("an uncited row is filed first, bordered in alert, and stamped NO QUOTE", async () => {
  await openRoutedReview({ ...ROUTED_REVIEW, edits: [
    { id: "fact:f1", kind: "fact", target: { kind: "fact", id: "f1" },
      label: "A standing fact", field: "text", before: "", after: "The priory owes the Reeve",
      authored: false,
      review: { certainty: 0.4, quote: "", speaker: "", authority: "uncited",
                score: 0.1, band: "low" } },
  ] });
  // It opens there without being asked: an uncited row is the one kind a
  // reviewer cannot check against anything.
  expect(reviewColumn().getByRole("button", { name: /uncited/i })).toHaveClass("active");
  expect(cardFor(/standing fact/)).toHaveClass("uncited");
  expect(screen.getByText(/NO QUOTE · CERTAINTY 0.40/)).toBeInTheDocument();
});

test("Approve all cited leaves the uncited rows alone", async () => {
  // The routing argument in one button: a cited row can be checked later, an
  // uncited one cannot, so it is the only kind this refuses to answer for.
  await openRoutedReview({ ...ROUTED_REVIEW, edits: [
    ROUTED_REVIEW.edits[2],   // low, but cited
    { id: "fact:f1", kind: "fact", target: { kind: "fact", id: "f1" },
      label: "A standing fact", field: "text", before: "", after: "The priory owes the Reeve",
      authored: false,
      review: { certainty: 0.4, quote: "", speaker: "", authority: "uncited",
                score: 0.1, band: "low" } },
  ] });
  fireEvent.click(screen.getByRole("button", { name: /approve all cited/i }));
  expect(cardFor(/standing fact/)).not.toHaveClass("approved");
  fireEvent.click(reviewColumn().getByRole("button", { name: /low confidence/i }));
  expect(cardFor(/The forged map/)).toHaveClass("approved");
});

test("a review replaces the scene rather than stacking on top of it", async () => {
  // It used to be a block pinned to the top of the play view, with the scene
  // head, the live transcript and the composer still mounted and scrolling on
  // underneath it — so the review was the top of a page that ran past it, and
  // End scene sat a mis-click from discarding every proposal already judged.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "She pressed a hand to her side.", speaker: "Grimoire" },
  ] });
  absorbs(ROUTED_REVIEW);
  renderCampaign();
  await screen.findByText("hi");
  // The play view, before: composer, scene actions, the live stream.
  expect(screen.getByPlaceholderText(/Speak your intent/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/still to judge/i);

  // None of it survives into the review.
  expect(screen.queryByPlaceholderText(/Speak your intent/)).toBeNull();
  expect(screen.queryByRole("button", { name: "End scene" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Ledger$/ })).toBeNull();
  expect(screen.queryByTestId("stream")).toBeNull();

  // The transcript is still readable — as the review's third pane, which is a
  // different thing from the scene being left mounted behind the review.
  expect(within(screen.getByRole("complementary", { name: /for checking/i }))
    .getByText("She pressed a hand to her side.")).toBeInTheDocument();
  // And the bar says which scene is being judged, since the scene is no longer
  // on screen to say so itself.
  expect(screen.getByRole("button", { name: /rename scene/i })).toHaveTextContent(/Absorbing Old/);
});

test("the transcript sits beside the review, and a row's quote lights its line", async () => {
  // The third pane is why this screen has its own layout: judging a proposal
  // means reading the line it came from, and reading it in another tab means
  // losing the row.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "She pressed a hand to her side.", speaker: "Grimoire" },
  ] });
  absorbs(ROUTED_REVIEW);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/still to judge/i);

  const pane = within(screen.getByRole("complementary", { name: /for checking/i }));
  expect(pane.getByText("She pressed a hand to her side.")).toBeInTheDocument();

  fireEvent.click(reviewColumn().getByRole("button", { name: /character state/i }));
  fireEvent.click(screen.getByRole("button", { name: /Find Seraphine — current state in transcript/i }));
  await waitFor(() => expect(document.querySelectorAll(".review-post.cited")).toHaveLength(1));
});

test("an uncited row offers no find, because there is nothing to find", async () => {
  await openRoutedReview({ ...ROUTED_REVIEW, edits: [
    { id: "fact:f1", kind: "fact", target: { kind: "fact", id: "f1" },
      label: "A standing fact", field: "text", before: "", after: "x", authored: false,
      review: { certainty: null, quote: "", speaker: "", authority: "uncited",
                score: 0.1, band: "low" } },
  ] });
  expect(screen.queryByRole("button", { name: /in transcript/i })).toBeNull();
  expect(screen.getByText(/NO QUOTE · CERTAINTY UNRATED/)).toBeInTheDocument();
});

// ---- the review is durable now (#396) --------------------------------------
//
// It lives on disk between being generated and being saved, so three states
// are reachable on a plain mount that were not before: one waiting, one the
// transcript has moved out from under, and one still being generated by a run
// this browser never started. The last is what a locked phone leaves behind,
// and is the case the whole feature exists for.

const STORED_REVIEW = {
  one_line: "They met.", summary: "A stored summary.", keywords: [],
  timeline_events: [], cast: [], location: "", date: "", edits: [],
  mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
               attempted: true, budget_exhausted: false },
  dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [],
              attempted: false, budget_exhausted: false },
  voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [],
           failed: [], skipped: [], attempted: false, budget_exhausted: false },
  commit_token: "stored-tok", phases: PHASES_NONE_CUT, contradictions: [] };

function withScene() {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(
    { meta: {}, messages: [{ role: "user", content: "hi" }] });
}

test("a review waiting on disk is adopted without asking for another absorb", async () => {
  // The reader locked their phone during End scene and came back. The absorb
  // landed on the server; there is nothing left to generate, and offering to
  // run it again would spend the whole budget a second time.
  withScene();
  (api.pendingReview as any).mockResolvedValue(
    { review: STORED_REVIEW, generation: "gen-stored", stale: null });
  renderCampaign();

  expect(await screen.findByDisplayValue("A stored summary.")).toBeInTheDocument();
  expect(api.absorbScene).not.toHaveBeenCalled();
});

test("a stored review does not replace the one already on screen", async () => {
  // A review deliberately outlives a scene switch -- only Discard or a
  // successful save closes one -- so adopting on every scene change would
  // replace the review being read with whatever the reader just clicked on,
  // throwing away every proposal they had already judged.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Newer", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue(
    { meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  // Scene 2 has a review of its own waiting, and it must stay waiting.
  (api.pendingReview as any).mockResolvedValue(
    { review: STORED_REVIEW, generation: "gen-stored", stale: null });
  await openScene(/Newer/);

  // It does not even ASK, which is the assertion worth making: "the panel still
  // shows A" a microtask after the switch is true whether or not the guard
  // exists, because the replacement would land later. The request not being
  // made is a fact about now.
  await act(async () => { await Promise.resolve(); });
  expect(api.pendingReview).not.toHaveBeenCalledWith("run", "s2");
  expect(screen.getByDisplayValue("A met B.")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("A stored summary.")).toBeNull();
});

test("a review that lands while the adoption pass is asking does not lose to it",
     async () => {
  // The counterweight to the check above, and the reason there are two: the
  // early return covers a review that is already open, and this covers one that
  // opens WHILE the request is in flight. Without the recheck after the await,
  // the answer to a question asked when the panel was empty installs itself
  // over the review the reader has since taken.
  withScene();
  let answer: (v: unknown) => void = () => {};
  (api.pendingReview as any).mockReturnValue(new Promise((r) => { answer = r; }));
  renderCampaign();
  await screen.findByText("hi");

  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  await act(async () => {
    answer({ review: STORED_REVIEW, generation: "gen-stored", stale: null });
    await Promise.resolve();
  });

  expect(screen.getByDisplayValue("A met B.")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("A stored summary.")).toBeNull();
});

test("a review the scene has moved past is reported rather than shown", async () => {
  // Saving it would mark the scene absorbed with a summary of posts that are
  // no longer there -- and the commit epoch cannot see play continuing, so
  // nothing else would catch it.
  withScene();
  (api.pendingReview as any).mockResolvedValue(
    { review: null, generation: "gen-stored",
      stale: { prepared_posts: 4, current_posts: 7 } });
  renderCampaign();

  expect(await screen.findByText(/The scene changed after its review was prepared/))
    .toBeInTheDocument();
  expect(screen.queryByText("Review scene summary")).toBeNull();
});

test("a review still being generated is adopted and waited out", async () => {
  withScene();
  (api.pendingReview as any)
    .mockResolvedValueOnce({ review: null, generation: null, stale: null })
    .mockResolvedValue({ review: STORED_REVIEW, generation: "gen-live", stale: null });
  (api.liveReview as any).mockResolvedValue(
    { id: "r9", attempt_id: null, state: "running", next_index: 0,
      cls: "review", review_generation: "gen-live" });
  let land: (v: unknown) => void = () => {};
  (api.awaitRun as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderCampaign();

  // The panel says so while it waits -- an absorb that is running on the
  // server and silent in the browser is indistinguishable from one that never
  // happened, which is what sends the reader to End scene again.
  expect(await screen.findByRole("button", { name: /Ending…/ })).toBeInTheDocument();
  await act(async () => { land({ id: "r9", state: "landed" }); });
  expect(await screen.findByDisplayValue("A stored summary.")).toBeInTheDocument();
  expect(api.absorbScene).not.toHaveBeenCalled();
});

test("a live CHAT turn is not mistaken for a review", async () => {
  // `liveReview` filters by class, and this is the counterweight that proves
  // the filter is doing something: without it End scene would sit at "Ending…"
  // over a scene that is merely generating a reply.
  withScene();
  (api.liveReview as any).mockResolvedValue(null);
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByRole("button", { name: /Ending…/ })).toBeNull();
  expect(api.awaitRun).not.toHaveBeenCalled();
});

test("Cancel absorb deletes the stored review, by generation", async () => {
  // Closing the panel is not enough any more: the review is on disk, and the
  // DELETE is also the only thing that stops a retry still generating for it.
  withScene();
  await openAbsorb();
  fireEvent.click(screen.getByRole("button", { name: /Cancel absorb/ }));
  await waitFor(() => expect(api.discardReview)
    .toHaveBeenCalledWith("run", "s1", "gen1"));
  expect(screen.queryByText("Review scene summary")).toBeNull();
});

test("End scene over a stale review replaces it, without deleting it first", async () => {
  // A fresh absorb replaces whatever is stored for the scene, so there is
  // nothing for a delete to buy here -- and it would cost something real: the
  // re-absorb path asks for confirmation, and a reader who declines would have
  // lost the review they were looking at to a question they answered "no" to.
  withScene();
  (api.pendingReview as any).mockResolvedValue(
    { review: null, generation: "gen-stale",
      stale: { prepared_posts: 4, current_posts: 7 } });
  absorbs({ ...STORED_REVIEW, commit_token: "second" }, "gen2");
  renderCampaign();
  await screen.findByText(/The scene changed after its review was prepared/);

  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  // The notice goes with the absorb, rather than sitting over the review that
  // has just replaced the one it was about.
  expect(await screen.findByDisplayValue("A stored summary.")).toBeInTheDocument();
  expect(screen.queryByText(/The scene changed after its review was prepared/)).toBeNull();
  expect(api.discardReview).not.toHaveBeenCalled();
});

test("switching scenes mid-wait does not latch the panel on Ending…", async () => {
  // The adoption pass waits on a run this browser did not start, and the
  // reader is free to walk away while it does. Left to the wait's own `finally`
  // -- which is skipped once the effect has been dropped -- the flag stays set
  // and End scene is disabled for every scene in the campaign until a remount.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Newer", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue(
    { meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.pendingReview as any).mockResolvedValue(
    { review: null, generation: null, stale: null });
  // Only the first scene is mid-absorb. The second is an ordinary scene, and
  // the point of the test is that it behaves like one.
  (api.liveReview as any).mockImplementation(async (_cid: string, sid: string) =>
    (sid === "s1"
      ? { id: "r9", attempt_id: null, state: "running", next_index: 0,
          cls: "review", review_generation: "gen-live" }
      : null));
  (api.awaitRun as any).mockReturnValue(new Promise(() => { /* never lands */ }));
  renderCampaign();

  await screen.findByRole("button", { name: /Ending…/ });
  await openScene(/Newer/);

  expect(await screen.findByRole("button", { name: /^End scene$/ })).toBeEnabled();
});

test("an abandoned wait does not clear the next scene's own", async () => {
  // The other half of the flag's release, and the reason the `finally` is
  // guarded while the cleanup is not: a dropped wait can settle long after the
  // next scene's adoption has set the flag for itself, and clearing it there
  // takes "Ending…" off a scene that is still being absorbed.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Newer", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue(
    { meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.pendingReview as any).mockResolvedValue(
    { review: null, generation: null, stale: null });
  (api.liveReview as any).mockResolvedValue(
    { id: "r9", attempt_id: null, state: "running", next_index: 0,
      cls: "review", review_generation: "gen-live" });
  let settleFirst: (v: unknown) => void = () => {};
  (api.awaitRun as any)
    .mockReturnValueOnce(new Promise((r) => { settleFirst = r; }))
    .mockReturnValue(new Promise(() => { /* the second scene keeps waiting */ }));
  renderCampaign();

  await screen.findByRole("button", { name: /Ending…/ });
  await openScene(/Newer/);
  await screen.findByRole("button", { name: /Ending…/ });

  await act(async () => {
    settleFirst({ id: "r9", state: "landed" });
    await Promise.resolve();
  });
  expect(screen.getByRole("button", { name: /Ending…/ })).toBeInTheDocument();
});

test("the scene being absorbed is locked from the moment End scene is pressed", async () => {
  // `absorbSid` scopes the lock, and it can be holding some earlier review's
  // scene -- a stale record adopted on another scene. Left until the review
  // lands, the composer and every scene control stay live for the whole of an
  // absorb, which is the one window the lock exists to cover.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Newer", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue(
    { meta: {}, messages: [{ role: "user", content: "hi" }] });
  // Scene 1 leaves a stale review behind, so `absorbSid` points at it...
  (api.pendingReview as any).mockImplementation(async (_cid: string, sid: string) =>
    (sid === "s1"
      ? { review: null, generation: "gen-A", stale: { prepared_posts: 4, current_posts: 7 } }
      : { review: null, generation: null, stale: null }));
  (api.absorbScene as any).mockReturnValue(new Promise(() => { /* still absorbing */ }));
  renderCampaign();
  await screen.findByText(/The scene changed after its review was prepared/);

  // ...and scene 2 is the one actually being ended.
  await openScene(/Newer/);
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  await waitFor(() => expect(api.absorbScene).toHaveBeenCalledWith(
    "run", "s2", false, expect.any(Function)));
  expect(screen.getByRole("button", { name: "Rename scene" })).toBeDisabled();
});

test("Stop ends an absorb that is holding the scene, before there is a review", async () => {
  // The escape hatch a detached review needs and a synchronous one did not: a
  // review holds the scene's exclusion key for as long as it runs, and
  // `absorb_budget = 0` means nothing bounds that -- so without this a wedged
  // End scene locks the scene against play until the process restarts.
  withScene();
  let never: (v: unknown) => void = () => {};
  (api.absorbScene as any).mockImplementation(
    async (_c: string, _s: string, _f: boolean, onStarted: (g: string) => void) => {
      onStarted("gen-live");
      return new Promise((r) => { never = r; });
    });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));

  fireEvent.click(await screen.findByRole("button", { name: /^Stop$/ }));

  await waitFor(() => expect(api.discardReview)
    .toHaveBeenCalledWith("run", "s1", "gen-live"));
  // The scene is playable again the moment the Stop answers -- the DELETE waits
  // for the run it flagged, so "stopped" really means stopped.
  expect(await screen.findByRole("button", { name: /^End scene$/ })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Rename scene" })).toBeEnabled();

  // ...and the absorb answering afterwards raises no banner: being refused is
  // the answer the reader asked for.
  await act(async () => {
    never(reviewResult(STORED_REVIEW, "gen-live"));
    await Promise.resolve();
  });
  expect(screen.queryByDisplayValue("A stored summary.")).toBeNull();
});

test("End scene waits for a Discard that is still stopping the last review", async () => {
  // `DELETE .../pending-review` answers only once the runs it flagged have
  // really stopped, and until it does they still hold the scene's exclusion
  // key -- so an End scene issued in that window is refused with
  // `run_in_flight` by a review the reader has already dismissed. Cancel stays
  // instant; the wait is paid by the one operation that needs the scene free.
  withScene();
  await openAbsorb();
  let stopped: (v: unknown) => void = () => {};
  (api.discardReview as any).mockReturnValue(new Promise((r) => { stopped = r; }));

  fireEvent.click(screen.getByRole("button", { name: /Cancel absorb/ }));
  // Instant: the panel is gone before the server has answered.
  await waitFor(() => expect(screen.queryByText("Review scene summary")).toBeNull());

  // One call so far: the End scene that opened the review being cancelled.
  expect(api.absorbScene).toHaveBeenCalledTimes(1);
  fireEvent.click(await screen.findByRole("button", { name: /^End scene$/ }));
  await act(async () => { await Promise.resolve(); });
  expect(api.absorbScene).toHaveBeenCalledTimes(1);

  await act(async () => {
    stopped({ removed: true, stopped: 1 });
    await Promise.resolve();
  });
  await waitFor(() => expect(api.absorbScene).toHaveBeenCalledTimes(2));
});
