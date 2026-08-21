import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CostPanel } from "./CostPanel";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getSceneUsage: vi.fn(),
    getCampaignBudget: vi.fn(),
    setCampaignBudget: vi.fn(),
  } };
});

const ZERO = {
  calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
  cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
  modelled_usd: 0, priced_calls: 0, unpriced_calls: 0,
  subscription_calls: 0, modelled_calls: 0, duration_ms: 0,
};

const TURN = {
  ts: "2026-08-14T10:00:00Z", task: "chat", model: "realm/opus", connection: "Main",
  status: "ok", error: "", attempts: 1,
  prompt_tokens: 900, completion_tokens: 40, total_tokens: 940,
  cache_read_tokens: 0, cache_write_tokens: 0,
  cost_usd: 0.0042, cost_basis: "billed", modelled_usd: null, post: 0,
  duration_ms: 4210,
};

const USAGE = {
  campaign: "c", scene: "s", since: "2026-08-01", until: "2026-08-14",
  generated_at: "2026-08-14T12:00:00Z",
  totals: { ...ZERO, calls: 2, prompt_tokens: 1800, completion_tokens: 80,
            total_tokens: 1880, cost_usd: 0.0084, priced_calls: 2 },
  by_task: [{ key: "chat", ...ZERO, calls: 2, cost_usd: 0.0084 }],
  by_post: [{ post: 0, rerolls: 1, ...ZERO, calls: 2, cost_usd: 0.0084,
              priced_calls: 2 }],
  turns: [TURN, { ...TURN, ts: "2026-08-14T09:00:00Z", task: "retry" }],
  listed: 2, truncated: false,
};

const OFF = { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 };

const SET = {
  limit_usd: 10, period: "monthly", level: "ok", warn_fraction: 0.8,
  since: "2026-08-01", until: "2026-08-14", spent_usd: 2, estimated_usd: 0,
  unpriced_calls: 0, calls: 12, fraction: 0.2,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSceneUsage).mockResolvedValue(USAGE as never);
  vi.mocked(api.getCampaignBudget).mockResolvedValue(OFF as never);
  vi.mocked(api.setCampaignBudget).mockResolvedValue(SET as never);
});

test("totals the scene and lists its turns newest first", async () => {
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(/\$0\.0084 · 2 turns · 1,880 tok/)).toBeInTheDocument();
  const labels = screen.getAllByText(/^(chat|retry)$/).map((n) => n.textContent);
  expect(labels).toEqual(["chat", "retry"]);
});

test("a turn the provider never priced says so rather than showing $0.00", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    ...USAGE,
    totals: { ...ZERO, calls: 1, unpriced_calls: 1 },
    by_task: [{ key: "chat", ...ZERO, calls: 1, unpriced_calls: 1 }],
    turns: [{ ...TURN, cost_usd: null }], listed: 1,
  } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText("unpriced")).toBeInTheDocument();
  expect(await screen.findByText(/1 call came back with no price/)).toBeInTheDocument();
});

test("subscription-billed dollars are reported apart from the spend", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    ...USAGE,
    totals: { ...ZERO, calls: 1, priced_calls: 1, subscription_calls: 1,
              estimated_usd: 0.5 },
  } as never);
  render(<CostPanel cid="c" sid="s" />);

  // The parenthetical is the point: a subscription call is not spend, and the
  // figure beside it is what it WOULD have cost per token.
  expect(await screen.findByText(
    /1 call billed to a subscription, not charged \(≈ \$0\.50 at the provider's per-token rates\)/))
    .toBeInTheDocument();
});

test("a call nobody priced is estimated from the user's own rates, marked", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    ...USAGE,
    totals: { ...ZERO, calls: 1, modelled_calls: 1, modelled_usd: 0.25 },
    by_task: [{ key: "chat", ...ZERO, calls: 1, modelled_calls: 1, modelled_usd: 0.25 }],
    turns: [{ ...TURN, cost_usd: null, modelled_usd: 0.25 }], listed: 1,
  });
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(
    /1 call the provider did not price \(≈ \$0\.25 at your per-token rates\)/))
    .toBeInTheDocument();
  // Headline and turn row both read as an estimate, never as a bill.
  expect(await screen.findByText(/^≈ \$0\.25 · 1 turn/)).toBeInTheDocument();
  expect(screen.queryByText("unpriced")).not.toBeInTheDocument();
});

test("says when the turn list was cut and the totals were not", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    { ...USAGE, listed: 2, truncated: true } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(/Showing the most recent 2/)).toBeInTheDocument();
});

test("a campaign with no budget offers to set one", async () => {
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText("No budget set for this campaign.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Budget in dollars"), { target: { value: "10" } });
  fireEvent.click(screen.getByText("Set budget"));

  await waitFor(() => expect(api.setCampaignBudget).toHaveBeenCalledWith(
    "c", { budget_usd: 10, budget_period: "monthly" }));
  expect(await screen.findByText(/\$2\.00 of \$10\.00/)).toBeInTheDocument();
});

test("an amount that is not a positive number cannot be saved", async () => {
  render(<CostPanel cid="c" sid="s" />);
  await screen.findByLabelText("Budget in dollars");

  expect(screen.getByText("Set budget")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Budget in dollars"), { target: { value: "0" } });
  expect(screen.getByText("Set budget")).toBeDisabled();
});

test("a budget past its warning threshold says how far along it is", async () => {
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { ...SET, spent_usd: 8.5, fraction: 0.85, level: "warn" } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText("85% of the budget spent.")).toBeInTheDocument();
});

test("clearing a budget sends null rather than zero", async () => {
  vi.mocked(api.getCampaignBudget).mockResolvedValue(SET as never);
  vi.mocked(api.setCampaignBudget).mockResolvedValue(OFF as never);
  render(<CostPanel cid="c" sid="s" />);

  fireEvent.click(await screen.findByText("Clear"));

  await waitFor(() => expect(api.setCampaignBudget).toHaveBeenCalledWith(
    "c", { budget_usd: null, budget_period: "monthly" }));
  expect(await screen.findByText("No budget set for this campaign.")).toBeInTheDocument();
});

test("a scene that has generated nothing says so instead of showing an empty list", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    { ...USAGE, totals: ZERO, by_task: [], turns: [], listed: 0 } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText("Nothing metered in this scene yet.")).toBeInTheDocument();
});

test("a failed budget write is reported without losing the panel", async () => {
  vi.mocked(api.setCampaignBudget).mockRejectedValue({ detail: "store is read-only" });
  render(<CostPanel cid="c" sid="s" />);

  fireEvent.change(await screen.findByLabelText("Budget in dollars"), { target: { value: "5" } });
  fireEvent.click(screen.getByText("Set budget"));

  expect(await screen.findByText("store is read-only")).toBeInTheDocument();
});

test("a turn landing mid-edit does not wipe the figure being typed", async () => {
  const { rerender } = render(<CostPanel cid="c" sid="s" refreshKey={0} />);
  fireEvent.change(await screen.findByLabelText("Budget in dollars"), { target: { value: "25" } });

  // What a finished turn does: the inspector bumps the key and everything here
  // re-reads. The stored budget is still none, and the box still says 25.
  rerender(<CostPanel cid="c" sid="s" refreshKey={1} />);
  await waitFor(() => expect(api.getCampaignBudget).toHaveBeenCalledTimes(2));

  expect(screen.getByLabelText("Budget in dollars")).toHaveValue(25);
});

test("a scene whose calls were all unpriced does not total to $0.00", async () => {
  const unpriced = { ...ZERO, calls: 3, unpriced_calls: 3, total_tokens: 90 };
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    ...USAGE, totals: unpriced, by_task: [{ key: "chat", ...unpriced }],
    turns: [{ ...TURN, cost_usd: null }], listed: 1,
  } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(/^unpriced · 3 turns/)).toBeInTheDocument();
  expect(screen.getByText(/chat 3 · unpriced/)).toBeInTheDocument();
  expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
});

test("one priced call among unpriced ones keeps the figure, as a floor", async () => {
  const mixed = { ...ZERO, calls: 3, priced_calls: 1, unpriced_calls: 2, cost_usd: 0.02 };
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    { ...USAGE, totals: mixed, by_task: [{ key: "chat", ...mixed }] } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(/^\$0\.02 · 3 turns/)).toBeInTheDocument();
  expect(screen.getByText(/2 calls came back with no price/)).toBeInTheDocument();
});

test("a budget smaller than a cent cannot be set, since the store reads it as none", async () => {
  render(<CostPanel cid="c" sid="s" />);
  fireEvent.change(await screen.findByLabelText("Budget in dollars"),
                   { target: { value: "0.004" } });

  expect(screen.getByText("Set budget")).toBeDisabled();
  expect(api.setCampaignBudget).not.toHaveBeenCalled();
});

test("turns sharing a stamp, a task and a model all render", async () => {
  // Absorb fans its phases out concurrently now, so this is a real shape and
  // not a contrived one: content-keyed rows would collapse into each other.
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    ...USAGE,
    totals: { ...ZERO, calls: 2, cost_usd: 0.0084, priced_calls: 2 },
    turns: [TURN, { ...TURN }], listed: 2,
  } as never);
  render(<CostPanel cid="c" sid="s" />);

  await screen.findByText(/\$0\.0084 · 2 turns/);
  expect(screen.getAllByText("chat")).toHaveLength(2);
});

test("a slow read for the scene just left cannot land under the new scene", async () => {
  let releaseFirst: (u: unknown) => void = () => {};
  vi.mocked(api.getSceneUsage)
    .mockImplementationOnce(() => new Promise((res) => { releaseFirst = res; }) as never)
    .mockResolvedValue({
      ...USAGE, totals: { ...ZERO, calls: 1, cost_usd: 0.5, priced_calls: 1 },
    } as never);

  const { rerender } = render(<CostPanel cid="c" sid="first" />);
  rerender(<CostPanel cid="c" sid="second" />);
  expect(await screen.findByText(/\$0\.50 · 1 turn/)).toBeInTheDocument();

  // The first scene's answer arrives late, naming numbers that are not this
  // scene's. It must not reach the panel.
  releaseFirst({ ...USAGE, totals: { ...ZERO, calls: 99, cost_usd: 42, priced_calls: 99 } });
  await waitFor(() => expect(api.getSceneUsage).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/\$42\.00/)).not.toBeInTheDocument();
  expect(screen.getByText(/\$0\.50 · 1 turn/)).toBeInTheDocument();
});

test("a scene that spent nothing does not head its empty list with $0.00", async () => {
  vi.mocked(api.getSceneUsage).mockResolvedValue(
    { ...USAGE, totals: ZERO, by_task: [], turns: [], listed: 0 } as never);
  render(<CostPanel cid="c" sid="s" />);

  await screen.findByText("Nothing metered in this scene yet.");
  expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
});

test("a budget lookup that failed leaves no heading standing on its own", async () => {
  vi.mocked(api.getCampaignBudget).mockRejectedValue(new Error("nope"));
  render(<CostPanel cid="c" sid="s" />);

  await screen.findByText(/\$0\.0084 · 2 turns/);
  expect(screen.queryByText("Campaign budget")).not.toBeInTheDocument();
});

test("a four-figure budget is grouped like every other number here", async () => {
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { ...SET, limit_usd: 2500, spent_usd: 1250, fraction: 0.5 } as never);
  render(<CostPanel cid="c" sid="s" />);

  expect(await screen.findByText(/\$1,250\.00 of \$2,500\.00/)).toBeInTheDocument();
});
