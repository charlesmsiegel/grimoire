import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import type { SceneDraft } from "./sceneDraft";

// Partial mock, the shape ClockPanel/CastPanel use: the module's pure helpers
// (`splitNativeDate`, which this form uses to drop a time of day) stay real, so
// a test can never pass against a reimplementation of one.
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    createScene: vi.fn(), addCastBatch: vi.fn(), setSceneLocation: vi.fn(),
    setSceneDatetime: vi.fn(), startFromGreeting: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(), listAppearances: vi.fn(), setSceneIdeaStatus: vi.fn(),
    getCampaignClock: vi.fn(),
  } };
});
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel, disabled }: any) =>
    <input aria-label={ariaLabel} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />,
}));
import { api } from "../api/client";

const GEN: SceneDraft = {
  source: "generated", title: "The creditor", defaultTitle: "The creditor",
  date: "2026-03-04", location: "saltmarch", pcless: false,
  premise: "A debt-collector arrives.",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
};
const GRT: SceneDraft = {
  source: "greeting", gid: "reck", title: "Reckoning", defaultTitle: "Reckoning",
  date: "2026-03-04", location: "saltmarch", pcless: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.addCastBatch as any).mockResolvedValue({ ok: true, added: 1, skipped: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, id: "s9-dated" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9-greet" });
  (api.renameScene as any).mockResolvedValue({ id: "s9-titled", title: "T" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.listEntities as any).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.setSceneIdeaStatus as any).mockResolvedValue({ ok: true });
  (api.getCampaignClock as any).mockResolvedValue(
    { now: "5786-Kislev-25", friendly: "25 Kislev 5786", log: [] });
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
});

function renderForm(draft: SceneDraft, onCreated = vi.fn()) {
  render(<SceneConfirmForm cid="c" draft={draft} ready onBack={() => {}} onCreated={onCreated} />);
  return onCreated;
}

test("nothing is written until Create", async () => {
  renderForm(GEN);
  await screen.findByDisplayValue("The creditor");
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Back writes nothing", async () => {
  const onBack = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} ready onBack={onBack} onCreated={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  expect(onBack).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a generated draft creates, casts, locates, dates, and hands off the premise", async () => {
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", "A debt-collector arrives."));
  expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", "2026-03-04", false);
  expect(api.addCastBatch).toHaveBeenCalledWith("c", "s9", [{ kind: "characters", id: "mara" }]);
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "saltmarch");
  expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s9", "2026-03-04");
});

test("a greeting draft applies location and date BEFORE seeding", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const seedOrder = (api.startFromGreeting as any).mock.invocationCallOrder[0];
  const locOrder = (api.setSceneLocation as any).mock.invocationCallOrder[0];
  const dateOrder = (api.setSceneDatetime as any).mock.invocationCallOrder[0];
  expect(locOrder).toBeLessThan(seedOrder);
  expect(dateOrder).toBeLessThan(seedOrder);
  // seeded against the dated scene, and the confirmed title lands after the rename
  // `false`: this pane owns the location, so the server must not seed the
  // greeting's over what the picker says (#218).
  expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9-dated", "reck", false);
  expect(api.renameScene).toHaveBeenCalledWith("c", "s9-greet", "Reckoning");
  expect(onCreated).toHaveBeenCalledWith("s9-titled", undefined);
});

test("clearing a greeting's pre-filled location leaves the scene with none", async () => {
  // The picker starts at the greeting's own location (#218). Emptying it is an
  // answer, not an omission -- and the only way the pane can say so is to stop
  // the server seeding, since an empty scene looks the same either way.
  const onCreated = renderForm(GRT);
  fireEvent.change(await screen.findByLabelText("Location"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(api.setSceneLocation).not.toHaveBeenCalled();
  expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9-dated", "reck", false);
});

test("editing title, date, location, and premise reaches the write sequence", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch" },
    { id: "harrow", name: "Harrow" },
  ]);
  const onCreated = renderForm(GEN);
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "The reckoning" } });
  fireEvent.change(screen.getByLabelText("Scene date"), { target: { value: "2026-05-01" } });
  fireEvent.change(await screen.findByLabelText("Location"), { target: { value: "harrow" } });
  fireEvent.change(screen.getByLabelText("Premise"), { target: { value: "A stranger returns." } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", "A stranger returns."));
  expect(api.createScene).toHaveBeenCalledWith("c", "The reckoning", "2026-05-01", false);
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "harrow");
  expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s9", "2026-05-01");
});

test("adding and removing cast members through the picker reaches addCastBatch", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara" },
    { id: "winifred", name: "Winifred" },
  ]);
  renderForm(GEN);
  // GEN starts cast with Mara already seated; remove her, then add Winifred.
  fireEvent.click(await screen.findByRole("button", { name: "Remove Mara" }));
  fireEvent.change(screen.getByLabelText("Add to cast"), { target: { value: "characters/winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.addCastBatch).toHaveBeenCalledWith(
    "c", "s9", [{ kind: "characters", id: "winifred" }]));
});

test("a greeting draft never hands a premise to the panel", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.any(String), undefined));
});

test("the greeting pane offers no premise or cast control", async () => {
  renderForm(GRT);
  await screen.findByDisplayValue("Reckoning");
  expect(screen.queryByLabelText("Premise")).toBeNull();
  expect(screen.queryByLabelText("Add to cast")).toBeNull();
  expect(screen.getByLabelText("Location")).toBeInTheDocument();
});

test("an emptied title falls back to defaultTitle, not to the backend default", async () => {
  renderForm(GEN);
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "  " } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", expect.any(String), false));
});

test("a cast failure deletes the scene", async () => {
  (api.addCastBatch as any).mockRejectedValue({ detail: "boom" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(screen.getByText(/boom/)).toBeInTheDocument();
});

test("a date failure keeps the scene and offers Continue", async () => {
  (api.setSceneDatetime as any).mockRejectedValue({ detail: "bad date" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/bad date/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives.");
});

test("a startFromGreeting failure deletes the scene", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "not available" });
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  // the scene has already been dated (id adopted from setSceneDatetime) by the
  // time startFromGreeting runs, so the delete must target the DATED id --
  // deleting the stale pre-date id would leave the real scene orphaned
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9-dated"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(screen.getByText(/not available/)).toBeInTheDocument();
});

test("an offscreen draft never offers a player, even one seated as a character", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" },
                                                 { id: "winifred", name: "Winifred" }]);
  (api.listAppearances as any).mockResolvedValue(
    [{ kind: "characters", id: "mara", version: "default", role: "player", scenes: [] }]);
  renderForm({ ...GEN, pcless: true, cast: [] });
  await screen.findByLabelText("Add to cast");
  const options = Array.from(screen.getByLabelText("Add to cast").querySelectorAll("option"))
    .map((o) => o.textContent);
  expect(options).not.toContain("Mara");
  expect(options).toContain("Winifred");
});

test("a pcless draft excludes a PC that has never appeared, not just ones the roster already flags", async () => {
  // Elara has no entry in listAppearances at all -- roster() only enumerates
  // actors that have appeared, so a roster-role filter alone would miss her.
  (api.listCharacters as any).mockResolvedValue([{ id: "winifred", name: "Winifred" }]);
  (api.listCampaignPCs as any).mockResolvedValue(
    [{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listAppearances as any).mockResolvedValue([]);
  renderForm({ ...GEN, pcless: true, cast: [] });
  await screen.findByLabelText("Add to cast");
  const options = Array.from(screen.getByLabelText("Add to cast").querySelectorAll("option"))
    .map((o) => o.textContent);
  expect(options).not.toContain("Elara");
  expect(options).toContain("Winifred");
});

test("an onscreen draft DOES offer a player seated as a character", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" },
                                                 { id: "winifred", name: "Winifred" }]);
  (api.listAppearances as any).mockResolvedValue(
    [{ kind: "characters", id: "mara", version: "default", role: "player", scenes: [] }]);
  renderForm({ ...GEN, pcless: false, cast: [] });
  await screen.findByLabelText("Add to cast");
  const options = Array.from(screen.getByLabelText("Add to cast").querySelectorAll("option"))
    .map((o) => o.textContent);
  expect(options).toContain("Mara");
  expect(options).toContain("Winifred");
});

test("a warning raised while the draft was built is shown here", async () => {
  render(<SceneConfirmForm cid="c" draft={GEN} ready notice="no key -- continuing without inferred details."
                           onBack={() => {}} onCreated={vi.fn()} />);
  expect(await screen.findByText(/continuing without inferred details/)).toBeInTheDocument();
});

test("a location failure keeps the scene and offers Continue", async () => {
  (api.setSceneLocation as any).mockRejectedValue({ detail: "gone" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/gone/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalled();
});

test("an emptied title on a greeting draft falls back to the greeting name", async () => {
  renderForm(GRT);
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("c", "s9-greet", "Reckoning"));
});

test("the form reports writing so the modal can refuse to close", async () => {
  let release: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { release = r; }));
  const onWriting = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} ready onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(onWriting).toHaveBeenLastCalledWith(true);
  await act(async () => { release({ id: "s9" }); });
  await waitFor(() => expect(onWriting).toHaveBeenLastCalledWith(false));
});

test("a cast failure still releases onWriting, not just the busy button", async () => {
  (api.addCastBatch as any).mockRejectedValue({ detail: "boom" });
  const onWriting = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} ready onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
  expect(onWriting).toHaveBeenLastCalledWith(false);
});

test("a startFromGreeting failure still releases onWriting", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "not available" });
  const onWriting = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GRT} ready onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
  expect(onWriting).toHaveBeenLastCalledWith(false);
});

test("a cast member the backend skips is reported rather than silently dropped", async () => {
  // addCastBatch succeeds overall but reports one ref it could not seat (e.g.
  // its default version moved since the actor's first appearance) -- that
  // must not be discarded, or the user is told the cast is complete when it isn't.
  (api.addCastBatch as any).mockResolvedValue({ ok: true, added: 0, skipped: ["characters/mara"] });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(await screen.findByText(/not seated.*Mara/i)).toBeInTheDocument();
  expect(onCreated).not.toHaveBeenCalled();
  expect(api.deleteScene).not.toHaveBeenCalled();   // skipped != failed: the scene is kept, like any soft failure
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalledWith("s9-dated", "A debt-collector arrives.");
});

test("Cancel closes the whole chooser without writing", async () => {
  const onCancel = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} ready onBack={() => {}} onCancel={onCancel} onCreated={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Cancel is disabled while the create sequence is writing, like Back", async () => {
  let release: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { release = r; }));
  render(<SceneConfirmForm cid="c" draft={GEN} ready onBack={() => {}} onCancel={() => {}} onCreated={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(screen.getByRole("button", { name: /^cancel$/i })).toBeDisabled();
  await act(async () => { release({ id: "s9" }); });
});

test("a cast failure whose own cleanup also fails says so", async () => {
  (api.addCastBatch as any).mockRejectedValue({ detail: "boom" });
  (api.deleteScene as any).mockRejectedValue({ detail: "delete blocked" });
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/boom/);
  expect(screen.getByText(/half-made scene/i)).toBeInTheDocument();
});

test("a final rename failure keeps the scene", async () => {
  (api.renameScene as any).mockRejectedValue({ detail: "locked" });
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/locked/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  // the greeting path's own id (from startFromGreeting), not the pre-rename
  // scene id -- the rename failure happens AFTER seeding adopted it
  expect(onCreated).toHaveBeenCalledWith("s9-greet", undefined);
});

// ---- Finding 2 (PR #318 review): a pending/failed locations read must not
// let a location the user never saw reach setSceneLocation ----
test("a pending locations read blocks Create scene", async () => {
  (api.listEntities as any).mockReturnValue(new Promise(() => {}));   // never resolves
  renderForm(GEN);
  await screen.findByDisplayValue("The creditor");
  expect(screen.getByRole("button", { name: /create scene/i })).toBeDisabled();
});

test("a location the read does not offer is cleared rather than sent unseen", async () => {
  // The read SUCCEEDS and simply does not contain the pre-filled id -- deleted
  // between the draft being composed and this pane opening. The <select>
  // renders an unmatched value as "— no location —", so leaving it in state
  // would send Create a location the reader was never shown (#412 review).
  (api.listEntities as any).mockResolvedValue([{ id: "harrow", name: "Harrow" }]);
  const onCreated = renderForm(GEN);   // GEN.location = "saltmarch"
  await screen.findByDisplayValue("The creditor");
  expect(screen.getByText(/no longer in this campaign/i)).toBeInTheDocument();
  expect(screen.getByLabelText<HTMLSelectElement>("Location").value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(api.setSceneLocation).not.toHaveBeenCalled();
});

test("a failed locations read lets the server seed the greeting's own location", async () => {
  // The pane may only claim to own the location when its picker actually
  // worked. A read that failed leaves nothing for the reader to have answered
  // with, so discarding the greeting's location would lose it to an
  // infrastructure fault they had no say in (#412 review).
  (api.listEntities as any).mockRejectedValue({ detail: "boom" });
  const onCreated = renderForm(GRT);
  await screen.findByDisplayValue("Reckoning");
  expect(screen.getByText(/open at the greeting's own location/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(api.setSceneLocation).not.toHaveBeenCalled();
  expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9-dated", "reck", true);
});

test("declining the greeting stops the banner promising its location", async () => {
  // The promise only holds while the greeting is actually going to be the first
  // post: declining it skips startFromGreeting, which is what seeds (#412
  // review). The banner has to follow that choice, not the read that failed.
  (api.listEntities as any).mockRejectedValue({ detail: "boom" });
  renderForm(GRT);
  await screen.findByDisplayValue("Reckoning");
  expect(screen.getByText(/open at the greeting's own location/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: /nothing/i }));
  expect(screen.queryByText(/open at the greeting's own location/i)).toBeNull();
  expect(screen.getByText(/pre-filled location was cleared/i)).toBeInTheDocument();
});

test("the location picker is not editable before it can show what is selected", async () => {
  // No <option> matches a pre-filled id until the list lands, so the browser
  // shows index 0 and picking it fires no change event -- the value the reader
  // believes they cleared would reappear (#412 review).
  (api.listEntities as any).mockReturnValue(new Promise(() => {}));   // never resolves
  renderForm(GRT);
  await screen.findByDisplayValue("Reckoning");
  expect(screen.getByLabelText("Location")).toBeDisabled();
});

test("a failed locations read clears an unresolved location rather than sending it unseen", async () => {
  (api.listEntities as any).mockRejectedValue({ detail: "boom" });
  const onCreated = renderForm(GEN);   // GEN.location = "saltmarch"
  await screen.findByDisplayValue("The creditor");
  await waitFor(() => expect(screen.getByRole("button", { name: /create scene/i })).not.toBeDisabled());
  expect(screen.getByText(/pre-filled location was cleared/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  // "saltmarch" was never shown in the (empty) <select>, so it must never reach the API
  expect(api.setSceneLocation).not.toHaveBeenCalled();
});

// ---- Finding 3 (PR #318 review): fields must not stay editable once
// create() has captured the render's values, or once a soft failure means
// Continue can no longer save anything typed afterward ----
test("fields are disabled while the create sequence is writing", async () => {
  let release: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(screen.getByLabelText("Title")).toBeDisabled();
  expect(screen.getByLabelText("Scene date")).toBeDisabled();
  expect(screen.getByLabelText("Location")).toBeDisabled();
  expect(screen.getByLabelText("Premise")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove Mara" })).toBeDisabled();
  expect(screen.getByLabelText("Add to cast")).toBeDisabled();
  await act(async () => { release({ id: "s9" }); });
});

test("fields stay disabled after a soft failure, since Continue cannot save further edits", async () => {
  (api.setSceneLocation as any).mockRejectedValue({ detail: "gone" });
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/gone/);
  expect(screen.getByLabelText("Title")).toBeDisabled();
  expect(screen.getByLabelText("Scene date")).toBeDisabled();
  expect(screen.getByLabelText("Location")).toBeDisabled();
  expect(screen.getByLabelText("Premise")).toBeDisabled();
});

// ---- Finding 5 (PR #318 review): a characters-kind actor who is the
// campaign's player must be seated with role "player", not defaulted to
// "npc" by the backend and then rejected by the campaign's locked role ----
test("a character-kind player is seated with role player, not left to the backend's npc default", async () => {
  (api.listAppearances as any).mockResolvedValue(
    [{ kind: "characters", id: "mara", version: "default", role: "player", scenes: [] }]);
  renderForm(GEN);   // GEN's cast already seats Mara as a `characters` actor
  await screen.findByLabelText("Add to cast");   // roster (and so playerTokens) has settled
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.addCastBatch).toHaveBeenCalledWith(
    "c", "s9", [{ kind: "characters", id: "mara", role: "player" }]));
});

// ---- issue #90: the first-post source selector ----
const BLANK: SceneDraft = {
  source: "custom", title: "New scene", defaultTitle: "New scene",
  date: "2026-03-04", location: "", pcless: false, premise: "", cast: [],
};

test("a greeting draft defaults to using the greeting verbatim", async () => {
  renderForm(GRT);
  expect(await screen.findByRole("radio", { name: /verbatim/i })).toBeChecked();
});

test("a generated draft with a premise defaults to generating an opening post", async () => {
  renderForm(GEN);
  expect(await screen.findByRole("radio", { name: /generate one/i })).toBeChecked();
  expect(screen.queryByRole("radio", { name: /verbatim/i })).toBeNull();
});

test("a draft with nothing typed defaults to no first post", async () => {
  renderForm(BLANK);
  expect(await screen.findByRole("radio", { name: /^nothing/i })).toBeChecked();
  expect(screen.queryByLabelText("Premise")).toBeNull();
});

test("declining the first post on a greeting draft never seeds the greeting", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("radio", { name: /^nothing/i }));
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", undefined));
  expect(api.startFromGreeting).not.toHaveBeenCalled();
  // the rename exists only to undo start_from_greeting's own retitle; the date
  // stamp re-slugs the filename but keeps the title, so nothing overwrote it
  expect(api.renameScene).not.toHaveBeenCalled();
});

test("a greeting draft that is not using its greeting casts the scene itself", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "winifred", name: "Winifred" }]);
  renderForm(GRT);
  // the backend only seats a greeting's cast when the greeting is the first post
  expect(screen.queryByLabelText("Add to cast")).toBeNull();
  fireEvent.click(await screen.findByRole("radio", { name: /^nothing/i }));
  fireEvent.change(await screen.findByLabelText("Add to cast"), { target: { value: "characters/winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.addCastBatch).toHaveBeenCalledWith(
    "c", "s9", [{ kind: "characters", id: "winifred" }]));
});

test("a greeting draft can take a premise instead of the greeting body", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("radio", { name: /generate one/i }));
  fireEvent.change(screen.getByLabelText("Premise"), { target: { value: "The tide comes in." } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", "The tide comes in."));
  expect(api.startFromGreeting).not.toHaveBeenCalled();
});

test("declining the first post keeps the premise out of the opener box", async () => {
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("radio", { name: /^nothing/i }));
  expect(screen.queryByLabelText("Premise")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", undefined));
});

test("Continue after a soft failure honours the declined first post", async () => {
  (api.setSceneLocation as any).mockRejectedValue({ detail: "gone" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("radio", { name: /^nothing/i }));
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await screen.findByText(/gone/);
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalledWith("s9-dated", undefined);
});

test("the generate option admits it when no LLM is connected", async () => {
  // SceneIdeaPicker and CastPanel both say this; this pane is the one now
  // offering to "generate an opening post", so it cannot be the one that stays
  // quiet about there being nothing to generate with. Reachable without an LLM:
  // the typed path builds a draft with a premise whether or not one is set up.
  render(<SceneConfirmForm cid="c" draft={GEN} ready={false} onBack={() => {}} onCreated={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: /generate one/i }));
  expect(screen.getByText(/set up an llm connection/i)).toBeInTheDocument();
});

test("the generate option stays quiet when an LLM is connected", async () => {
  renderForm(GEN);
  await screen.findByLabelText("Premise");
  expect(screen.queryByText(/set up an llm connection/i)).toBeNull();
});

test("the first-post choice stays locked after a soft failure, like every other field", async () => {
  (api.setSceneLocation as any).mockRejectedValue({ detail: "gone" });
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/gone/);
  expect(screen.getByRole("radio", { name: /^nothing/i })).toBeDisabled();
});

test("the first-post choice is locked while the create sequence is writing", async () => {
  let release: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(screen.getByRole("radio", { name: /^nothing/i })).toBeDisabled();
  await act(async () => { release({ id: "s9" }); });
});

// ---- the scene ledger (#88): a picked idea is marked used once its scene
// exists, and only then ----
const SAVED: SceneDraft = { ...(GEN as any), source: "saved", lid: "the-tide-book" };

test("a saved draft marks its ledger idea used, against the scene's final id", async () => {
  const onCreated = renderForm(SAVED);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", "A debt-collector arrives."));
  // "s9-dated", not the id createScene returned: the date stamp renames the
  // scene, and the ledger must point at the scene that exists
  expect(api.setSceneIdeaStatus).toHaveBeenCalledWith("c", "the-tide-book", "used", "s9-dated");
});

test("a draft that is not a saved idea never touches the ledger", async () => {
  renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalled());
  expect(api.setSceneIdeaStatus).not.toHaveBeenCalled();
});

test("a failed mark-used keeps the scene and says the idea is still on the list", async () => {
  // the scene is real and usable, so this is soft -- but silently leaving the
  // idea active is indistinguishable from a deliberate keep
  (api.setSceneIdeaStatus as any).mockRejectedValue({ detail: "ledger unreachable" });
  const onCreated = renderForm(SAVED);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/ledger unreachable/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalledWith("s9-dated", "A debt-collector arrives.");
});

// ---- the "Last scene's date" fill button ----

test("the fill button puts the campaign's date in the form and writes nothing", async () => {
  const draft = { ...GEN, date: "" };
  renderForm(draft);
  const button = await screen.findByRole("button", { name: /last scene's date/i });
  expect(screen.getByLabelText("Scene date")).toHaveValue("");

  fireEvent.click(button);

  expect(screen.getByLabelText("Scene date")).toHaveValue("5786-Kislev-25");
  // a FILL, not an apply: the scene's date is only written by Create
  expect(api.setSceneDatetime).not.toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("the fill button names the date it will fill in", async () => {
  renderForm({ ...GEN, date: "" });
  const button = await screen.findByRole("button", { name: /last scene's date/i });
  expect(button).toHaveAttribute("title", expect.stringContaining("25 Kislev 5786"));
});

test("the fill button drops a time of day — the form takes a date", async () => {
  (api.getCampaignClock as any).mockResolvedValue(
    { now: "5786-Kislev-25T21:30", friendly: "25 Kislev 5786", log: [] });
  renderForm({ ...GEN, date: "" });
  fireEvent.click(await screen.findByRole("button", { name: /last scene's date/i }));
  expect(screen.getByLabelText("Scene date")).toHaveValue("5786-Kislev-25");
});

test("no fill button on a campaign that has no date yet", async () => {
  (api.getCampaignClock as any).mockResolvedValue({ now: "", friendly: "", log: [] });
  renderForm({ ...GEN, date: "" });
  await screen.findByDisplayValue("The creditor");
  expect(screen.queryByRole("button", { name: /last scene's date/i })).toBeNull();
});

test("a failed clock read costs the button, not the form", async () => {
  (api.getCampaignClock as any).mockRejectedValue(new Error("nope"));
  renderForm({ ...GEN, date: "" });
  await screen.findByDisplayValue("The creditor");
  expect(screen.queryByRole("button", { name: /last scene's date/i })).toBeNull();
});

test("the fill overrides a date already in the form", async () => {
  renderForm(GEN);   // draft carries 2026-03-04
  fireEvent.click(await screen.findByRole("button", { name: /last scene's date/i }));
  expect(screen.getByLabelText("Scene date")).toHaveValue("5786-Kislev-25");
});

test("the button names the native date when the calendar cannot render a friendly one", async () => {
  // `GET /clock` answers friendly: "" for a calendar it cannot load — the
  // moment is still real and still worth offering.
  (api.getCampaignClock as any).mockResolvedValue(
    { now: "5786-Kislev-25", friendly: "", log: [] });
  renderForm({ ...GEN, date: "" });
  const button = await screen.findByRole("button", { name: /last scene's date/i });
  expect(button).toHaveAttribute("title", expect.stringContaining("5786-Kislev-25"));

  fireEvent.click(button);
  expect(screen.getByLabelText("Scene date")).toHaveValue("5786-Kislev-25");
});
