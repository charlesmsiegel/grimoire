import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { FocusProvider, useFocus } from "../components/focus";
import { api } from "../api/client";
import type { CampaignBudget } from "../api/client";
import { Here, installCampaignMocks, ONE_SCENE, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";

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



// ---- provenance in the play view (4a) ----

test("a dossier row carries its citation, and hovering it lights the transcript line", async () => {
  // The claim of the whole screen: every continuity line is something you can
  // check, and checking it costs nothing you were holding.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "I'd rather the mud than his company.", speaker: "Sister Aud" },
    { role: "assistant", content: "The tide had been going out since before dawn." },
  ] });
  vi.mocked(api.getCasefile).mockResolvedValue({
    kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
    scenes: [{ id: "s1", title: "Old" }], last_seen: "Old",
    standing: "Guarded. Will not be alone with the Reeve.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  vi.mocked(api.campaignProvenance).mockResolvedValue({
    "characters/aud#current_state": {
      quote: "I'd rather the mud than his company.", speaker: "Sister Aud",
      certainty: 0.92, authority: "self", band: "high", scene: "s1",
      recorded: "2026-08-13T10:00:00Z",
    },
  });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(await column.findByText("Sister Aud"));

  const marker = await screen.findByRole("button", { name: /^Standing: Cited/ });
  fireEvent.focus(marker);
  expect(screen.getByText(/CERTAINTY 0.92/)).toBeInTheDocument();

  // the post the quote came from lights up, and only that one
  await waitFor(() => expect(document.querySelectorAll(".msg.cited")).toHaveLength(1));
  expect((document.querySelector(".msg.cited") as HTMLElement).textContent)
    .toMatch(/rather the mud/);

  fireEvent.blur(marker);
  await waitFor(() => expect(document.querySelectorAll(".msg.cited")).toHaveLength(0));
});

test("a citation of a rule's text lights the post, and Go to turn reaches it", async () => {
  // The quote exists only in `shown`: a rule rewrote "mud" to "marsh" for
  // both the screen and the prompt the reviewer judged.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "I'd rather the mud than his company.",
      shown: "I'd rather the marsh than his company.", speaker: "Sister Aud" },
    { role: "assistant", content: "The tide had been going out since before dawn." },
  ] });
  vi.mocked(api.getCasefile).mockResolvedValue({
    kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
    scenes: [{ id: "s1", title: "Old" }], last_seen: "Old",
    standing: "Guarded. Will not be alone with the Reeve.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  vi.mocked(api.campaignProvenance).mockResolvedValue({
    "characters/aud#current_state": {
      quote: "I'd rather the marsh than his company.", speaker: "Sister Aud",
      certainty: 0.92, authority: "self", band: "high", scene: "s1",
      recorded: "2026-08-13T10:00:00Z",
    },
  });
  // jsdom may not define it at all, so it is installed and put back by
  // descriptor rather than spied on.
  const scrolled = vi.fn();
  const before = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
  Object.defineProperty(Element.prototype, "scrollIntoView",
                        { value: scrolled, configurable: true, writable: true });
  try {
    renderCampaign();
    const column = within(await screen.findByRole("complementary"));
    fireEvent.click(await column.findByText("Sister Aud"));

    const marker = await screen.findByRole("button", { name: /^Standing: Cited/ });
    fireEvent.focus(marker);
    await waitFor(() => expect(document.querySelectorAll(".msg.cited")).toHaveLength(1));
    expect((document.querySelector(".msg.cited") as HTMLElement).textContent)
      .toMatch(/rather the marsh/);

    fireEvent.mouseDown(screen.getByRole("button", { name: /Go to turn/ }));
    await waitFor(() => expect(scrolled).toHaveBeenCalled());
  } finally {
    if (before) Object.defineProperty(Element.prototype, "scrollIntoView", before);
    else delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  }
});

test("a campaign whose provenance cannot be read shows uncited rows, not an error", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  vi.mocked(api.campaignProvenance).mockRejectedValue(new Error("boom"));
  vi.mocked(api.getCasefile).mockResolvedValue({
    kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
    scenes: [{ id: "s1", title: "Old" }], last_seen: "Old", standing: "Guarded.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(await column.findByText("Sister Aud"));
  expect(await screen.findByRole("button", { name: /^Standing: No citation/ }))
    .toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

// ---- the absorb review's three panes (4c) ----

// ---- focus mode ----

/** Stands in for the app header's FOCUS button, which is not on this screen. */
function EnterFocus() {
  const { setFocus } = useFocus();
  return <button onClick={() => setFocus(true)}>enter-focus</button>;
}

function renderFocusable() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      <FocusProvider>
        {withPalette(<><EnterFocus /><Here />{playRoutes()}</>)}
      </FocusProvider>
    </MemoryRouter>,
  );
}

test("focus mode leaves the transcript and the composer, and nothing above them", async () => {
  localStorage.clear();
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(screen.getByRole("button", { name: /End scene/ })).toBeInTheDocument();

  fireEvent.click(screen.getByText("enter-focus"));

  // The scene bar: eleven controls that wrap into four rows at 375px.
  expect(screen.queryByRole("button", { name: /End scene/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /^Ledger$/ })).toBeNull();
  // The scene head: its title, its turn count and its rename/delete pair.
  expect(screen.queryByRole("heading", { name: /^Old$/ })).toBeNull();
  expect(screen.queryByText(/SCENE 1 ·/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /rename scene/i })).toBeNull();

  // What is left is what the mode is for.
  expect(within(screen.getByTestId("stream")).getByText("a reply")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /continue ▶|send ▸/i })).toBeInTheDocument();
});

test("a panel the scene bar opened does not outlive the bar that closes it", async () => {
  localStorage.clear();
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });

  fireEvent.click(screen.getByRole("button", { name: /^Calendar$/ }));
  expect(await screen.findByTestId("calendar-config")).toBeInTheDocument();

  // Its Close is the same bar button, so leaving it up would strand a panel
  // above the transcript with nothing that could shut it.
  fireEvent.click(screen.getByText("enter-focus"));
  expect(screen.queryByTestId("calendar-config")).toBeNull();
});

test("the inspector stays reachable in focus mode, because its toggle does", async () => {
  localStorage.clear();
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByText("enter-focus"));

  // The composer is the input area focus mode exists to keep, and this link is
  // part of it — so unlike the bar's five panels this one can be shut again.
  fireEvent.click(screen.getByRole("button", { name: /what the model saw/i }));
  expect(await screen.findByRole("button", { name: /hide what the model saw/i }))
    .toBeInTheDocument();
});

test("the scene bar opens the incoming-changes review, and closes it again", async () => {
  vi.mocked(api.getIncoming).mockResolvedValue([
    { ref: { kind: "locations", id: "saltmarch-harbor" }, status: "conflict",
      world: { name: "Saltmarch Harbor", body: "The harbour is blockaded." },
      mine: { name: "Saltmarch Harbor", body: "A busy port town." } },
  ]);
  renderCampaign();
  await screen.findByRole("button", { name: "World updates" });

  fireEvent.click(screen.getByRole("button", { name: "World updates" }));
  expect(await screen.findByRole("heading", { name: "Incoming world changes" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /Saltmarch Harbor/ })).toBeInTheDocument();

  // The menu item ticks to say which panel is showing, and the bar grows the
  // one control the menu cannot carry: a Close that names what it will close,
  // because a panel above the transcript whose only control is behind a
  // disclosure costs two clicks and a hunt for the tick.
  expect(screen.getByRole("button", { name: /World updates ✓/ }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Close World updates" }));
  expect(screen.queryByRole("heading", { name: "Incoming world changes" })).toBeNull();
});

test("the scene bar opens the composition overview, and its banner opens the review on that ref", async () => {
  // Two panels, two reads: the composition reads its own endpoint (#71), the
  // review it opens still reads `/incoming` for the diff.
  vi.mocked(api.getComposition).mockResolvedValue({ rows: [
    { ref: { kind: "locations", id: "saltmarch-harbor" }, name: "Saltmarch Harbor",
      state: "conflict", pinned: false, lock: null },
  ] });
  vi.mocked(api.getIncoming).mockResolvedValue([
    { ref: { kind: "locations", id: "saltmarch-harbor" }, status: "conflict",
      world: { name: "Saltmarch Harbor", body: "The harbour is blockaded." },
      mine: { name: "Saltmarch Harbor", body: "A busy port town." } },
  ]);
  renderCampaign();
  await screen.findByRole("button", { name: "Composition" });

  fireEvent.click(screen.getByRole("button", { name: "Composition" }));
  expect(await screen.findByRole("heading", { name: "Composition" })).toBeInTheDocument();

  // The banner does not accept anything: it opens the panel that shows what the
  // change actually is, already on the ref the reader was looking at.
  fireEvent.click(screen.getByRole("button", { name: "Review world updates" }));
  expect(await screen.findByRole("heading", { name: "Incoming world changes" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 3 }).textContent).toContain("Saltmarch Harbor");
});

test("accepting in the review re-reads the composition open beside it", async () => {
  // Both panels are open and both read `/incoming`, so a resolve that only
  // refreshed the review would leave the composition rows and its pending count
  // describing a change that no longer exists.
  vi.mocked(api.getComposition).mockResolvedValue({ rows: [
    { ref: { kind: "locations", id: "saltmarch-harbor" }, name: "Saltmarch Harbor",
      state: "update", pinned: false, lock: null },
  ] });
  vi.mocked(api.getIncoming).mockResolvedValue([
    { ref: { kind: "locations", id: "saltmarch-harbor" }, status: "update",
      world: { name: "Saltmarch Harbor", body: "The harbour is blockaded." },
      mine: { name: "Saltmarch Harbor", body: "The harbour is blockaded." } },
  ]);
  vi.mocked(api.acceptIncoming).mockResolvedValue({ ok: true });
  renderCampaign();
  await screen.findByRole("button", { name: "Composition" });
  fireEvent.click(screen.getByRole("button", { name: "Composition" }));
  expect(await screen.findByText(/1 update pending/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review world updates" }));
  await screen.findByRole("heading", { name: "Incoming world changes" });

  vi.mocked(api.getComposition).mockResolvedValue({ rows: [] });
  vi.mocked(api.getIncoming).mockResolvedValue([]);
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() =>
    expect(screen.queryByText(/1 update pending/)).not.toBeInTheDocument());
  expect(await screen.findByText(/A record joins the composition/)).toBeInTheDocument();
});

// ---- the campaign budget banner (#153) ----
const OVER_BUDGET: CampaignBudget = {
  limit_usd: 10, period: "monthly", level: "over", warn_fraction: 0.8,
  since: "2026-08-01", until: "2026-08-14", spent_usd: 12.5, estimated_usd: 0,
  unpriced_calls: 0, calls: 40, fraction: 1.25,
};

test("a campaign past its budget says so above the scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignBudget).mockResolvedValue(OVER_BUDGET);
  renderCampaign();

  expect(await screen.findByText(/Over budget: \$12\.50 of \$10\.00 spent this month/))
    .toBeInTheDocument();
});

test("a campaign approaching its budget is warned before it is broken", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { ...OVER_BUDGET, level: "warn", spent_usd: 8.5, fraction: 0.85 });
  renderCampaign();

  expect(await screen.findByText(/Approaching budget: \$8\.50 of \$10\.00/)).toBeInTheDocument();
});

test("a campaign with no budget gets no banner", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Run One");

  expect(screen.queryByText(/budget/i)).not.toBeInTheDocument();
});

test("dismissing the warning does not swallow the one that says it is gone", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { ...OVER_BUDGET, level: "warn", spent_usd: 8.5, fraction: 0.85 });
  renderCampaign();

  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
  expect(screen.queryByText(/Approaching budget/)).not.toBeInTheDocument();

  // The same campaign, now past the cap after one more turn: a different fact,
  // so it is said again. A turn is what re-reads the budget -- the banner is
  // keyed on the same bump the inspector's panels are, not on a timer.
  vi.mocked(api.getCampaignBudget).mockResolvedValue(OVER_BUDGET);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  expect(await screen.findByText(/Over budget/)).toBeInTheDocument();
});

test("a budget lookup that fails leaves no banner and no error over the scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignBudget).mockRejectedValue(new Error("nope"));
  renderCampaign();
  await screen.findByText("Run One");

  expect(screen.queryByText(/budget/i)).not.toBeInTheDocument();
});

test("the budget banner says where the budget can be changed", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCampaignBudget).mockResolvedValue(OVER_BUDGET);
  renderCampaign();

  expect(await screen.findByText(/Change it in the inspector’s Cost section\./))
    .toBeInTheDocument();
});
