import { render, screen, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// The three settings panels are driven by their own suites; here they only
// have to be REACHABLE, which is the bug this covers.
vi.mock("../components/MechanicsConfig", () => ({
  default: () => <div data-testid="mechanics-panel" />,
}));
vi.mock("../components/CalendarConfig", () => ({
  CalendarConfig: () => <div data-testid="calendar-panel" />,
}));
vi.mock("../components/CampaignCover", () => ({
  CampaignCover: () => <div data-testid="cover-panel" />,
}));

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    getShell: vi.fn(),
    listScenes: vi.fn(),
    getChronicle: vi.fn(),
    // The second wave: worth having, not worth waiting for.
    listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(),
    campaignChanges: vi.fn(),
    getCampaignBudget: vi.fn(),
    listSceneIdeas: vi.fn(),
    actorImageUrl: vi.fn(() => "/img"),
  },
}));

import { api } from "../api/client";
import CampaignHub from "./CampaignHub";

const META = { id: "run", name: "Run One", world: "w", world_name: "Saltmarch",
               created: "", updated: "", scenes: 3, last_scene: "", absorbed: 1 };

function shell(over: Record<string, unknown> = {}) {
  return {
    campaigns: 2, todo: null,
    campaign: {
      id: "run", name: "Run One", world_name: "Saltmarch", scenes: 3,
      open: [], ledger_open: 0, sheets: null, unreviewed: 0, pending: [],
      images_undescribed: null,
      money: {
        calls: 0, cost_usd: 0, estimated_usd: 0, modelled_usd: 0,
        unpriced_calls: 0, unmetered_calls: 0, subscription_calls: 0,
        modelled_calls: 0, priced_calls: 0, total_tokens: 0, partial: false,
      },
      ...over,
    },
  };
}

/** The money block, filled in for a campaign that has actually been played. */
function withMoney(over: Record<string, unknown>) {
  return shell({
    money: {
      calls: 6, cost_usd: 4.82, estimated_usd: 1.1, modelled_usd: 0.36,
      unpriced_calls: 0, unmetered_calls: 0, subscription_calls: 2,
      modelled_calls: 1, priced_calls: 5, total_tokens: 9000,
      partial: false, ...over,
    },
  });
}

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes><Route path="/campaigns/:cid" element={<CampaignHub />} /></Routes>
    </MemoryRouter>);
}

beforeEach(() => {
  (api.getCampaign as any).mockResolvedValue({ meta: META, body: "" });
  (api.getShell as any).mockResolvedValue(shell());
  (api.listScenes as any).mockResolvedValue([
    { id: "s3", title: "The third", done: false, model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "The second", done: true, model: "", created: "", updated: "", date: "" },
  ]);
  (api.getChronicle as any).mockResolvedValue([
    { sid: "s2", one_line: "It ended.", summary: "It ended, and the tide went out." },
  ]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.campaignChanges as any).mockResolvedValue([]);
  (api.getCampaignBudget as any).mockResolvedValue({ level: "off", limit_usd: 0,
                                                     period: "monthly" });
  (api.listSceneIdeas as any).mockResolvedValue([]);
});

test("the layout picker is gone; the hub is one column", async () => {
  // Three layouts of the same cards is a setting the reader has to have an
  // opinion about before they can read the page.
  renderHub();
  await screen.findByText("Run One");
  expect(screen.queryByText(/two column/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/^cards$/i)).not.toBeInTheDocument();
});

test("the hub leads with what to do next, not with state", async () => {
  (api.getShell as any).mockResolvedValue(
    shell({ open: [{ sid: "s3", title: "The third", turns: null }] }));
  renderHub();
  await screen.findByText("Run One");
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByText("The third")).toBeInTheDocument();
  expect(within(next).getByRole("link", { name: /continue scene/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s3");
});

test("with several scenes open it offers each rather than resuming one", async () => {
  // The behaviour this whole page replaced: opening a campaign used to navigate
  // straight to whichever scene was played last. Picking one inside a card
  // would be the same mistake with a nicer border.
  (api.getShell as any).mockResolvedValue(shell({
    open: [{ sid: "s3", title: "The third", turns: null },
            { sid: "s4", title: "The fourth", turns: null }],
  }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText(/pick one rather than being sent to whichever was last/i))
    .toBeInTheDocument();
  // Scoped to Next up: the Scenes card lists the same scenes further down, and
  // a bare query would match both and prove neither.
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByRole("link", { name: /the third/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s3");
  expect(within(next).getByRole("link", { name: /the fourth/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s4");
  // ...and no single "continue", which would be a pick the reader did not make.
  expect(screen.queryByRole("link", { name: /continue scene/i })).not.toBeInTheDocument();
});

test("starting a scene is offered whether or not one is open", async () => {
  // The regression this covers: the new-scene link used to render only in the
  // empty branch, so a reader with a scene in flight could not start another
  // from the front door -- which is the one thing a front door is for.
  (api.getShell as any).mockResolvedValue(
    shell({ open: [{ sid: "s3", title: "The third", turns: null }] }));
  renderHub();
  await screen.findByText("Run One");
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByRole("link", { name: /new scene/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes");
  // ...and it stays secondary: Continue is the offer, not an alternative to it.
  expect(within(next).getByRole("link", { name: /new scene/i }))
    .not.toHaveClass("hub-primary");
});

test("with nothing open the new-scene link is the primary offer", async () => {
  // Same control, same destination, one name -- its weight follows what else
  // the panel has to offer, and here there is nothing else.
  renderHub();
  await screen.findByText("Run One");
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByRole("link", { name: /new scene/i }))
    .toHaveClass("hub-primary");
});

test("unreviewed proposals are named as holding the world back", async () => {
  (api.getShell as any).mockResolvedValue(
    shell({ unreviewed: 8, pending: [{ sid: "s2", proposals: 8 }] }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("Waiting on you")).toBeInTheDocument();
  expect(screen.getByText(/8 proposals/)).toBeInTheDocument();
});

test("each waiting scene is named on its own wrap-up link", async () => {
  // Two pending reviews used to render two buttons reading the same four
  // words: a choice with nothing to choose between, and no way to tell which
  // scene was absorbed short of opening one. The sids travel in the payload
  // for exactly this, and the scene list the page already fetches has the
  // titles.
  (api.getShell as any).mockResolvedValue(shell({
    unreviewed: 5,
    pending: [{ sid: "s2", proposals: 3 }, { sid: "s3", proposals: 2 }],
  }));
  renderHub();
  await screen.findByText("Run One");
  const waiting = screen.getByText("Waiting on you").closest("section")!;
  expect(within(waiting).getByRole("link", { name: /wrap up the second/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s2");
  expect(within(waiting).getByRole("link", { name: /wrap up the third/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s3");
  // The prose counts the scenes too, rather than saying "a scene" over two.
  expect(within(waiting).getByText(/2 scenes were absorbed/)).toBeInTheDocument();
});

test("a waiting scene the list cannot name still offers its wrap-up", async () => {
  // A review sidecar can outlive the scene it belongs to. The route still
  // answers, so the offer stands -- unnamed rather than invented.
  (api.getShell as any).mockResolvedValue(
    shell({ unreviewed: 2, pending: [{ sid: "gone", proposals: 2 }] }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByRole("link", { name: /open wrap-up/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/gone");
});

test("the to-do card renders a zero and links at the list", async () => {
  // `0` is an answer -- nothing outstanding -- and the card says so with the
  // count showing, which is the half of the rule that is easy to lose by
  // writing a truthiness test.
  (api.getShell as any).mockResolvedValue({ ...shell(), todo: 0 });
  renderHub();
  await screen.findByText("Run One");
  const card = screen.getByText("To do").closest("section")!;
  expect(within(card).getByText("0")).toBeInTheDocument();
  expect(within(card).getByText(/nothing outstanding/i)).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: /everything noticed/i }))
    .toHaveAttribute("href", "/todo");
});

test("a to-do count nobody computed draws no count at all", async () => {
  // The other half: `null` means the field was never answered, and a zero
  // there would read as a measurement nobody made. Same sentence as the cost
  // rule one domain over.
  (api.getShell as any).mockResolvedValue({ ...shell(), todo: null });
  renderHub();
  await screen.findByText("Run One");
  const card = screen.getByText("To do").closest("section")!;
  expect(within(card).queryByText("0")).not.toBeInTheDocument();
  expect(within(card).getByText(/no count was reported/i)).toBeInTheDocument();
});

test("the to-do card carries what is still outstanding", async () => {
  (api.getShell as any).mockResolvedValue({ ...shell(), todo: 4 });
  renderHub();
  await screen.findByText("Run One");
  const card = screen.getByText("To do").closest("section")!;
  expect(within(card).getByText("4")).toBeInTheDocument();
  expect(within(card).getByText(/4 still to answer/i)).toBeInTheDocument();
});

test("nothing waiting reads as an answer, not as an empty banner", async () => {
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("Nothing waiting")).toBeInTheDocument();
  expect(screen.queryByText("Waiting on you")).not.toBeInTheDocument();
});

test("a review holding no proposals is still a scene waiting", async () => {
  // `unreviewed` is the SUM of proposals across the sidecars, so an empty
  // edits list counts zero while its scene is still holding a review. Branched
  // on that sum, this panel answered "Nothing waiting -- every proposal has
  // been decided" over a scene that was waiting, which is the one thing it
  // must never say. `ScenesView` keys off `pending` and was always right.
  (api.getShell as any).mockResolvedValue(
    shell({ unreviewed: 0, pending: [{ sid: "s2", proposals: 0 }] }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("Waiting on you")).toBeInTheDocument();
  expect(screen.queryByText("Nothing waiting")).not.toBeInTheDocument();
  // ...and it does not argue against itself with a count of nought.
  expect(screen.queryByText(/0 proposals/i)).not.toBeInTheDocument();
});

test("with no mechanics module there is no sheets card and no sheets link", async () => {
  // "No mechanics bound" is a legal state, not a coverage of 0 of 0 -- and a
  // card whose whole content is "this does not apply here" is a card that
  // should not be on the page.
  renderHub();
  await screen.findByText("Run One");
  expect(screen.queryByText(/mechanics & sheets/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /^sheets$/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/0 of 0/)).not.toBeInTheDocument();
});

test("with a module bound the sheets card carries its coverage", async () => {
  (api.getShell as any).mockResolvedValue(shell({ sheets: { sheeted: 4, total: 7 } }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("4 of 7")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /^sheets$/i }))
    .toHaveAttribute("href", "/campaigns/run/sheets");
});

// Moved here with the controls themselves: these were on the scene toolbar,
// which is where four campaign-level destinations were duplicating the rail.
test("the export menu carries a download link per format", async () => {
  renderHub();
  await screen.findByText("Run One");
  const epub = screen.getByRole("link", { name: /^epub$/i });
  expect(epub).toHaveAttribute("href", "/api/campaigns/run/export.epub");
  expect(epub).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: /markdown/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.md.zip");
  expect(screen.getByRole("link", { name: /^html$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.html");
  expect(screen.getByRole("link", { name: /plain text/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.txt");
});

test("the ledger is reachable from the hub", async () => {
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByRole("link", { name: /ledger & timeline/i }))
    .toHaveAttribute("href", "/campaigns/run/ledger");
});

test("a failed read is not an empty campaign", async () => {
  // Opposite answers, and the difference has to survive: "could not be read"
  // must never render as "there is nothing here".
  (api.getShell as any).mockRejectedValue(new Error("offline"));
  renderHub();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText("Next up")).not.toBeInTheDocument();
});


test("the campaign's own settings are reachable from its front door", () => {
  // Mechanics, Calendar and Cover belong to the CAMPAIGN, but they used to sit
  // on the scene bar -- so opening a campaign gave you no way to bind a
  // mechanics module at all. You had to already be inside a scene to configure
  // the thing the scenes belong to.
  renderHub();
  return screen.findByText("Run One").then(() => {
    fireEvent.click(screen.getByRole("button", { name: "Mechanics" }));
    expect(screen.getByTestId("mechanics-panel")).toBeInTheDocument();
    // One at a time, and clicking the open one closes it.
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    expect(screen.queryByTestId("mechanics-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("calendar-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    expect(screen.queryByTestId("calendar-panel")).not.toBeInTheDocument();
  });
});


// ---- the money card ----

test("the three money columns are shown and never summed", async () => {
  // The complaint the redesign opened with. Each column is labelled with what
  // it actually is, so a campaign whose spend is $0 can still be seen to have
  // used a subscription's worth of generation.
  (api.getShell as any).mockResolvedValue(withMoney({}));
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Costs" }))
    .closest("section")!;
  expect(within(card).getByText("$4.82")).toBeInTheDocument();
  expect(within(card).getByText("≈ $1.10")).toBeInTheDocument();
  expect(within(card).getByText("≈ $0.36")).toBeInTheDocument();
  // 4.82 + 1.10 + 0.36. The one figure this card may never render.
  expect(within(card).queryByText(/6\.28/)).not.toBeInTheDocument();
  expect(within(card).getByText(/never summed/i)).toBeInTheDocument();
});

test("an unpriced call is flagged rather than counted as zero", async () => {
  (api.getShell as any).mockResolvedValue(withMoney({ unpriced_calls: 3 }));
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Costs" }))
    .closest("section")!;
  expect(within(card).getByText(/3 calls reported no price/i)).toBeInTheDocument();
});

test("an aggregate that could not be totalled says so instead of showing $0.00", async () => {
  (api.getShell as any).mockResolvedValue(withMoney({ partial: true }));
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Costs" }))
    .closest("section")!;
  expect(within(card).getByText(/could not be totalled/i)).toBeInTheDocument();
  expect(within(card).queryByText("$0.00")).not.toBeInTheDocument();
});

test("the budget bar draws only where a budget is set", async () => {
  (api.getShell as any).mockResolvedValue(withMoney({}));
  renderHub();
  await screen.findByRole("heading", { name: "Costs" });
  // `level: "off"` from the default mock: no cap, so no fraction, so no bar.
  expect(screen.queryByRole("img", { name: /budget/i })).not.toBeInTheDocument();
});

test("a budget that is set draws its bar", async () => {
  (api.getShell as any).mockResolvedValue(withMoney({}));
  (api.getCampaignBudget as any).mockResolvedValue({
    level: "warn", limit_usd: 10, spent_usd: 4.82, fraction: 0.482,
    period: "monthly", unpriced_calls: 0 });
  renderHub();

  expect(await screen.findByRole("img", { name: /\$4\.82 of \$10\.00 budget/ }))
    .toBeInTheDocument();
});

// ---- the cast card ----

test("the cast card names faces and marks the PCs", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara Vance", default_version: "v1", versions: [],
      has_avatar: false },
  ]);
  (api.listCampaignPCs as any).mockResolvedValue([
    { id: "sera", name: "Seraphine", default_version: "v1", versions: [],
      tags: [], has_avatar: false },
  ]);
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Cast" }))
    .closest("section")!;
  expect(within(card).getByText("Seraphine")).toBeInTheDocument();
  expect(within(card).getByText("Mara Vance")).toBeInTheDocument();
  // The PC chip is on the PC and only on the PC.
  expect(within(card).getAllByText("PC")).toHaveLength(1);
  // A count, derived from the two lists rather than restated.
  expect(within(card).getByText("2")).toBeInTheDocument();
});

test("a cast that could not be read is not an empty cast", async () => {
  // Opposite answers, and they must never render the same way.
  (api.listCharacters as any).mockRejectedValue(new Error("nope"));
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Cast" }))
    .closest("section")!;
  expect(within(card).getByText(/could not be read/i)).toBeInTheDocument();
  expect(within(card).queryByText(/nobody has been cast/i)).not.toBeInTheDocument();
});

// ---- world changes ----

test("world changes lists what play moved", async () => {
  (api.campaignChanges as any).mockResolvedValue([
    { ref: { kind: "lore", id: "the-salt-pact" }, name: "The Salt Pact",
      scene: { id: "s2", title: "The second", date: "" },
      fields: [{ field: "body", from: "a", to: "b" }] },
  ]);
  renderHub();

  const card = (await screen.findByRole("heading", { name: "World changes" }))
    .closest("section")!;
  expect(within(card).getByText("The Salt Pact")).toBeInTheDocument();
  expect(within(card).getByText(/1 field · The second/)).toBeInTheDocument();
});

test("a world nothing has changed says so", async () => {
  renderHub();
  const card = (await screen.findByRole("heading", { name: "World changes" }))
    .closest("section")!;
  expect(within(card).getByText(/has not changed the world yet/i)).toBeInTheDocument();
});

// ---- play next ----

test("play next offers saved ideas and spends nothing to do it", async () => {
  (api.listSceneIdeas as any).mockResolvedValue([
    { id: "i1", title: "The lower step", premise: "An owed thread is past due.",
      status: "active", source: "llm", date: "", cast: [], location: null,
      pcless: false, created: "", used_scene: "" },
    { id: "i2", title: "Used already", premise: "", status: "used",
      source: "llm", date: "", cast: [], location: null, pcless: false,
      created: "", used_scene: "s1" },
  ]);
  renderHub();

  const card = (await screen.findByRole("heading", { name: "Play next" }))
    .closest("section")!;
  expect(within(card).getByText("The lower step")).toBeInTheDocument();
  expect(within(card).getByText("An owed thread is past due.")).toBeInTheDocument();
  // A used idea is not something to play next.
  expect(within(card).queryByText("Used already")).not.toBeInTheDocument();
  // The whole reason this card shows saved ideas rather than generated ones:
  // opening a campaign must not spend a generation.
  expect((api as any).sceneSuggestions).toBeUndefined();
});
