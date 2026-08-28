import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import CostsView from "./CostsView";
import { PaletteProvider } from "../components/palette";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    getCampaignSceneCosts: vi.fn(),
  },
}));
import { api } from "../api/client";

const ZERO = {
  calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
  cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
  modelled_usd: 0, priced_calls: 0, unpriced_calls: 0,
  subscription_calls: 0, modelled_calls: 0, unmetered_calls: 0, duration_ms: 0,
};

const row = (scene: string, title: string, over: Partial<typeof ZERO> & {
  first_ts?: string; last_ts?: string; missing?: boolean } = {}) => ({
  ...ZERO, scene, title, created: "", updated: "",
  first_ts: "2026-08-01T10:00:00Z", last_ts: "2026-08-01T10:00:00Z",
  missing: false, ...over,
});

const REPORT = {
  campaign: "run", since: "2026-06-01", until: "2026-08-14",
  generated_at: "2026-08-14T12:00:00Z", order: "cost",
  totals: { ...ZERO, calls: 9, priced_calls: 9, cost_usd: 1.5, total_tokens: 90000 },
  scenes: [
    row("002--market", "The Market", { calls: 6, priced_calls: 6, cost_usd: 1.2,
                                       total_tokens: 60000,
                                       last_ts: "2026-08-10T10:00:00Z" }),
    row("001--arrival", "The Priory Door", { calls: 3, priced_calls: 3, cost_usd: 0.3,
                                             total_tokens: 30000,
                                             last_ts: "2026-08-12T10:00:00Z" }),
  ],
  listed: 2, truncated: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch" }, body: "" });
  (api.getCampaignSceneCosts as any).mockResolvedValue(REPORT);
});

function renderCosts() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run/costs"]}>
      <PaletteProvider>
        <Routes>
          <Route path="/campaigns/:cid/costs" element={<CostsView />} />
          <Route path="/campaigns/:cid" element={<div>the play view</div>} />
          <Route path="/campaigns/:cid/scenes/:sid" element={<div>the scene</div>} />
        </Routes>
      </PaletteProvider>
    </MemoryRouter>,
  );
}

const column = () => within(screen.getByRole("complementary"));
/** The three money columns, which live across the top of the BODY now.
 *
 *  This is the one page whose whole subject is the money, and they were being
 *  read at 274px in the slot that answers "what am I navigating". What is left
 *  in the column is the campaign, the order, and the generation count. */
const headline = () => within(screen.getByRole("main"));
const bodyRows = () => screen.getAllByRole("row").slice(1);

test("each scene is listed with what it cost", async () => {
  renderCosts();

  const [first, second] = await screen.findAllByRole("row").then((r) => r.slice(1));
  expect(within(first).getByText("The Market")).toBeInTheDocument();
  expect(within(first).getByText("$1.20")).toBeInTheDocument();
  expect(within(second).getByText("The Priory Door")).toBeInTheDocument();
  expect(within(second).getByText("$0.30")).toBeInTheDocument();
});

test("the campaign's all-time total is the headline", async () => {
  renderCosts();

  expect(await headline().findByText("$1.50")).toBeInTheDocument();
  // The count stays in the column: it says how much of the ledger the figure
  // is built on, which is a fact about the report rather than the report.
  expect(column().getByText(/9 generations/)).toBeInTheDocument();
});

test("the money is across the body, not down the 274px column", async () => {
  // The layout complaint this page had: three columns of money in a slot that
  // is 274px wide and is meant for navigation.
  renderCosts();
  await headline().findByText("$1.50");

  expect(column().queryByText("$1.50")).not.toBeInTheDocument();
  expect(headline().getByText(/never summed/i)).toBeInTheDocument();
});

test("a scene row links into the scene it names", async () => {
  renderCosts();

  expect(await screen.findByRole("link", { name: "The Market" }))
    .toHaveAttribute("href", "/campaigns/run/scenes/002--market");
});

test("re-ordering asks the SERVER, because the list is capped there", async () => {
  // Sorting the response here would make every ordering but the default mean
  // "…of the most expensive N", so a campaign past the cap would be missing a
  // recent cheap scene from a list headed "most recent".
  renderCosts();
  await headline().findByText("$1.50");
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT, order: "recent", scenes: [...REPORT.scenes].reverse(),
  });
  fireEvent.click(column().getByRole("button", { name: /most recent/i }));

  await waitFor(() => expect(api.getCampaignSceneCosts)
    .toHaveBeenCalledWith("run", "recent"));
  expect(within(bodyRows()[0]).getByText("The Priory Door")).toBeInTheDocument();
});

test("a deleted scene keeps its row, because it keeps its place in the total", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    scenes: [row("003--gone", "", { calls: 2, priced_calls: 2, cost_usd: 0.5,
                                    missing: true })],
  });
  renderCosts();

  const [only] = await screen.findAllByRole("row").then((r) => r.slice(1));
  expect(within(only).getByText(/deleted, and its spend still counted/)).toBeInTheDocument();
  expect(within(only).getByText("$0.50")).toBeInTheDocument();
  expect(within(only).queryByRole("link")).toBeNull();
});

test("calls that belong to no scene are still in the list", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    scenes: [row("", "", { calls: 1, priced_calls: 1, cost_usd: 0.02 })],
  });
  renderCosts();

  expect(await screen.findByText("Outside any scene")).toBeInTheDocument();
});

test("a campaign nobody billed does not claim a reported charge of $0.00", async () => {
  // `cost_usd` is 0.0 when everything was subscription-billed, and the closing
  // line would then say providers reported a charge of $0.00 — a reported zero
  // where nobody reported anything.
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    totals: { ...ZERO, calls: 2, priced_calls: 2, subscription_calls: 2,
              estimated_usd: 0.80 },
    scenes: REPORT.scenes.slice(0, 1),
  });
  renderCosts();

  expect(await screen.findByText(/No provider reported a charge for this campaign/))
    .toBeInTheDocument();
  expect(screen.queryByText(/is what providers said they charged/)).toBeNull();
});

test("the truncation note names the order the rows were cut by", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue(
    { ...REPORT, order: "recent", truncated: true, listed: 2 });
  renderCosts();

  expect(await screen.findByText(/came first by most recent/)).toBeInTheDocument();
});

test("a scene nobody priced does not total to $0.00", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    totals: { ...ZERO, calls: 3, unpriced_calls: 3 },
    scenes: [row("001--arrival", "The Priory Door", { calls: 3, unpriced_calls: 3 })],
  });
  renderCosts();

  const [only] = await screen.findAllByRole("row").then((r) => r.slice(1));
  expect(within(only).getByText("not reported")).toBeInTheDocument();
  expect(screen.queryByText("$0.00")).toBeNull();
});

test("the scanned window is reported in the reader's own calendar", async () => {
  renderCosts();

  expect(await column().findByText(
    new RegExp(`Ledger scanned from ${new Date(2026, 5, 1, 12).toLocaleDateString()}`)))
    .toBeInTheDocument();
});

test("a scene row never totals its two kinds of estimate", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    scenes: [row("002--market", "The Market", {
      calls: 5, priced_calls: 3, cost_usd: 0.40, subscription_calls: 2,
      estimated_usd: 0.90, modelled_calls: 1, modelled_usd: 0.10 })],
  });
  renderCosts();

  const [only] = await screen.findAllByRole("row").then((r) => r.slice(1));
  expect(within(only).getByText("$0.40")).toBeInTheDocument();
  expect(within(only).getByText(/≈ \$0\.90 subscription/)).toBeInTheDocument();
  expect(within(only).getByText(/≈ \$0\.10 estimated/)).toBeInTheDocument();
  // $1.00 would be the merged figure, and it reconciles to neither column.
  expect(within(only).queryByText(/\$1\.00/)).toBeNull();
});

test("a row whose headline IS the estimate does not print it twice", async () => {
  // `bucketPrice` renders the single estimate as the headline; a "+ ≈ $X"
  // line under it reads as two amounts where the ledger holds one.
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    scenes: [row("002--market", "The Market", {
      calls: 2, priced_calls: 2, subscription_calls: 2, estimated_usd: 0.90 })],
  });
  renderCosts();

  const [only] = await screen.findAllByRole("row").then((r) => r.slice(1));
  expect(within(only).getByText("≈ $0.90")).toBeInTheDocument();
  expect(within(only).queryByText(/\+ ≈ \$0\.90/)).toBeNull();
});

test("subscription and modelled spend are reported apart from the bill", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue({
    ...REPORT,
    totals: { ...ZERO, calls: 4, priced_calls: 3, cost_usd: 0.4,
              subscription_calls: 2, estimated_usd: 0.9,
              modelled_calls: 1, modelled_usd: 0.1 },
    scenes: REPORT.scenes.slice(0, 1),
  });
  renderCosts();

  expect(await headline().findByText(
    /2 calls billed to a subscription, not charged \(≈ \$0\.90 at the provider's per-token rates\)/))
    .toBeInTheDocument();
  expect(headline().getByText(
    /1 call the provider did not price \(≈ \$0\.10 at your per-token rates\)/))
    .toBeInTheDocument();
});

test("a campaign that has generated nothing says so rather than showing $0.00", async () => {
  (api.getCampaignSceneCosts as any).mockResolvedValue(
    { ...REPORT, totals: ZERO, scenes: [], listed: 0 });
  renderCosts();

  expect(await screen.findByText(/Nothing has been generated in this campaign yet/))
    .toBeInTheDocument();
});

test("a failed read says so rather than reporting a measured zero", async () => {
  // Degrading to an empty report printed "$0.00" and "Nothing has been
  // generated" over a campaign with real spend — this feature's cardinal
  // error wearing a different hat.
  (api.getCampaignSceneCosts as any).mockRejectedValue(new Error("no"));
  renderCosts();

  expect(await screen.findByText(/Could not read this campaign's costs/))
    .toBeInTheDocument();
  expect(screen.queryByText("$0.00")).toBeNull();
  expect(screen.queryByText(/Nothing has been generated/)).toBeNull();
});

test("a failed read can be retried", async () => {
  (api.getCampaignSceneCosts as any)
    .mockRejectedValueOnce(new Error("no")).mockResolvedValue(REPORT);
  renderCosts();

  fireEvent.click(await screen.findByText("Try again"));

  expect(await headline().findByText("$1.50")).toBeInTheDocument();
});

test("the heading describes the order that came back, not the one just clicked", async () => {
  // An all-time rescan can be slow, and until it lands the rows are still in
  // the previous order.
  renderCosts();
  await headline().findByText("$1.50");
  let release: (v: unknown) => void = () => {};
  (api.getCampaignSceneCosts as any).mockReturnValue(
    new Promise((res) => { release = res; }));
  fireEvent.click(column().getByRole("button", { name: /most recent/i }));

  // Still the cost-ordered answer on screen, and the eyebrow still says so.
  expect(screen.getByText(/WHERE THE MONEY WENT/)).toBeInTheDocument();
  expect(screen.getByText(/REORDERING/)).toBeInTheDocument();

  release({ ...REPORT, order: "recent" });
  expect(await screen.findByText(/NEWEST ACTIVITY FIRST/)).toBeInTheDocument();
});

test("the way back to the campaign is a link", async () => {
  renderCosts();

  expect(await column().findByRole("link", { name: /saltmarch/i }))
    .toHaveAttribute("href", "/campaigns/run");
});
