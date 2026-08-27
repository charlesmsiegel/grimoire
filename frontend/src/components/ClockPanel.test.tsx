import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ClockPanel } from "./ClockPanel";
import { api, type AdvanceDigest, type CampaignClock,
         type ForkReport } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getCampaignClock: vi.fn(),
    previewAdvance: vi.fn(),
    advanceTime: vi.fn(),
    // CalendarDatePicker (the "skip to a date" control) fetches months.
    getCalendarMonths: vi.fn(),
    // Read only to know whether a calendar has been chosen for this campaign.
    getCalendarConfig: vi.fn(),
    // The checkpoint the panel offers before a large skip (#107).
    forkCampaign: vi.fn(),
  } };
});

const CLOCK = {
  now: "2026-12-24", friendly: "24 December 2026",
  log: [{ from: "", to: "2026-12-24", reason: "the caravan sets out", at: "2026-12-01T10:00:00Z" }],
  revision: "rev-1",
};

const DIGEST: AdvanceDigest = {
  from: "2026-12-24", to: "2026-12-27",
  from_friendly: "24 December 2026", to_friendly: "27 December 2026",
  elapsed_days: 3, backward: false, truncated: false,
  holidays: [{ name: "Christmas Day", native: "2026-12-25", friendly: "25 December 2026", in_days: 1 }],
  birthdays: [{ name: "Seraphine", age: 36, native: "2026-12-26", friendly: "26 December 2026" }],
  open_threads: [{ id: "debt", title: "The moneylender's debt", status: "open",
                   last_scene: "001--a", latest_beat: "Interest accrues.",
                   aging: { state: "stale", days_since: 53, days_over: null, due_in: null } }],
  // Scheduled events (#101) and the commitments this move ages (#103): both
  // arrive on every digest, so the fixture carries both.
  events: [{ id: "coronation", name: "The coronation", date: "2026-12-26",
             friendly: "26 December 2026", note: "", fired: null, passed: false,
             in_days: 2 }],
  commitments: [{ id: "the-debt", title: "Repay the moneylender", kind: "promise",
                  status: "open", due: "2026-12-20", last_scene: "001--a",
                  latest_beat: "Mara swore it.",
                  aging: { state: "overdue", days_since: 53, days_over: 7, due_in: null } }],
  aging: { overdue: 1, stale: 1, stale_after: 30 },
  // The checkpoint nudge (#107) rides every digest. Three days is not a large
  // skip, so the default fixture is the ungated path.
  fork: false, fork_threshold: 30,
};

/** The same move, over the threshold — what the checkpoint prompt answers to. */
const BIG: AdvanceDigest = { ...DIGEST, elapsed_days: 90, to: "2027-03-24",
              to_friendly: "24 March 2027", fork: true };

const FORK_REPORT: ForkReport = { id: "before-24-march-2027", from_scene: "",
                      removed_scenes: [], records: 0, refused: [], failed: [],
                      replayed: false };

/** The campaign's write token (#409), as every pricing below hands one back.
 *
 *  One value for the whole suite because almost every test is about something
 *  else and only needs the panel to have a token to carry; the tests that are
 *  about the token say so by using a second one. */
const REV = "rev-1";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getCampaignClock).mockResolvedValue(CLOCK);
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  vi.mocked(api.advanceTime).mockResolvedValue(
    { ok: true, moved: true, now: "2026-12-27", friendly: "27 December 2026",
      digest: DIGEST, fired: DIGEST.events });
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [] });
  vi.mocked(api.forkCampaign).mockResolvedValue(FORK_REPORT);
  vi.mocked(api.getCalendarConfig).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true } as never);
});

test("shows where the campaign's present is", async () => {
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/Now: 24 December 2026/)).toBeInTheDocument();
});

test("says so when no campaign date exists yet", async () => {
  vi.mocked(api.getCampaignClock).mockResolvedValue({ now: "", friendly: "", log: [], revision: REV });
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/No campaign date yet/)).toBeInTheDocument();
});

test("refuses to move time until the campaign has a calendar", async () => {
  // The same nudge the neighbouring When section gives. Recording a moment in the
  // default calendar's notation before the reader picks a different one leaves the
  // whole campaign holding a date its own calendar cannot read.
  vi.mocked(api.getCalendarConfig).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false } as never);
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/Select a calendar in the When section/)).toBeInTheDocument();
  expect(screen.queryByText("Advance time")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Days")).not.toBeInTheDocument();
});

test("previews the digest without advancing", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "3" } });
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/Would advance/)).toBeInTheDocument();
  expect(screen.getByText(/Christmas Day/)).toBeInTheDocument();
  expect(screen.getByText(/Seraphine turns 36/)).toBeInTheDocument();
  expect(screen.getByText("The moneylender's debt")).toBeInTheDocument();
  expect(api.previewAdvance).toHaveBeenCalledWith("c", { days: 3 });
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("advancing needs a reason", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  expect(screen.getByText("Advance time")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  expect(screen.getByText("Advance time")).toBeEnabled();
});

test("advances by days with the reason, then reports what it crossed", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/^Advanced/)).toBeInTheDocument();
  expect(api.advanceTime).toHaveBeenCalledWith(
    "c", { days: 3, reason: "three days of rain", expect_revision: REV });
  // The clock is re-read, so the header line follows the move.
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledTimes(2));
});

test("a confirmed advance the clock was already at does not claim to have moved", async () => {
  vi.mocked(api.advanceTime).mockResolvedValue({
    ok: true, moved: false, now: "2026-12-24", friendly: "24 December 2026",
    digest: { ...DIGEST, from: "2026-12-24", to: "2026-12-24",
              to_friendly: "24 December 2026", elapsed_days: 0,
              holidays: [], birthdays: [] },
  } as never);
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "nowhere" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/Already at/)).toBeInTheDocument();
  expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument();
});

test("a landed advance disarms the button, so a stray second click cannot repeat it", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/^Advanced/);
  // The duration is deliberately left as typed; the cleared reason is what stops
  // the next click from skipping another three days.
  expect(screen.getByLabelText("Days")).toHaveValue(1);
  expect(screen.getByText("Advance time")).toBeDisabled();
  expect(api.advanceTime).toHaveBeenCalledTimes(1);
});

test("skips to a date instead of a duration when asked", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Advance by"), { target: { value: "date" } });
  expect(screen.queryByLabelText("Days")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Skip to year")).toBeInTheDocument();
  // With no date picked there is nothing to preview or confirm.
  expect(screen.getByText("Preview")).toBeDisabled();
});

test("drops a previewed digest when the target changes", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  await screen.findByText(/Would advance/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "9" } });
  // Confirming a skip the reader never previewed is the failure this prevents.
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();
});

test("an overlong span reports the span and says why it is not itemized", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: {
    ...DIGEST, elapsed_days: 4000, truncated: true, holidays: [], birthdays: [],
  } });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/4000 days/)).toBeInTheDocument();
  expect(screen.getByText(/Too long a span to itemize/)).toBeInTheDocument();
});

test("a backward move is labelled as one", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: {
    ...DIGEST, elapsed_days: -4, backward: true,
  } });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/4 days back/)).toBeInTheDocument();
});

test("surfaces a refused advance instead of pretending it landed", async () => {
  vi.mocked(api.advanceTime).mockRejectedValue({ detail: "no current date to advance from" });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "onwards" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/no current date to advance from/)).toBeInTheDocument();
  expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument();
});

// ---- the checkpoint prompt (#107) ------------------------------------------

/** Fill in a reason and ask to advance, from the default one-day state. */
async function askToAdvance(days?: string) {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  if (days !== undefined) {
    fireEvent.change(screen.getByLabelText("Days"), { target: { value: days } });
  }
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  // Confirming always prices the move first, so this is the settle every caller
  // needs: without it the helper returns through an `await` with the preview
  // still in flight, and the assertion after it runs against a half-built page
  // (CLAUDE.md — an `await` means the page has SETTLED).
  await waitFor(() => expect(api.previewAdvance).toHaveBeenCalled());
}

test("a large skip asks about a checkpoint before it moves anything", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  expect(await screen.findByText(/large time skip/)).toBeInTheDocument();
  // The whole point: nothing has been written when the question is asked.
  expect(api.advanceTime).not.toHaveBeenCalled();
  expect(api.forkCampaign).not.toHaveBeenCalled();
  // ...and the digest that priced it is on screen underneath, so the question
  // is asked next to what it is about rather than in the abstract.
  expect(screen.getByText(/Would advance/)).toBeInTheDocument();
});

test("a threshold that asks about everything still says \"1 day\"", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: {
    ...DIGEST, elapsed_days: 1, fork: true, fork_threshold: 0 } });
  await askToAdvance("1");
  expect(await screen.findByText(/1 day, more than 0 days/)).toBeInTheDocument();
});

test("the prompt says what this install counts as large", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({
    revision: REV, digest: { ...BIG, fork_threshold: 7 } });
  await askToAdvance("90");
  // Read off the digest rather than restated in the panel, so the sentence
  // cannot disagree with the comparison that produced it.
  expect(await screen.findByText(/more than 7 days/)).toBeInTheDocument();
});

test("a large backward correction is asked about, and says which way it goes", async () => {
  // Un-living two months loses as much of the recorded present as living them.
  // "90 days" with no direction would read as the wrong one.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: {
    ...BIG, elapsed_days: -90, backward: true } });
  await askToAdvance("-90");
  expect(await screen.findByText(/90 days backward/)).toBeInTheDocument();
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("the checkpoint is taken before the skip, never after it", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  const order: string[] = [];
  vi.mocked(api.forkCampaign).mockImplementation(
    () => { order.push("fork"); return Promise.resolve(FORK_REPORT as never); });
  vi.mocked(api.advanceTime).mockImplementation(
    () => { order.push("advance"); return Promise.resolve(
      { ok: true, moved: true, now: "2027-03-24", friendly: "24 March 2027",
        digest: BIG } as never); });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  // A copy taken after the clock moved would be a copy of the wrong campaign.
  expect(order).toEqual(["fork", "advance"]);
  // The copy carries the idempotency key its operation derives (#409), and
  // the skip carries the token it was priced against.
  expect(api.forkCampaign).toHaveBeenCalledWith(
    "c", "Before 24 March 2027", undefined, `checkpoint:${BIG.to}:${REV}`);
  expect(api.advanceTime).toHaveBeenCalledWith(
    "c", { days: 90, reason: "a season passes", expect_revision: REV });
});

test("the checkpoint is a copy left behind, so the skip happens in the campaign you are in", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  // Both calls name the ORIGINAL campaign: the fork is the thing left standing
  // where the story was, and play continues where the reader already is.
  expect(vi.mocked(api.forkCampaign).mock.calls[0][0]).toBe("c");
  expect(vi.mocked(api.advanceTime).mock.calls[0][0]).toBe("c");
  expect(screen.getByText(/Checkpoint saved/)).toBeInTheDocument();
});

test("the checkpoint can be named", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.change(screen.getByLabelText("Checkpoint name"),
                   { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await waitFor(() => expect(vi.mocked(api.forkCampaign).mock.calls[0]?.[1]).toBe("Saltmarch"));
});

test("a checkpoint with no name cannot be taken", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.change(screen.getByLabelText("Checkpoint name"), { target: { value: "  " } });
  // The endpoint refuses an empty name with a 400; the button that needs one
  // says so by being disabled rather than by earning it.
  expect(screen.getByText("Checkpoint, then advance")).toBeDisabled();
});

test("a failed checkpoint leaves the clock where it was", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.forkCampaign).mockRejectedValue({ detail: "no space left on device" });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  expect(await screen.findByText(/no space left on device/)).toBeInTheDocument();
  // The reader asked for a checkpoint AND a skip. Skipping anyway would leave
  // them past the moment they wanted to be able to come back to.
  expect(api.advanceTime).not.toHaveBeenCalled();
  // ...and the question is still on screen, so they can retry or skip anyway.
  expect(screen.getByText("Checkpoint, then advance")).toBeInTheDocument();
});

test("a retry after a checkpoint that landed does not take a second copy", async () => {
  // The expensive half succeeded and the cheap half did not. Forking again
  // would silently `copytree` the whole campaign a second time, and leave two
  // identically-named checkpoints on the shelf.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "campaign is busy" });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  expect(await screen.findByText(/campaign is busy/)).toBeInTheDocument();
  expect(screen.getByText(/Checkpoint saved/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Retry the skip"));
  await screen.findByText(/^Advanced/);
  expect(api.forkCampaign).toHaveBeenCalledTimes(1);
  expect(api.advanceTime).toHaveBeenCalledTimes(2);
});

test("the skip cannot be edited out from under a checkpoint in flight", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  let release: (r: ForkReport) => void = () => { /* replaced below */ };
  vi.mocked(api.forkCampaign).mockReturnValue(new Promise((res) => { release = res; }));
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  // A checkpoint is a `copytree` of a whole campaign and takes real time. The
  // skip that follows it is the one the question was asked about, so the
  // duration must not move underneath it.
  await waitFor(() => expect(screen.getByLabelText("Days")).toBeDisabled());
  release(FORK_REPORT);
  await screen.findByText(/^Advanced/);
  expect(api.advanceTime).toHaveBeenCalledWith(
    "c", { days: 90, reason: "a season passes", expect_revision: REV });
});

test("a pricing reply for the campaign you left cannot open a question here", async () => {
  // The confirm path, which is the one that matters: the reply belongs to
  // campaign A, and the buttons the question renders belong to whatever is on
  // screen. Installed anyway, answering it forks and skips B on the strength of
  // a span measured in A.
  let release: (r: { digest: AdvanceDigest; revision: string }) => void = () => { /* replaced */ };
  vi.mocked(api.previewAdvance).mockReturnValue(new Promise((res) => { release = res; }));
  const { rerender } = render(<ClockPanel cid="a" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await waitFor(() => expect(api.previewAdvance).toHaveBeenCalledWith("a", { days: 90 }));

  rerender(<ClockPanel cid="b" />);           // the reader moves on, mid-flight
  release({ digest: BIG, revision: REV });
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledWith("b"));

  expect(screen.queryByText(/large time skip/)).not.toBeInTheDocument();
  expect(api.forkCampaign).not.toHaveBeenCalled();
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("a clock read for the campaign you left cannot become the new one's present", async () => {
  // The clock is the moment every span is measured FROM. B displaying A's
  // present would misprice the next skip and mislabel the header while doing it.
  let release: (c: CampaignClock) => void = () => { /* replaced */ };
  vi.mocked(api.getCampaignClock).mockReturnValueOnce(new Promise((res) => { release = res; }));
  vi.mocked(api.getCampaignClock).mockResolvedValue(
    { now: "2030-01-01", friendly: "1 January 2030", log: [], revision: REV });
  const { rerender } = render(<ClockPanel cid="a" />);
  rerender(<ClockPanel cid="b" />);
  await screen.findByText(/Now: 1 January 2030/);
  release(CLOCK);                       // campaign A's clock, arriving late
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledWith("b"));
  expect(screen.queryByText(/24 December 2026/)).not.toBeInTheDocument();
  expect(screen.getByText(/Now: 1 January 2030/)).toBeInTheDocument();
});

test("...and cannot leave a stale preview on the new campaign either", async () => {
  let release: (r: { digest: AdvanceDigest; revision: string }) => void = () => { /* replaced */ };
  vi.mocked(api.previewAdvance).mockReturnValue(new Promise((res) => { release = res; }));
  const { rerender } = render(<ClockPanel cid="a" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  rerender(<ClockPanel cid="b" />);
  release({ digest: BIG, revision: REV });
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledWith("b"));
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();
});

test("a reason cannot be emptied out from under an open question", async () => {
  // The endpoint requires one and the gate's actions do not re-check it, so a
  // cleared reason would fork the campaign and then earn a 400 for the skip --
  // a full copy on the shelf for a move that never happened.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  expect(screen.getByLabelText("Reason")).toBeDisabled();
});

test("a checkpoint stops counting once the campaign has moved on", async () => {
  // The marker means "a copy of this campaign AS IT STANDS exists". A turn
  // landing between the copy and the retry makes that false, and reusing it
  // would hand back a restore point missing everything since.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "campaign is busy" });
  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/campaign is busy/);
  expect(screen.getByText("Retry the skip")).toBeInTheDocument();

  rerender(<ClockPanel cid="c" refreshKey={1} />);      // a turn lands
  await waitFor(() =>
    expect(screen.getByText("Checkpoint, then advance")).toBeInTheDocument());
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  // A second copy, deliberately: the first no longer holds what it is for.
  expect(api.forkCampaign).toHaveBeenCalledTimes(2);
});

test("an advance that landed in the campaign you left is not reported here", async () => {
  // The clock moved in A, which is right — A is what the request named. What
  // must not follow it is B adopting the result: "Advanced" over another
  // campaign's digest, and B's half-typed reason cleared by A's success.
  type AdvanceResult = Awaited<ReturnType<typeof api.advanceTime>>;
  let release: (r: AdvanceResult) => void = () => { /* replaced */ };
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: { ...DIGEST, fork: false } });
  const landed = { ok: true, moved: true, now: "2026-12-27",
                   friendly: "27 December 2026", digest: DIGEST } as AdvanceResult;
  vi.mocked(api.advanceTime).mockReturnValue(new Promise((res) => { release = res; }));

  const { rerender } = render(<ClockPanel cid="a" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a day" } });
  fireEvent.click(screen.getByText("Advance time"));
  await waitFor(() =>
    expect(api.advanceTime).toHaveBeenCalledWith(
      "a", { days: 1, reason: "a day", expect_revision: REV }));

  rerender(<ClockPanel cid="b" />);           // the reader moves on, mid-advance
  release(landed);
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledWith("b"));

  expect(screen.queryByText(/^Advanced/)).not.toBeInTheDocument();
  // The reason survives a campaign switch, so A clearing it would be visible.
  expect(screen.getByLabelText("Reason")).toHaveValue("a day");
});

test("an invalidated copy leaves a way forward, not a dead end", async () => {
  // Aborting the skip must not strand the reader: a campaign whose turns keep
  // landing could invalidate copy after copy. Previewing again always reaches a
  // completed skip once nothing lands during the copy.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  let release: (r: ForkReport) => void = () => { /* replaced */ };
  vi.mocked(api.forkCampaign).mockReturnValueOnce(new Promise((res) => { release = res; }));
  vi.mocked(api.forkCampaign).mockResolvedValue(FORK_REPORT);

  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);

  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  rerender(<ClockPanel cid="c" refreshKey={1} />);      // a turn lands mid-copytree
  release(FORK_REPORT);
  await screen.findByText(/was not taken/);
  expect(api.advanceTime).not.toHaveBeenCalled();

  // The way forward is Advance time, which re-prices: the campaign moved, so
  // the token the shown span was priced against is one the server would now
  // refuse, and the question has to be asked again about the state the
  // campaign is actually in.
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  expect(api.forkCampaign).toHaveBeenCalledTimes(2);
  expect(api.advanceTime).toHaveBeenCalledTimes(1);
});

test("a copy taken across a change does not carry the skip with it", async () => {
  // A turn landing while `copytree` runs is on one side of it or the other and
  // nothing here can see which. Reusing that copy on a retry would hand back a
  // restore point quietly missing a turn, so the reader is told what the copy
  // might not hold and the pricing behind the question is thrown away.
  let release: (r: ForkReport) => void = () => { /* replaced */ };
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.forkCampaign).mockReturnValue(new Promise((res) => { release = res; }));

  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));

  rerender(<ClockPanel cid="c" refreshKey={1} />);   // a turn lands mid-copytree
  release(FORK_REPORT);
  await screen.findByText(/may not hold the latest turn/);

  // The clock does NOT move. The panel's own rule is that a copy which fails
  // abandons the skip; a copy that may not hold the moment is that failure in
  // a weaker form, and advancing anyway would hand back exactly what the
  // reader said they did not want.
  expect(api.advanceTime).not.toHaveBeenCalled();
  // ...and the question goes with the pricing behind it, so nothing on screen
  // invites a second copy of a span that has stopped being true.
  expect(screen.queryByText("Checkpoint, then advance")).not.toBeInTheDocument();
  expect(screen.queryByText("Retry the skip")).not.toBeInTheDocument();
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();
});

test("a checkpoint of the campaign you left is never recorded against the new one", async () => {
  // The worst shape this class takes. A copy of A marked as B's means a large
  // skip in B offers "Retry the skip", takes no copy at all, and advances
  // anyway — the feature failing silently in the one direction it exists to
  // prevent, with the reader believing a restore point exists.
  let release: (r: ForkReport) => void = () => { /* replaced */ };
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.forkCampaign).mockReturnValue(new Promise((res) => { release = res; }));
  vi.mocked(api.advanceTime).mockRejectedValue({ detail: "campaign is busy" });

  const { rerender } = render(<ClockPanel cid="a" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));

  rerender(<ClockPanel cid="b" />);          // the reader moves on, mid-copytree
  release(FORK_REPORT);
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledWith("b"));

  // B never heard about A's copy...
  expect(screen.queryByText(/Checkpoint saved/)).not.toBeInTheDocument();
  // ...so a large skip here is offered a checkpoint of its own, not a retry.
  vi.mocked(api.advanceTime).mockResolvedValue(
    { ok: true, moved: true, now: "2027-03-24", friendly: "24 March 2027",
      digest: BIG } as never);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "onwards" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  expect(screen.getByText("Checkpoint, then advance")).toBeInTheDocument();
  expect(screen.queryByText("Retry the skip")).not.toBeInTheDocument();
});

test("dismissing after a checkpoint landed does not take a second one", async () => {
  // `checkpointed` has to outlive the question being closed. Without that, a
  // reader who cancels after the copy landed and then asks again gets a second
  // full copy of the same campaign, under the same name, silently.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "campaign is busy" });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/campaign is busy/);
  fireEvent.click(screen.getByText("Cancel"));
  await waitFor(() => expect(screen.queryByText(/large time skip/)).not.toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "onwards" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText("Retry the skip")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Retry the skip"));
  await screen.findByText(/^Advanced/);
  expect(api.forkCampaign).toHaveBeenCalledTimes(1);
});

test("the skip can be taken without a checkpoint", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Skip without one"));
  await screen.findByText(/^Advanced/);
  expect(api.forkCampaign).not.toHaveBeenCalled();
  expect(api.advanceTime).toHaveBeenCalledWith(
    "c", { days: 90, reason: "a season passes", expect_revision: REV });
});

test("the question can be dismissed without skipping or checkpointing", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Cancel"));
  await waitFor(() => expect(screen.queryByText(/large time skip/)).not.toBeInTheDocument());
  expect(api.advanceTime).not.toHaveBeenCalled();
  expect(api.forkCampaign).not.toHaveBeenCalled();
  // The typed skip survives the dismissal, so answering "not yet" costs nothing.
  expect(screen.getByLabelText("Days")).toHaveValue(90);
});

test("changing the skip takes back the question it was asked about", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "2" } });
  // Answering "checkpoint, then advance" against a span the prompt was never
  // about is the failure this prevents -- the same rule the digest follows.
  await waitFor(() => expect(screen.queryByText(/large time skip/)).not.toBeInTheDocument());
  expect(screen.getByText("Advance time")).toBeInTheDocument();
});

test("a small skip is not asked about", async () => {
  await askToAdvance("3");
  await screen.findByText(/^Advanced/);
  expect(screen.queryByText(/large time skip/)).not.toBeInTheDocument();
  expect(api.forkCampaign).not.toHaveBeenCalled();
});

test("a skip to a date is priced before the question, so the prompt cannot be dodged", async () => {
  // In "skip to a date" mode the client has no idea how far the skip goes --
  // that is calendar arithmetic, which is the server's. Confirming without
  // pressing Preview must therefore still ask, which means pricing it first.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.getCalendarMonths).mockResolvedValue(
    { months: [{ key: "March", name: "March", days: 31 }] });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Advance by"), { target: { value: "date" } });
  fireEvent.change(screen.getByLabelText("Skip to year"), { target: { value: "2027" } });
  await waitFor(() => expect(screen.getByLabelText("Skip to month")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Skip to month"), { target: { value: "March" } });
  await waitFor(() => expect(screen.getByLabelText("Skip to day")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Skip to day"), { target: { value: "24" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/large time skip/)).toBeInTheDocument();
  expect(api.previewAdvance).toHaveBeenCalledWith("c", { to: "2027-March-24" });
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("a clock that moved under a preview is re-priced, not confirmed against", async () => {
  // The inputs held still and the campaign's present moved: setting a scene's
  // date carries the clock forward, and that control is one section up from
  // this one. Priced small from December and confirmed after the clock reached
  // June, the same "skip to a date" is a long correction BACKWARD -- over any
  // threshold, and the question would never have been asked.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: { ...DIGEST, fork: false } });
  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  await screen.findByText(/Would advance/);

  vi.mocked(api.getCampaignClock).mockResolvedValue(
    { ...CLOCK, now: "2027-06-01", friendly: "1 June 2027" });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  rerender(<ClockPanel cid="c" refreshKey={1} />);
  await screen.findByText(/Now: 1 June 2027/);
  // The stale span is off the screen the moment the present it was measured
  // from stopped being the present.
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  // Re-priced against the moment it will actually land from, so the checkpoint
  // question is asked after all.
  expect(await screen.findByText(/large time skip/)).toBeInTheDocument();
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("confirming prices the move even when Preview just ran on the same inputs", async () => {
  // The reuse this replaces was the bug above: a digest that looks current
  // because the inputs have not changed is not current if the clock has.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: { ...DIGEST, fork: false } });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  await screen.findByText(/Would advance/);
  vi.mocked(api.previewAdvance).mockClear();
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "onwards" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/^Advanced/);
  expect(api.previewAdvance).toHaveBeenCalledTimes(1);
});

test("a landed advance is priced again rather than reusing its own digest", async () => {
  // The digest left on screen after a move describes the move that HAPPENED.
  // Reading a nudge off it would answer for the wrong span -- skipping to a
  // fixed date twice is ninety days and then none at all.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  await askToAdvance("3");
  await screen.findByText(/^Advanced/);
  vi.mocked(api.previewAdvance).mockClear();
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "again" } });
  fireEvent.click(screen.getByText("Advance time"));
  await waitFor(() => expect(api.previewAdvance).toHaveBeenCalledTimes(1));
});

test("a pricing failure stops the skip instead of waving it through", async () => {
  vi.mocked(api.previewAdvance).mockRejectedValue({ detail: "not a valid date in this calendar" });
  await askToAdvance("90");
  expect(await screen.findByText(/not a valid date in this calendar/)).toBeInTheDocument();
  // A skip that could not be priced could not be judged large or small, and
  // advancing on an unanswered question is the one outcome the gate forbids.
  expect(api.advanceTime).not.toHaveBeenCalled();
});


test("lists the recent advances and why they happened", async () => {
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/the caravan sets out/)).toBeInTheDocument();
});

test("a failed clock read degrades to the empty state", async () => {
  vi.mocked(api.getCampaignClock).mockRejectedValue(new Error("offline"));
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/No campaign date yet/)).toBeInTheDocument();
});

test("a preview lists the scheduled events the skip would reach", async () => {
  // Deliberately NOT "fired": a preview writes nothing, and the heading is the
  // only thing on screen that says which of the two this is.
  render(<ClockPanel cid="c" />);
  fireEvent.click(await screen.findByText("Preview"));
  expect(await screen.findByText("Scheduled events")).toBeInTheDocument();
  expect(screen.getByText(/The coronation — 26 December 2026/)).toBeInTheDocument();
});

test("a confirmed advance says the events fired", async () => {
  render(<ClockPanel cid="c" />);
  fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "a week off" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText("Events fired")).toBeInTheDocument();
});

test("the digest badges what the skip leaves overdue and stale", async () => {
  render(<ClockPanel cid="c" />);
  fireEvent.click(await screen.findByText("Preview"));
  expect(await screen.findByText(/OVERDUE BY 7 DAYS/)).toBeInTheDocument();
  expect(screen.getByText(/STALE · 53 DAYS UNTOUCHED/)).toBeInTheDocument();
});

// --- the write token (#409) ------------------------------------------------

test("a skip carries the token it was priced against", async () => {
  // The whole point of re-pricing on confirm was that the campaign can move
  // between a preview and the advance it describes. The re-price now says which
  // state it measured from, and the advance says which one it expects — so a
  // write landing in the gap is refused by the server rather than silently
  // producing a different move than the one on screen.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: "rev-9", digest: DIGEST });
  render(<ClockPanel cid="c" />);
  fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "a week off" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/^Advanced/);
  expect(vi.mocked(api.advanceTime).mock.calls[0][1].expect_revision).toBe("rev-9");
});

test("a refused skip takes the numbers it was refused for off the screen", async () => {
  // `campaign_moved` does not merely explain why nothing happened: it says the
  // digest, the question and the token behind them all describe a state the
  // campaign has left. Leaving them up invites the reader to confirm again
  // against a span nobody is being offered.
  vi.mocked(api.advanceTime).mockRejectedValue({
    kind: "campaign_moved", revision: "rev-2",
    detail: "this campaign changed while the skip was being decided; preview it again" });
  await askToAdvance("3");
  expect(await screen.findByText(/preview it again/)).toBeInTheDocument();
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();
  // ...and the clock is re-read, because a write that moved the token may have
  // moved the campaign's present with it.
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledTimes(2));
});

test("an ordinary refusal leaves the preview standing", async () => {
  // The counterpart, so the clearing above is a decision about one refusal
  // rather than a new rule for all of them: "an advance needs a reason" says
  // nothing about the numbers on screen having stopped being true.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  vi.mocked(api.advanceTime).mockRejectedValue({ detail: "an advance needs a reason" });
  await askToAdvance("3");
  expect(await screen.findByText(/an advance needs a reason/)).toBeInTheDocument();
  expect(screen.getByText(/Would advance/)).toBeInTheDocument();
});

test("a checkpoint retried after a lost response asks for the same copy", async () => {
  // The case the key exists for. The copy may well have landed — a lost
  // response and a failed write look identical from here — so the retry has to
  // reach the server as the SAME request, or it takes a second `copytree` of
  // the campaign and leaves a duplicate on the shelf. Derived from the
  // operation rather than minted per attempt, so a reload between the two
  // rebuilds it too.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.forkCampaign).mockRejectedValueOnce({ detail: "connection lost" });
  vi.mocked(api.forkCampaign).mockResolvedValue({ ...FORK_REPORT, replayed: true });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/connection lost/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(`checkpoint:${BIG.to}:${REV}`);
  expect(keys[1]).toBe(keys[0]);
});


test("a disowned copy costs one more copy, not two", async () => {
  // The reason the pricing is cleared rather than the key merely varied. Left
  // standing, the question could be answered again — a second whole `copytree`
  // — and only then would `/advance` refuse the stale token, so landing one
  // checkpoint would have taken three copies. Re-pricing first mints both the
  // token the skip needs and the key the copy needs, in one call.
  let release: (r: ForkReport) => void = () => { /* replaced */ };
  // Two prices before the turn lands (the confirm, then the checkpoint's own
  // re-price), and rev-2 for everything after it.
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: "rev-2", digest: BIG });
  vi.mocked(api.forkCampaign).mockReturnValueOnce(new Promise((res) => { release = res; }));
  vi.mocked(api.forkCampaign).mockResolvedValue(FORK_REPORT);

  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  rerender(<ClockPanel cid="c" refreshKey={1} />);      // a turn lands mid-copytree
  release(FORK_REPORT);
  await screen.findByText(/may not hold the latest turn/);

  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  // Exactly two copies for one landed checkpoint: the disowned one, and the one
  // the reader kept. The second names the state it was actually priced against,
  // and so does the skip.
  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]);
  expect(keys).toEqual([`checkpoint:${BIG.to}:${REV}`, `checkpoint:${BIG.to}:rev-2`]);
  expect(vi.mocked(api.advanceTime).mock.calls[0][1].expect_revision).toBe("rev-2");
});

test("a checkpoint the campaign outlived is not replayed for the retry", async () => {
  // The path a counted-calls assertion cannot see. The copy COMPLETED, its
  // advance then failed, and a turn landed before the retry: `refreshKey`
  // clears the marker so a fresh copy is wanted, but the token that names the
  // old one is still in state — and the key is derived from it, so the server
  // would replay the pre-turn copy and then refuse the skip that followed it.
  //
  // Nothing is written for a question the campaign has outlived. The reader is
  // sent back to Preview, which is the only thing that produces a digest, a
  // token and a key that agree with each other.
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: "rev-2", digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "an advance needs a reason" });

  const { rerender } = render(<ClockPanel cid="c" refreshKey={0} />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);

  // The copy lands; the skip that follows it does not.
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/an advance needs a reason/);
  expect(screen.getByText("Retry the skip")).toBeInTheDocument();

  rerender(<ClockPanel cid="c" refreshKey={1} />);   // ...and then a turn lands
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/preview it again/);
  // No second copy was taken for the question the turn invalidated, and no skip.
  expect(api.forkCampaign).toHaveBeenCalledTimes(1);
  expect(api.advanceTime).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();

  // Previewing again produces a token, a key and a skip that all name the
  // campaign as it now stands.
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]);
  expect(keys).toEqual([`checkpoint:${BIG.to}:${REV}`, `checkpoint:${BIG.to}:rev-2`]);
  expect(vi.mocked(api.advanceTime).mock.calls[1][1].expect_revision).toBe("rev-2");
});

test("a span whose contents changed under an open question is not skipped", async () => {
  // The case `pricedNow` cannot see: another tab schedules an event INSIDE the
  // proposed span. The clock has not moved, so nothing on screen looks stale —
  // but confirming would now fire something the reader was never shown. The
  // token is what notices, because it moves on any write at all.
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: "rev-2", digest: WITH_EVENT });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));

  await screen.findByText(/preview it again/);
  expect(api.forkCampaign).not.toHaveBeenCalled();
  expect(api.advanceTime).not.toHaveBeenCalled();
});
