import { screen, fireEvent, within } from "@testing-library/react";
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";

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



// ---- director notes in the transcript ----

const NOTE = "⁣Note";

function sceneWithNote() {
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {},
    messages: [
      { role: "user", content: "I knock." },
      { role: "assistant", content: "make it rain", speaker: NOTE },
      { role: "assistant", content: "Rain begins." },
    ],
  }));
}

test("a scene with no notes grows no toggle for them", async () => {
  renderCampaign();
  await screen.findByText("Scene ⋯");

  expect(screen.queryByRole("button", { name: /director notes/i }))
    .not.toBeInTheDocument();
});

test("director notes are hidden until asked for, and counted on the toggle", async () => {
  // A note is in the transcript so the turn it bought has an index to be
  // charged against — not because it happened in the scene. Read back later,
  // the scene is prose and these are stage directions in the margin.
  sceneWithNote();
  renderCampaign();
  await screen.findByText("Rain begins.");

  expect(screen.queryByText("make it rain")).not.toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Show director notes · 1" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");

  fireEvent.click(toggle);

  expect(await screen.findByText("make it rain")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Hide director notes · 1" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("a hidden note does not renumber the posts around it", async () => {
  // The index a post is edited by is its position in the transcript. Filtering
  // the list before numbering would send an edit to the wrong post — so a note
  // is skipped at render, after its neighbours have their numbers.
  sceneWithNote();
  renderCampaign();
  await screen.findByText("Rain begins.");

  // "Rain begins." is index 2 whether or not the note at index 1 is drawn, so
  // its controls address message 3 (1-based in the label).
  const rain = screen.getByText("Rain begins.").closest(".msg") as HTMLElement;
  // The edit control names the post it acts on. Several controls on the row
  // do, so this asserts they all agree rather than picking one.
  const named = within(rain).getAllByRole("button", { name: /message 3/i });
  expect(named.length).toBeGreaterThan(0);
  // ...and nothing on the row addresses message 2, which is the note.
  expect(within(rain).queryByRole("button", { name: /message 2/i }))
    .not.toBeInTheDocument();
});

test("a shown note is not plated as the narrator", async () => {
  // It is assistant-role by the transcript format's construction, so without
  // its own label it would carry the narrator's plate — the one thing it is
  // definitely not.
  sceneWithNote();
  renderCampaign();
  await screen.findByText("Rain begins.");
  fireEvent.click(screen.getByRole("button", { name: /show director notes/i }));

  const note = (await screen.findByText("make it rain")).closest(".run")!;
  expect(note).toHaveClass("director-note");
  // The rule says what it is; a fourth kind of speaker plate in a column of
  // three would not.
  expect(within(note as HTMLElement).queryByText("Grimoire")).not.toBeInTheDocument();
});


// ---- what the scene has cost, where the reader already is ----

function sceneUsage(over: Record<string, unknown> = {}) {
  const zero = {
    calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
    cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
    modelled_usd: 0, priced_calls: 0, unpriced_calls: 0, subscription_calls: 0,
    modelled_calls: 0, unmetered_calls: 0, duration_ms: 0,
  };
  return {
    campaign: "run", scene: "s1", since: "", until: "", generated_at: "",
    clamped: false, by_task: [], by_post: [], turns: [], listed: 0,
    truncated: false,
    totals: { ...zero, calls: 4, cost_usd: 0.41, priced_calls: 4, ...over },
  };
}

test("the scene bar says what the scene has cost", async () => {
  // It used to be reachable only by opening the inspector, which is a surface
  // you have to leave the scene to open — so nobody opened it.
  vi.mocked(api.getSceneUsage).mockResolvedValue(sceneUsage());
  renderCampaign();

  expect(await screen.findByText(/\$0\.41 SPEND/)).toBeInTheDocument();
});

test("a scene nobody has generated in shows no figure at all", async () => {
  // "Never generated against" is not "cost nothing", and `$0.00` says the
  // second — the one claim the cost surfaces exist not to make.
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    sceneUsage({ calls: 0, cost_usd: 0, priced_calls: 0 }));
  renderCampaign();

  await screen.findByText("Scene ⋯");
  expect(screen.queryByText(/SPEND/)).not.toBeInTheDocument();
  expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
});

test("a scene whose calls nobody priced says so rather than $0.00", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    sceneUsage({ cost_usd: 0, priced_calls: 0, unpriced_calls: 4 }));
  renderCampaign();

  expect(await screen.findByText(/not reported/)).toBeInTheDocument();
});

test("a zero estimate beside calls nobody priced says not reported in the scene head", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    sceneUsage({ calls: 11, cost_usd: 0, priced_calls: 0, modelled_calls: 1,
                 modelled_usd: 0, unpriced_calls: 10 }));
  renderCampaign();

  expect(await screen.findByText(/not reported/)).toBeInTheDocument();
  expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
});

test("an estimate in the scene head is not labelled spend", async () => {
  // A subscription's per-token equivalent is real usage, not money anybody
  // paid -- `≈ $0.40 SPEND` would call it a bill.
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    sceneUsage({ cost_usd: 0, priced_calls: 4, subscription_calls: 4,
                 estimated_usd: 0.4 }));
  renderCampaign();

  expect(await screen.findByText(/≈ \$0\.40/)).toBeInTheDocument();
  expect(screen.queryByText(/SPEND/)).not.toBeInTheDocument();
});

test("the scene head says when the scene's token counts were estimated here", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    sceneUsage({ cost_usd: 0, priced_calls: 0, modelled_calls: 4, modelled_usd: 0.37,
                 estimated_token_calls: 4 }));
  renderCampaign();

  const figure = await screen.findByText(/≈ \$0\.37/);
  expect(figure.closest("span")).toHaveAttribute(
    "title", expect.stringMatching(/4 calls with tokens estimated/));
});

test("a ledger read that failed costs the figure and nothing else", async () => {
  vi.mocked(api.getSceneUsage).mockRejectedValue(new Error("busy"));
  renderCampaign();

  // The transcript is the page; the money is a line on the bar above it.
  expect(await screen.findByText("Scene ⋯")).toBeInTheDocument();
  expect(screen.queryByText(/SPEND/)).not.toBeInTheDocument();
});


test("a shown director note carries what its turn cost", async () => {
  // The whole reason a note is in the transcript at all: it has an index, so
  // the generation it bought has somewhere to sit. A chip gated on user-role
  // left the one line that exists to carry a figure as the only line that
  // could not — a note is assistant-role by the format's construction.
  sceneWithNote();
  vi.mocked(api.getSceneUsage).mockResolvedValue(fixture({
    ...sceneUsage(), by_post: [{
      post: 1, calls: 2, rerolls: 1, total_tokens: 900,
      cost_usd: 0.21, estimated_usd: 0, modelled_usd: 0,
      priced_calls: 2, unpriced_calls: 0, subscription_calls: 0,
      modelled_calls: 0, unmetered_calls: 0,
    }],
  }));
  renderCampaign();
  await screen.findByText("Rain begins.");
  fireEvent.click(screen.getByRole("button", { name: /show director notes/i }));

  const note = (await screen.findByText("make it rain")).closest(".msg-body")!;
  expect(within(note as HTMLElement).getByText("$0.21")).toBeInTheDocument();
  expect(within(note as HTMLElement).getByText(/1 reroll/)).toBeInTheDocument();
});

test("an unstamped player post is plated with the PC's name and portrait", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "elara", role: "player", name: "Elara Vane" },
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" }]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "pcs", id: "elara", version: "default", role: "player" }]));
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "I step off the boat." },
    { role: "assistant", content: "a reply" }] }));
  const { container } = renderCampaign();
  await screen.findByText("I step off the boat.");
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["Elara Vane", "Grimoire"]);
  // the portrait comes from the roster's locked version, like any other actor's
  const avatar = container.querySelector(".plate-avatar img");
  expect(avatar?.getAttribute("src")).toBe("/img/pcs/elara/default/avatar?w=128");
});

test("with two players seated the plate names the first, deterministically", async () => {
  // An unstamped post says which side of the table it came from and no more, so
  // this is a pick rather than a derivation -- matching what the backend renders
  // into the book and into every prompt that quotes the transcript back.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "adair", role: "player", name: "Adair Finch" },
    { kind: "pcs", id: "wren", role: "player", name: "Wren Halloway" }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "I step off the boat." }] }));
  const { container } = renderCampaign();
  await screen.findByText("I step off the boat.");
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["Adair Finch"]);
});

test("with nobody seated the plate keeps the reserved label", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "I step off the boat." }] }));
  const { container } = renderCampaign();
  await screen.findByText("I step off the boat.");
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["You"]);
});
