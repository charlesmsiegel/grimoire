import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type {
PinRule
} from "../api/types";
import { fixture } from "../testkit/fixture";
import { renderInspector, resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

/** One pin row as the panel receives it. */
function pinRow(over: Partial<PinRule> = {}): PinRule {
  return {
    ref: "characters:seraphine", kind: "characters", id: "seraphine",
    name: "Seraphine", missing: false, mode: "pin", scope: "scene", sid: "s",
    ttl_posts: 0, remaining: null, created: "2026-01-01T00:00:00Z", ...over,
  };
}

test("an empty rule set shows the hint, not a list", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect(api.getPins).toHaveBeenCalledWith("c", "s");
});

test("a cast row's pin toggle files a scene-scoped rule and reloads", async () => {
  const changed = vi.fn();
  renderInspector(changed);
  fireEvent.click(await screen.findByRole("button", { name: "Pin Seraphine in the prompt" }));
  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "characters:seraphine", mode: "pin", scope: "scene", sid: "s" }));
  // The prompt moved, so the context panel has to be re-read.
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(api.getPins).toHaveBeenCalledTimes(2);
});

test("the toggle reads as pressed while its rule stands, and lifts it on a second click", async () => {
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [pinRow()] }));
  renderInspector();
  const pin = await screen.findByRole("button", { name: "Pin Seraphine in the prompt" });
  await waitFor(() => expect(pin.getAttribute("aria-pressed")).toBe("true"));

  fireEvent.click(pin);
  await waitFor(() => expect(api.removePin).toHaveBeenCalledWith("c", "characters:seraphine", "scene", "s"));
  expect(api.setPin).not.toHaveBeenCalled();
});

test("excluding someone already pinned replaces the rule rather than removing it", async () => {
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [pinRow()] }));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: "Exclude Seraphine from the prompt" }));
  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "characters:seraphine", mode: "exclude", scope: "scene", sid: "s" }));
  expect(api.removePin).not.toHaveBeenCalled();
});

test("the panel lists each rule with its scope, countdown and a way to lift it", async () => {
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [
    pinRow({ remaining: 3, ttl_posts: 3 }),
    pinRow({ ref: "lore:tide-oath", kind: "lore", id: "tide-oath", name: "Tide oath",
             mode: "exclude", scope: "campaign", sid: "" }),
  ] }));
  renderInspector();
  await screen.findByText(/3 posts left/);
  await screen.findByText(/lore · campaign/);
  expect(screen.getByText("excluded")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Lift the rule on Tide oath" }));
  await waitFor(() => expect(api.removePin).toHaveBeenCalledWith("c", "lore:tide-oath", "campaign", ""));
});

test("a rule whose target the campaign lost is shown as deleted rather than hidden", async () => {
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [
    pinRow({ ref: "lore:gone", kind: "lore", id: "gone", name: "gone", missing: true })] }));
  renderInspector();
  await screen.findByText(/deleted/);
});

test("adding a rule from the picker sends the kind, mode, scope and post window", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  renderInspector();
  await screen.findByText(/Nothing pinned/);

  fireEvent.change(screen.getByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "c" }, "lore"));
  fireEvent.change(await screen.findByLabelText("Record to pin or exclude"),
                   { target: { value: "tide-oath" } });
  fireEvent.change(screen.getByLabelText("Pin or exclude"), { target: { value: "exclude" } });
  fireEvent.change(screen.getByLabelText("Posts to keep the rule for"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add rule" }));

  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "lore:tide-oath", mode: "exclude", scope: "scene", sid: "s", ttl_posts: 4 }));
});

test("a campaign-wide rule hides the TTL box, since posts have no scene to run in", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect(screen.getByLabelText("Posts to keep the rule for")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Rule scope"), { target: { value: "campaign" } });
  expect(screen.queryByLabelText("Posts to keep the rule for")).toBeNull();
});

test("Add is disabled until a record is picked", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "+ Add rule" }).disabled).toBe(true);
});

test("a refused rule surfaces the server's reason", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  vi.mocked(api.setPin).mockRejectedValue({ detail: "a campaign-scoped rule cannot carry a post TTL" });
  renderInspector();
  fireEvent.change(await screen.findByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  fireEvent.change(await screen.findByLabelText("Record to pin or exclude"),
                   { target: { value: "tide-oath" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add rule" }));
  await screen.findByText(/cannot carry a post TTL/);
});

test("a pinned section is marked in the context breakdown", async () => {
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0, pinned: true }],
  });
  renderInspector();
  await screen.findByText("pinned");
});

test("a campaign-wide rule shows on the row but is not toggled from it", async () => {
  // The row toggles only ever write scene scope, so leaving them live here made
  // a pressed button whose click changed nothing a reader could see.
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [pinRow({ scope: "campaign", sid: "" })] }));
  renderInspector();
  const pin = await screen.findByRole("button", { name: "Pin Seraphine in the prompt" });
  await waitFor(() => expect(pin.getAttribute("aria-pressed")).toBe("true"));
  expect((pin as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(pin);
  expect(api.setPin).not.toHaveBeenCalled();
  expect(api.removePin).not.toHaveBeenCalled();
});

test("switching scenes does not leave the previous scene's rules driving the toggles", async () => {
  // The component survives a scene switch, so an unscoped rule list left the
  // new scene's row showing the old scene's pin — and a click on it would have
  // asked the server to lift a rule that scene never had.
  vi.mocked(api.getPins).mockResolvedValue(fixture({ pins: [pinRow()] }));
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.getByRole("button", { name: "Pin Seraphine in the prompt" }).getAttribute("aria-pressed"),
  ).toBe("true"));

  // The next scene's read is still in flight when it repaints.
  vi.mocked(api.getPins).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  expect(
    screen.getByRole("button", { name: "Pin Seraphine in the prompt" }).getAttribute("aria-pressed"),
  ).toBe("false");
});

test("the picker's options do not survive a campaign switch", async () => {
  // Same reason, one cache over: offering another campaign's lore here would
  // file a rule naming an entry this campaign has never had.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.change(await screen.findByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  // Scoped to the picker: `listEntities` also feeds the location list, so a
  // bare option query would find the Move-to select's copy of the same name.
  const picker = () => within(screen.getByLabelText("Record to pin or exclude"));
  await waitFor(() => expect(picker().queryByRole("option", { name: "Tide oath" })).not.toBeNull());

  vi.mocked(api.listEntities).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(picker().queryByRole("option", { name: "Tide oath" })).toBeNull());
});

test("a retired pin save cannot clear the next scene's new draft or refresh it", async () => {
  let finish: (value: Awaited<ReturnType<typeof api.setPin>>) => void = () => {};
  vi.mocked(api.setPin).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  vi.mocked(api.listEntities).mockResolvedValue(fixture([{ id: "crypt", name: "Crypt" }]));
  const changed = vi.fn();
  const view = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={changed} /></MemoryRouter>);
  await screen.findByText("The Crypt");
  fireEvent.change(screen.getByLabelText("What to pin or exclude"), { target: { value: "locations" } });
  await screen.findByRole("option", { name: "Crypt" });
  fireEvent.change(screen.getByLabelText("Record to pin or exclude"), { target: { value: "crypt" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add rule" }));
  view.rerender(<MemoryRouter><SceneInspector cid="c" sid="next" refreshKey={0} onSceneChanged={changed} /></MemoryRouter>);
  await screen.findByText("The Crypt");
  fireEvent.change(screen.getByLabelText("Record to pin or exclude"), { target: { value: "crypt" } });
  fireEvent.change(screen.getByLabelText("Posts to keep the rule for"), { target: { value: "7" } });
  await act(async () => { finish(fixture({ ok: true })); });
  expect(screen.getByLabelText("Record to pin or exclude")).toHaveValue("crypt");
  expect(screen.getByLabelText("Posts to keep the rule for")).toHaveValue(7);
  expect(changed).not.toHaveBeenCalled();
});
