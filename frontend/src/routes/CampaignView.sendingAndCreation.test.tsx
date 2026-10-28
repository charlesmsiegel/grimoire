import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { campaignInference, here, Here, installCampaignMocks, ONE_SCENE, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { ShowState, SCENES_WITH_DATE } from "../testkit/campaignFixtures";

// The mocks and the per-test defaults live in `src/testkit`, which the review
// suite shares (#378). A `vi.mock` factory is hoisted above every import
// and can close over nothing, so it reaches the harness through a dynamic
// import rather than a module-scope binding.
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);



test("Enter sends a message in the active scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() =>
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), false),
  );
});

test("Shift+Enter does not send", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("the response length picker shows the scene's preset and reverts after a successful send", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  // Waited for, not asserted flat. The <select> renders as soon as `listScenes`
  // names the active scene; its VALUE arrives on the separate `getScene` chain,
  // so `findBy` returning the element says nothing about the preset being in it
  // yet. `findBy` polls on a timer, so under load a poll lands between the two
  // commits and the flat assert reads "" -- which is the whole of this file's
  // flakiness, one arbitrary victim per full-suite run.
  await waitFor(() => expect(picker).toHaveValue(null));
  fireEvent.change(picker, { target: { value: "120" } });
  expect(picker).toHaveValue(120);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // the picker's promise is "the next reply" — once it lands, the one-shot
  // pick is spent and it falls back to the scene's own setting.
  await waitFor(() => expect(picker).toHaveValue(null));
});

// The Escape-closes-the-dropdown test that stood here is gone with the custom
// listbox it covered. A native <select> is opened, closed, Escape-dismissed and
// keyboard-driven by the browser; there is no longer any code of ours to test.

test("sends the one-shot override in the chat request payload", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  // Same wait, and for the same reason, as the test above: the scene's own
  // preset arrives on the `getScene` chain AFTER the <select> renders, so an
  // override picked before it lands is overwritten by it.
  await waitFor(() => expect(picker).toHaveValue(null));
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "Go on.", expect.any(Function), { response_continuation_words: "120" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
});

test("the one-shot picker refuses nonpositive and fractional values", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  expect(picker).toHaveValue(120);
  for (const value of ["0", "-1", "1.5", "1e2"]) {
    fireEvent.change(picker, { target: { value } });
    expect(picker).toHaveValue(120);
  }
});

test("a failed stream keeps the override, and retry carries it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  vi.mocked(api.chat).mockRejectedValueOnce(new Error("stream failed"));
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  await waitFor(() => expect(picker).toHaveValue(null));  // see above
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(picker).toHaveValue(120)); // NOT cleared by the failure
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { response_continuation_words: "120" }, expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
});

// "Sending with no scene creates one" is gone with the state that allowed it:
// the play view mounts on a scene now, so there is no composer in an empty
// campaign to type the first one into. Creation moved to the scenes list, and
// its coverage with it (`ScenesView.test.tsx`).

test("+ New Scene opens the chooser without creating a scene", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a chooser pick refreshes the rail, selects the scene, and seeds the prompt", async () => {
  vi.mocked(api.listScenes)
    .mockResolvedValueOnce(ONE_SCENE)                // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s9", { limit: 60 }));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  // the premise reaches the empty scene's CastPanel
  expect(await screen.findByText("A premise")).toBeInTheDocument();
});

test("a seeded premise survives the rename from the first date set", async () => {
  vi.mocked(api.listScenes)
    .mockResolvedValueOnce(ONE_SCENE)                // initial load
    .mockResolvedValueOnce([{ id: "s9", title: "New", model: "", created: "", updated: "", date: "" }])
    .mockResolvedValue([{ id: "s10", title: "New", model: "", created: "", updated: "", date: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await screen.findByText("A premise");
  fireEvent.click(screen.getByText("stub-datestamp"));   // first date set renames s9 -> s10
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s10", { limit: 60 }));
  expect(screen.getByTestId("cast-panel")).toHaveTextContent("A premise");
});

test("the setup panel is not remounted by the rename its own date field causes", async () => {
  // The rename drops `loaded` until the re-read lands, on purpose: nothing may
  // act on posts whose indices might have moved. The setup panel acts on no
  // post, and remounting it there threw away what it held and re-read the
  // campaign's character list mid-setup.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const panel = await screen.findByTestId("cast-panel");
  vi.mocked(api.listScenes).mockResolvedValue(
    [{ id: "s10", title: "Old", model: "", created: "", updated: "", date: "" }]);
  fireEvent.click(screen.getByText("stub-datestamp"));   // first date set renames s1 -> s10
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s10", { limit: 60 }));
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s10"));
  expect(screen.getByTestId("cast-panel")).toBe(panel);
});

test("closing the chooser creates nothing", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  // Counted from before the click, not from zero: the view has a scene open,
  // so it has already read one. What must not happen is a SECOND read caused
  // by dismissing the chooser.
  const readsBefore = vi.mocked(api.getScene).mock.calls.length;
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(api.createScene).not.toHaveBeenCalled();
  expect(vi.mocked(api.getScene).mock.calls.length).toBe(readsBefore);
});

test("the edit button renames a scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "New"));
});

test("the delete button deletes a scene after confirm", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "s1"));
});

test("a delete the server refuses says so and leaves the scene", async () => {
  // The ✕ drops the promise, so a refusal used to be an unhandled rejection:
  // the row stood there with nothing to say the delete had been asked for.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.deleteScene).mockRejectedValue(
    new ApiError(409, "a review is being prepared for this scene", "scene_busy"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(await screen.findByText(/a review is being prepared for this scene/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /^Old$/ })).toBeInTheDocument();
});

test("declining the delete confirm does nothing", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(api.deleteScene).not.toHaveBeenCalled();
});

test.each(["success", "list refusal", "scene refusal"])(
  "a campaign switch retires the old list failure and preserves its own %s", async (outcome) => {
    let runReads = 0;
    vi.mocked(api.listScenes).mockImplementation((cid) => {
      if (cid === "run" && ++runReads > 1) return Promise.reject(new Error("A list failed"));
      if (cid === "other" && outcome === "list refusal") return Promise.reject(new Error("B list failed"));
      return Promise.resolve(SCENES_WITH_DATE);
    });
    vi.mocked(api.getScene).mockImplementation((cid) => {
      if (cid === "other" && outcome === "scene refusal") return Promise.reject(new Error("B scene failed"));
      return Promise.resolve({ meta: { id: "s1", title: "Old" }, messages: [] });
    });
    render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<><Here /><ShowState />
        <Link to="/campaigns/other/scenes/s1" state={{ seedPrompt: "Mara at the gate" }}>switch campaign</Link>
        {playRoutes()}</>)}
    </MemoryRouter>);
    await screen.findByTestId("cast-panel");
    fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
    fireEvent.click(screen.getByText("stub-pick"));
    expect(await screen.findByText(/A list failed/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
    await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
    await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("other"));
    if (outcome === "success") {
      await waitFor(() => expect(screen.getByTestId("cast-panel")).toHaveTextContent("Mara at the gate"));
    } else {
      expect(await screen.findByText(outcome === "list refusal" ? /B list failed/ : /B scene failed/)).toBeInTheDocument();
    }
    expect(screen.queryByText(/A list failed/)).not.toBeInTheDocument();
    expect(screen.getByTestId("hist-state")).toHaveTextContent("none");
  },
);

test.each(["delete", "refresh"])("a late %s refusal cannot report in another campaign", async (phase) => {
  let rejectPending: ((reason: Error) => void) | undefined;
  let runReads = 0;
  vi.mocked(api.listScenes).mockImplementation((cid) => {
    if (cid === "run" && ++runReads > 1 && phase === "refresh") {
      return new Promise((_, reject) => { rejectPending = reject; });
    }
    return Promise.resolve(SCENES_WITH_DATE);
  });
  vi.mocked(api.getScene).mockImplementation((cid) => Promise.resolve(fixture({
    meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: `${cid} transcript` }],
  })));
  if (phase === "delete") {
    vi.mocked(api.deleteScene).mockReturnValueOnce(new Promise((_, reject) => { rejectPending = reject; }));
  }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByText("run transcript");
  fireEvent.click(screen.getByRole("button", { name: "Delete scene" }));
  await waitFor(() => expect(rejectPending).toBeDefined());
  fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
  await screen.findByText("other transcript");
  const usageReads = vi.mocked(api.getSceneUsage).mock.calls.length;
  if (!rejectPending) throw new Error("the original request was never pending");
  await act(async () => { rejectPending?.(new Error("original campaign refusal")); });
  expect(screen.queryByText(/original campaign refusal/)).not.toBeInTheDocument();
  expect(screen.getByText("other transcript")).toBeInTheDocument();
  expect(api.getSceneUsage).toHaveBeenCalledTimes(usageReads);
});

test.each(["delete", "refresh"])("a late %s completion cannot refresh another campaign", async (phase) => {
  let finishPending: (() => void) | undefined;
  let runReads = 0;
  vi.mocked(api.listScenes).mockImplementation((cid) => {
    if (cid === "run" && ++runReads > 1 && phase === "refresh") {
      return new Promise((resolve) => { finishPending = () => resolve([]); });
    }
    return Promise.resolve(SCENES_WITH_DATE);
  });
  vi.mocked(api.getScene).mockImplementation((cid) => Promise.resolve(fixture({
    meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: `${cid} transcript` }],
  })));
  if (phase === "delete") {
    vi.mocked(api.deleteScene).mockReturnValueOnce(new Promise((resolve) => {
      finishPending = () => resolve({ ok: true });
    }));
  }
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByText("run transcript");
  fireEvent.click(screen.getByRole("button", { name: "Delete scene" }));
  await waitFor(() => expect(finishPending).toBeDefined());
  fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
  await screen.findByText("other transcript");
  const sceneReads = vi.mocked(api.listScenes).mock.calls.length;
  const usageReads = vi.mocked(api.getSceneUsage).mock.calls.length;
  if (!finishPending) throw new Error("the original request was never pending");
  await act(async () => { finishPending?.(); });
  expect(screen.getByText("other transcript")).toBeInTheDocument();
  expect(api.listScenes).toHaveBeenCalledTimes(sceneReads);
  expect(api.getSceneUsage).toHaveBeenCalledTimes(usageReads);
});

test("an error shows a Retry button that retries the scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  // the server persisted the user turn even though the stream errored —
  // the post-stream re-fetch returns it
  vi.mocked(api.getScene)
    .mockResolvedValueOnce(fixture({ meta: {}, messages: [] }))
    .mockResolvedValue(fixture({ meta: {}, messages: [{ role: "user", content: "hello" }] }));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent(fixture({ error: { detail: "boom" } }));
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const retryBtn = await screen.findByRole("button", { name: /retry/i });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
});

test("a turn refused for its settings offers the page that fixes them, not a Retry", async () => {
  // Retry sent the same request into the same 409.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent({ error: { detail: "Realm Local has no key", kind: "missing_key" } });
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "hello" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  await screen.findByText(/Realm Local has no key/);
  expect(screen.getByRole("link", { name: /Providers page/ })).toHaveAttribute("href", "/providers");
  expect(screen.queryByRole("button", { name: /^retry$/i })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(screen.queryByText(/Realm Local has no key/)).toBeNull();
});

test("where the campaign's scene route cannot answer, Send is disabled and says why", async () => {
  // The library's model can be ready while this campaign's own override has
  // no key, and the other way round: the scene route is the one a turn runs on.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignInference).mockResolvedValue(
    campaignInference({ problem: "No API key is set (Realm Local, routed for scene turns)" }));
  renderCampaign();
  const reason = await screen.findByText(/No model is ready to generate/);
  expect(reason).toHaveTextContent("No API key is set (Realm Local, routed for scene turns)");
  const send = screen.getByRole("button", { name: /continue ▶/i });
  expect(send).toBeDisabled();
  expect(send).toHaveAccessibleDescription(/No model is ready to generate/);
  // The chord the button stands for is held too.
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "hello" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});
