import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import type { SceneDraft } from "./sceneDraft";

vi.mock("../api/client", () => ({
  api: {
    createScene: vi.fn(), addCastBatch: vi.fn(), setSceneLocation: vi.fn(),
    setSceneDatetime: vi.fn(), startFromGreeting: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(), listAppearances: vi.fn(),
  },
}));
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel }: any) =>
    <input aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />,
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
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
});

function renderForm(draft: SceneDraft, onCreated = vi.fn()) {
  render(<SceneConfirmForm cid="c" draft={draft} onBack={() => {}} onCreated={onCreated} />);
  return onCreated;
}

test("nothing is written until Create", async () => {
  renderForm(GEN);
  await screen.findByDisplayValue("The creditor");
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Back writes nothing", async () => {
  const onBack = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} onBack={onBack} onCreated={vi.fn()} />);
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
  const order = (api.setSceneDatetime as any).mock.invocationCallOrder[0];
  expect(order).toBeLessThan((api.startFromGreeting as any).mock.invocationCallOrder[0]);
  // seeded against the dated scene, and the confirmed title lands after the rename
  expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9-dated", "reck");
  expect(api.renameScene).toHaveBeenCalledWith("c", "s9-greet", "Reckoning");
  expect(onCreated).toHaveBeenCalledWith("s9-titled", undefined);
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
  renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
});

test("an offscreen draft never offers a player, even one seated as a character", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" },
                                                 { id: "winifred", name: "Winifred" }]);
  (api.listAppearances as any).mockResolvedValue(
    [{ kind: "characters", id: "mara", version: "default", role: "player", scenes: [] }]);
  renderForm({ ...GEN, pcless: true, cast: [] } as any);
  await screen.findByLabelText("Add to cast");
  const options = Array.from(screen.getByLabelText("Add to cast").querySelectorAll("option"))
    .map((o) => o.textContent);
  expect(options).not.toContain("Mara");
  expect(options).toContain("Winifred");
});

test("a warning raised while the draft was built is shown here", async () => {
  render(<SceneConfirmForm cid="c" draft={GEN} notice="no key -- continuing without inferred details."
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
  render(<SceneConfirmForm cid="c" draft={GEN} onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  expect(onWriting).toHaveBeenLastCalledWith(true);
  await act(async () => { release({ id: "s9" }); });
  await waitFor(() => expect(onWriting).toHaveBeenLastCalledWith(false));
});

test("a cast failure still releases onWriting, not just the busy button", async () => {
  (api.addCastBatch as any).mockRejectedValue({ detail: "boom" });
  const onWriting = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
  expect(onWriting).toHaveBeenLastCalledWith(false);
});

test("a startFromGreeting failure still releases onWriting", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "not available" });
  const onWriting = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GRT} onBack={() => {}} onCreated={vi.fn()}
                           onWriting={onWriting} />);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
  expect(onWriting).toHaveBeenLastCalledWith(false);
});

test("a final rename failure keeps the scene", async () => {
  (api.renameScene as any).mockRejectedValue({ detail: "locked" });
  renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/locked/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /continue to scene/i })).toBeInTheDocument();
});
