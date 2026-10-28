import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { InferenceSettings, ResolvedSelection } from "../api/client";
import { configChanged } from "../appEvents";
import {
  campaignInference,
  installCampaignMocks,
  ONE_SCENE,
  playRoutes,
  renderCampaign,
  shellModel,
  shellReady,
  withPalette,
 } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { PROPOSAL_PAYLOAD } from "../testkit/campaignFixtures";

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



test.each([
  { assistant: "Grimoire", speaker: "Grimoire", actor_ref: undefined },
  { assistant: "Seraphine", speaker: "Seraphine", actor_ref: undefined },
  { assistant: "Winifred", speaker: "Mara", actor_ref: "grimoire" },
])("a narrator plate says narrator with the $assistant label", async ({ assistant, speaker, actor_ref }) => {
  vi.mocked(api.getCampaignSettings).mockResolvedValue(labelSettings("Mara", assistant));
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "The tide turns.", speaker, ...(actor_ref === undefined ? {} : { actor_ref }) },
  ] });
  const { container } = renderCampaign();
  await screen.findByText("The tide turns.");
  expect(container.querySelector(".plate .role-chip")).toHaveTextContent(/^narrator$/);
});

test.each(["characters:mara", "pcs:mara"])(
  "a departed actor reference %s stays an actor when its label matches the narrator", async (actor_ref) => {
    vi.mocked(api.getCampaignSettings).mockResolvedValue(labelSettings("Winifred", "Mara"));
    vi.mocked(api.getCast).mockResolvedValue([]);
    vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
      { role: "assistant", content: "Mara waits.", speaker: "Mara", actor_ref },
    ] });
    const { container } = renderCampaign();
    await screen.findByText("Mara waits.");
    expect(container.querySelector(".plate .role-chip")).toHaveTextContent(/^npc$/);
  });

test("a known missing avatar renders initials in both the cast and the transcript", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", role: "npc", name: "Mara" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue([
    { kind: "characters", id: "mara", version: "default", role: "npc", scenes: ["s1"],
      appeared: true, avatar_v: null },
  ]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "Mara waits.", speaker: "Mara", actor_ref: "characters:mara" },
  ] });
  const { container } = renderCampaign();
  await screen.findByText("Mara waits.");
  expect(screen.queryByAltText("Mara portrait")).not.toBeInTheDocument();
  expect(container.querySelector(".plate .portrait-initials")).toHaveTextContent("M");
  expect(container.querySelector(".cast-tile .portrait-initials")).toHaveTextContent("M");
});

function labelSettings(user_label = "Mara", assistant_label = "Seraphine") {
  const effective = { context_scan_depth: "8", context_budget: "12000", recap_depth: "5", archive_depth: "3",
    rolling_summary_every: "10", scene_break_every: "6", system_prompt: "", user_label, assistant_label };
  return { overrides: {}, inherited: effective, effective };
}

test("campaign settings supply effective transcript labels and refresh on config changes", async () => {
  vi.mocked(api.getCampaignSettings).mockResolvedValue(labelSettings());
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
  ] }));
  const { container } = renderCampaign();
  await screen.findByText("a reply");
  const names = () => [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names()).toEqual(["Mara", "Seraphine"]);
  vi.mocked(api.getCampaignSettings).mockResolvedValue(labelSettings("Winifred"));
  act(() => { configChanged(); });
  await waitFor(() => expect(names()).toEqual(["Winifred", "Seraphine"]));
});

test("a failed campaign label read retains global display labels", async () => {
  vi.mocked(api.getCampaignSettings).mockRejectedValue(new Error("offline"));
  vi.mocked(api.getConfig).mockResolvedValue(fixture({ quote_color: "off", user_label: "Mara", assistant_label: "Seraphine" }));
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
  ] }));
  const { container } = renderCampaign();
  await screen.findByText("a reply");
  expect(api.getCampaignSettings).toHaveBeenCalledWith("run");
  expect([...container.querySelectorAll(".plate-name")].map((n) => n.textContent)).toEqual(["Mara", "Seraphine"]);
});

test("late campaign labels cannot replace the current campaign or a fresher config read", async () => {
  let oldCampaign!: (value: ReturnType<typeof labelSettings>) => void;
  let oldRefresh!: (value: ReturnType<typeof labelSettings>) => void;
  vi.mocked(api.getCampaignSettings).mockReturnValueOnce(new Promise((r) => { oldCampaign = r; }))
    .mockResolvedValueOnce(labelSettings("Winifred"));
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
  ] }));
  const { container } = render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
  const names = () => [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  await waitFor(() => expect(names()).toEqual(["Winifred", "Seraphine"]));
  await act(async () => { oldCampaign(labelSettings("Mara")); });
  expect(names()).toEqual(["Winifred", "Seraphine"]);
  vi.mocked(api.getCampaignSettings).mockReturnValueOnce(new Promise((r) => { oldRefresh = r; }))
    .mockResolvedValueOnce(labelSettings("Seraphine", "Mara"));
  act(() => { configChanged(); });
  act(() => { configChanged(); });
  await waitFor(() => expect(names()).toEqual(["Seraphine", "Mara"]));
  await act(async () => { oldRefresh(labelSettings("Winifred")); });
  expect(names()).toEqual(["Seraphine", "Mara"]);
});

/** The campaign's inference view with its scene route resolved as given. */
function sceneRouteResolves(resolves: ResolvedSelection | null, problem: string | null = null) {
  vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({ resolves, problem }));
}

test("the header shows the resolved scene model", async () => {
  sceneRouteResolves({ provider: "saltmarch", provider_name: "Saltmarch Router",
                       model: "vendor/haiku", preset: "", preset_name: "",
                       via: "role", scope: "campaign" });
  renderCampaign(undefined, { shell: true });
  // The page is the only thing that can answer: the header has a pathname and
  // no cid, and what a scene turn runs on is the resolver's answer for THIS
  // campaign -- the scene route's `resolves`, not the library's chat model.
  await waitFor(() => expect(shellModel()).toBe("vendor/haiku"));
  expect(api.getCampaignInference).toHaveBeenCalledWith("run");
  expect(shellReady()).toBe("true");
});

test("the header's readiness is the scene route's problem", async () => {
  sceneRouteResolves(
    { provider: "realm", provider_name: "Realm Local", model: "realm/small", preset: "",
      preset_name: "", via: "route", scope: "campaign" },
    "No API key is set (Realm Local, routed for scene turns)");
  renderCampaign(undefined, { shell: true });
  await waitFor(() => expect(shellModel()).toBe("realm/small"));
  expect(shellReady()).toBe("false");
});

test.each(["ready", "unready", "failed"])(
  "generation waits for a pending campaign readiness read that becomes %s", async (answer) => {
    let land!: (view: InferenceSettings) => void;
    let refuse!: (error: Error) => void;
    vi.mocked(api.getCampaignInference).mockReturnValue(new Promise((resolve, reject) => {
      land = resolve; refuse = reject;
    }));
    renderCampaign();
    await screen.findByRole("heading", { name: /^Old$/ });
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Mara waits." } });
    const send = screen.getByRole("button", { name: /send/i });
    expect(send).toBeDisabled();
    fireEvent.click(send);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(api.chat).not.toHaveBeenCalled();
    await act(async () => {
      if (answer === "failed") refuse(new Error("offline"));
      else land(campaignInference({ problem: answer === "unready" ? "No API key is set" : null }));
    });
    if (answer === "unready") expect(send).toBeDisabled();
    else {
      expect(send).toBeEnabled();
      fireEvent.click(send);
      await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(1));
    }
  });

test("generation waits for refreshed campaign readiness while its cached header model stays visible", async () => {
  renderCampaign(undefined, { shell: true });
  await waitFor(() => expect(shellModel()).toBe("campaign/model"));
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "Mara waits." } });
  expect(screen.getByRole("button", { name: /send/i })).toBeEnabled();
  let land!: (view: InferenceSettings) => void;
  vi.mocked(api.getCampaignInference).mockReturnValue(new Promise((resolve) => { land = resolve; }));
  act(() => { configChanged(); });
  const send = screen.getByRole("button", { name: /send/i });
  expect(shellModel()).toBe("campaign/model");
  expect(send).toBeDisabled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(api.chat).not.toHaveBeenCalled();
  await act(async () => { land(campaignInference({ problem: "No API key is set" })); });
  expect(send).toBeDisabled();
});

test("a new campaign cannot inherit the previous campaign's generation permission", async () => {
  let land!: (view: InferenceSettings) => void;
  vi.mocked(api.getCampaignInference).mockImplementation((cid) => cid === "run"
    ? Promise.resolve(campaignInference()) : new Promise((resolve) => { land = resolve; }));
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByRole("heading", { name: /^Old$/ });
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
  await waitFor(() => expect(api.getCampaignInference).toHaveBeenCalledWith("other"));
  const generate = screen.getByRole("button", { name: /continue ▶/i });
  expect(generate).toBeDisabled();
  fireEvent.click(generate);
  expect(api.chat).not.toHaveBeenCalled();
  await act(async () => { land(campaignInference()); });
  expect(generate).toBeEnabled();
});

test.each(["quick send", "reroll response", "keep writing", "generate variant"])(
  "campaign scene readiness blocks %s while stored response actions remain available", async (action) => {
    sceneRouteResolves(null, "The scene provider has no key");
    vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
    vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
      { role: "user", content: "Mara waits." },
      { role: "assistant", content: "The tide turns.", response_id: "r1",
        response_status: "complete", response_can_reroll: true },
    ] });
    vi.mocked(api.getResponseSwipe).mockResolvedValue({
      active: 1, variants: [{ id: "v1", status: "complete" }, { id: "v2", status: "complete" }],
      settings: null, resume_settings: null, can_reroll: true, editable: true,
      round_open: false, edited: false,
    });
    vi.mocked(api.getEffectiveQuickReplies).mockResolvedValue({ replies: [
      { id: "look", label: "Look around", kind: "send", text: "I look around.", mode: "send" },
    ] });
    renderCampaign();
    await screen.findByText(/No model is ready to generate/);
    await screen.findByText("The tide turns.");
    await screen.findByRole("button", { name: "Generate a new reply variant" });
    fireEvent.click(screen.getByText("Response actions"));
    const name = action === "quick send" ? "Look around"
      : action === "reroll response" ? "Reroll response"
      : action === "keep writing" ? /Keep writing/ : "Generate a new reply variant";
    const button = screen.getByRole("button", { name });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(api.chat).not.toHaveBeenCalled();
    expect(api.regenerateResponse).not.toHaveBeenCalled();
    expect(api.extendResponse).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Delete response" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Previous reply variant" })).toBeEnabled();
  });

test("an unready scene route keeps the reroll picker open but blocks its default send", async () => {
  sceneRouteResolves(null, "The scene provider has no key");
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "Mara waits." }, { role: "assistant", content: "The tide turns." },
  ] });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  await screen.findByText("The tide turns.");
  expect(screen.getByTitle("Reroll")).toBeEnabled();
  fireEvent.keyDown(window, { key: "r" });
  const guidance = await screen.findByLabelText("Reroll guidance");
  const submit = screen.getByRole("button", { name: /reroll ▸/i });
  expect(submit).toBeDisabled();
  fireEvent.click(submit);
  fireEvent.keyDown(guidance, { key: "Enter" });
  expect(api.regenerate).not.toHaveBeenCalled();
});

test("campaign scene readiness keeps a roll proposal pending until narration can run", async () => {
  sceneRouteResolves(null, "The scene provider has no key");
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: {
    id: "pr-1", status: "pending", payload: { ...PROPOSAL_PAYLOAD, available: {} }, resolution: null,
  } });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  const roll = await screen.findByRole("button", { name: "Roll it" });
  expect(roll).toBeDisabled();
  fireEvent.click(roll);
  expect(api.resolveProposal).not.toHaveBeenCalled();
  expect(roll).toBeInTheDocument();
});

test("an unready scene still lets a pending proposal's local edits be staged", async () => {
  sceneRouteResolves(null, "The scene provider has no key");
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: {
    id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null,
  } });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  const modify = await screen.findByRole("button", { name: "Modify" });
  expect(modify).toBeEnabled();
  fireEvent.click(modify);
  const difficulty = screen.getByLabelText("Difficulty");
  expect(difficulty).toBeEnabled();
  fireEvent.change(difficulty, { target: { value: "8" } });
  expect(difficulty).toHaveValue(8);
  expect(screen.getByRole("button", { name: "Roll it" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Decline" })).toBeDisabled();
  expect(api.resolveProposal).not.toHaveBeenCalled();
  sceneRouteResolves(null);
  act(() => { configChanged(); });
  const roll = screen.getByRole("button", { name: "Roll it" });
  await waitFor(() => expect(roll).toBeEnabled());
  fireEvent.click(roll);
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ difficulty: 8 }), expect.any(Function),
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test.each(["auth", "missing_dependency"])("configuration failures %s offer no identical Retry", async (kind) => {
  vi.mocked(api.chat).mockImplementation(async (_cid, _sid, _text, onEvent) => {
    onEvent({ error: { detail: "Fix the configured provider first", kind } });
  });
  renderCampaign();
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send/i }));
  await screen.findByText("Fix the configured provider first");
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  fireEvent.keyDown(window, { key: "t" });
  expect(api.retry).not.toHaveBeenCalled();
});

test("a failed model read leaves the header to the library's answer", async () => {
  vi.mocked(api.getCampaignInference).mockRejectedValue(new Error("offline"));
  renderCampaign(undefined, { shell: true });
  await waitFor(() => expect(api.getCampaignInference).toHaveBeenCalledWith("run"));
  expect(shellModel()).toBe("null");
  expect(shellReady()).toBe("null");
});

test("a config change keeps the header's scene model until the re-read lands", async () => {
  renderCampaign(undefined, { shell: true });
  await waitFor(() => expect(shellModel()).toBe("campaign/model"));

  let land!: (view: InferenceSettings) => void;
  vi.mocked(api.getCampaignInference).mockReturnValue(new Promise((r) => { land = r; }));
  act(() => { configChanged(); });
  // Asked again, and still showing this campaign's answer meanwhile -- not
  // the library's for as long as the read takes.
  expect(api.getCampaignInference).toHaveBeenCalledTimes(2);
  expect(shellModel()).toBe("campaign/model");
  expect(shellReady()).toBe("true");

  await act(async () => {
    land(campaignInference({ resolves: { provider: "openrouter", provider_name: "OpenRouter",
                                         model: "campaign/other", preset: "", preset_name: "",
                                         via: "role", scope: "campaign" } }));
  });
  await waitFor(() => expect(shellModel()).toBe("campaign/other"));
});

test("the pinned conditions block names where, when and the campaign's world copy", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-03", friendly: "3 July 2026", weekday: "Friday",
               secondary_friendly: null, holidays_today: ["Independence Day"], upcoming: null, cast: [] },
    history: [],
  }));
  vi.mocked(api.getSceneLocation).mockResolvedValue({
    current: { id: "tideflats", name: "The Tideflats" }, visited: [] });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  await column.findByText(/Friday 3 July 2026/i);
  expect(column.getByText("The Tideflats")).toBeInTheDocument();
  // The campaign's own copy of the world, said before the click rather than
  // after: edits there reach this campaign only.
  expect(column.getByRole("link", { name: /this campaign/i }))
    .toHaveAttribute("href", "/campaigns/run/world/characters");
});

test("the scene heading counts the scene and its turns", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  // `find`, not `get`: the title comes from the scene *list* and the turn count
  // from the separate `getScene`, so waiting for the heading proves nothing
  // about the count. On a fast machine the second promise happens to have
  // settled by now; on a loaded CI runner it has not, and this failed against
  // "SCENE 1 · 0 TURNS". Wait for the thing being asserted.
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(await screen.findByText(/SCENE 1 · 2 TURNS/i)).toBeInTheDocument();
});
