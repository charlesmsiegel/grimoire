// The play view's own bindings (#193): the shortcuts a reader has while a
// scene is open, and the guards they inherit from the controls they mirror.
//
// A suite of its own rather than more of `CampaignView.test.tsx`, for the
// reason `SceneReview` is one (#378): it drives the same page through the same
// harness, and what it is about — the keyboard — is not what that file is
// about.
import { screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponsePresetPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponsePresetPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";

beforeEach(installCampaignMocks);

const composer = () => screen.getByRole("textbox");
function press(key: string, init: Partial<KeyboardEventInit> = {}, on: Window | Element = window) {
  fireEvent.keyDown(on, { key, ...init });
}

const ONE_EXCHANGE = { meta: {}, messages: [
  { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] };

test("the send chord reaches the turn from outside the composer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(composer(), { target: { value: "hello" } });
  // Focus deliberately elsewhere: the textarea's own Enter already covers the
  // caret being in it, and what this binding adds is the reader who just
  // clicked a dossier chip and still means to send what they wrote.
  screen.getByRole("button", { name: /\+ new scene/i }).focus();
  press("Enter", { metaKey: true });
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "hello", expect.any(Function), undefined, expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
});

test("the send chord sends once, not once per listener, from inside the composer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(composer(), { target: { value: "hello" } });
  composer().focus();
  press("Enter", { metaKey: true }, composer());
  await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(1));
});

test("N opens the new-scene chooser", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  press("n");
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
  expect(api.createScene).not.toHaveBeenCalled();
});

// The whole reason the dispatcher asks about focus: a scene is written in
// prose, and half the alphabet is a shortcut somewhere.
test("a letter typed into the composer is prose, not a shortcut", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  composer().focus();
  press("n", {}, composer());
  press("r", {}, composer());
  press("t", {}, composer());
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(api.retry).not.toHaveBeenCalled();
});

test("R opens the reroll box on the reply the ↻ button hangs off", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(ONE_EXCHANGE);
  renderCampaign();
  await screen.findByText("old reply");
  press("r");
  // The same two-step the button is: guidance first, and nothing spent until
  // the reader says go.
  expect(await screen.findByLabelText("Reroll guidance")).toBeInTheDocument();
  expect(api.regenerate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());
});

// The ↻ button is hidden while its own post is being edited, and `reroll()`
// does not clear the draft: the edit form would rebind to the replacement reply
// at the same index, and Save would overwrite what was just generated. Same
// guard `pickAlternate` takes, for the same reason (PR #400 review).
test("R is inert while an edit form is open", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(ONE_EXCHANGE);
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getAllByTitle("Edit message")[1]);   // the assistant post
  await screen.findByLabelText("Edit message");
  press("r");
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
});

// The reroll box answers plain Enter without preventing the default, so a
// send chord typed into it used to reach BOTH: `reroll()` from the input and
// `send()` from the window, each reading the same render's `busy === false`,
// each entering `runStream`. Two turns racing one scene (PR #400 review).
test("the send chord does not fire alongside an inline action that owns Enter", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(ONE_EXCHANGE);
  renderCampaign();
  await screen.findByText("old reply");
  press("r");
  const box = await screen.findByLabelText("Reroll guidance");
  box.focus();
  press("Enter", { metaKey: true }, box);
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(1));
  expect(api.chat).not.toHaveBeenCalled();
});

test("R is inert when there is no reply to reroll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  press("r");
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
});

test("T retries the turn that failed, and only while it has failed", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hello" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "boom" } });
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  press("t");
  expect(api.retry).not.toHaveBeenCalled();     // nothing has failed yet
  fireEvent.change(composer(), { target: { value: "hello" } });
  fireEvent.keyDown(composer(), { key: "Enter" });
  await screen.findByRole("button", { name: /retry/i });
  press("t");
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
});

describe("the help sheet, in a scene", () => {
  test("lists what this scene binds beside what works anywhere", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    (api.getScene as any).mockResolvedValue(ONE_EXCHANGE);
    renderCampaign();
    await screen.findByText("old reply");
    press("?");
    const sheet = screen.getByRole("dialog", { name: /keyboard/i });
    expect(sheet.textContent).toContain("New scene");
    expect(sheet.textContent).toContain("Reroll the last reply");
    expect(sheet.textContent).toContain("Go anywhere");
  });

  // `?` has the same problem the bindings under it have — nothing on screen
  // says it exists — so the sheet is offered by the one surface that answers
  // "what can I do here" without being asked.
  test("is reachable from the palette, for the reader who does not know ?", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    renderCampaign();
    await screen.findByRole("heading", { name: /^Old$/ });
    press("k", { metaKey: true });
    const input = await screen.findByRole("combobox", { name: /search/i });
    fireEvent.change(input, { target: { value: "keyboard" } });
    // By role, not by text: the palette bolds the matched substring, so the
    // label is three nodes rather than one.
    fireEvent.click(await screen.findByRole("option", { name: /keyboard shortcuts/i }));
    expect(await screen.findByRole("dialog", { name: /keyboard/i })).toBeInTheDocument();
  });

  // Both open from anywhere, and the palette draws BENEATH this sheet: without
  // this, ⌘K would open a palette nobody can see, take focus into its hidden
  // search box, and answer the next Escape (PR #400 review).
  test("⌘K replaces it rather than opening behind it", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    renderCampaign();
    await screen.findByRole("heading", { name: /^Old$/ });
    press("?");
    expect(screen.getByRole("dialog", { name: /keyboard/i })).toBeTruthy();
    press("k", { metaKey: true });
    expect(await screen.findByRole("combobox", { name: /search/i })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
  });

  // Yielding to the palette must not also drag focus back to wherever the
  // sheet found it: the palette would be up and typing would go to a control
  // behind it (PR #400 review). Only reachable when something WAS focused --
  // with focus on `<body>`, restoring it is a no-op and the bug hides.
  test("yielding to ⌘K leaves focus in the palette, not where the sheet found it", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    renderCampaign();
    await screen.findByRole("heading", { name: /^Old$/ });
    screen.getByRole("button", { name: /\+ new scene/i }).focus();
    press("?");
    press("k", { metaKey: true });
    const input = await screen.findByRole("combobox", { name: /search/i });
    expect(document.activeElement).toBe(input);
  });

  // The sheet is read to find out what a key IS; a row that vanished whenever
  // its control was disabled would answer "there is no such key".
  test("dims the rows whose control is disabled right now", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    renderCampaign();
    await screen.findByRole("heading", { name: /^Old$/ });
    press("?");
    const row = screen.getByText("Reroll the last reply").closest(".shortcuts-row")!;
    expect(row.className).toContain("off");
    expect(screen.getByText("New scene").closest(".shortcuts-row")!.className).not.toContain("off");
  });
});
