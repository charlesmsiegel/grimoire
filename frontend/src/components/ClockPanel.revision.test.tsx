// The write token (#409): a priced move carries the campaign's revision, and
// a checkpoint's idempotency key names the move it was asked about.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ClockPanel } from "./ClockPanel";
import { api, type AdvanceDigest, type ForkReport } from "../api/client";
import { fixture } from "../testkit/fixture";
import { askToAdvance, BIG, CLOCK, DIGEST, FORK_REPORT, keyFor, resetClockMocks, REV } from "../testkit/clockHarness";

vi.mock("../api/client", async () => (await import("../testkit/clockMocks")).clockApiMock());

beforeEach(resetClockMocks);

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

  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.idempotencyKey);
  expect(keys).toHaveLength(2);
  expect(keys[0]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  expect(keys[1]).toBe(keys[0]);
  // ...and both name the state they were priced against, so the server refuses
  // the copy rather than taking one of a campaign that moved.
  expect(vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.expectRevision))
    .toEqual([REV, REV]);
});


test("a replayed checkpoint is named by the fork that exists", async () => {
  // The key is derived from the operation precisely so a RELOAD between the
  // copy and the skip still reaches the server as the same request — and a
  // reload is exactly when the name the reader typed is gone. So the one case
  // the key exists for is the one where the name submitted here is not the
  // fork's, and naming it anyway sends the reader to the shelf looking for a
  // campaign nobody created.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  vi.mocked(api.forkCampaign).mockResolvedValue({ ...FORK_REPORT, replayed: true });
  vi.mocked(api.getCampaign).mockResolvedValue(
    fixture({ meta: { name: "Winifred's last quiet week" }, body: "" }));

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  expect(api.getCampaign).toHaveBeenCalledWith(FORK_REPORT.id);
  expect(screen.getByText(/Winifred's last quiet week/)).toBeInTheDocument();
  expect(screen.queryByText(/Before 24 March 2027/)).not.toBeInTheDocument();
});

test("a copy this call took is named by what it was asked for", async () => {
  // The other half, so the lookup above is about a replay rather than a new
  // rule for every checkpoint: `uniquify` suffixes the fork's ID when a name
  // slugifies onto an existing campaign, never its NAME, so a copy is called
  // what the reader called it and there is nothing to go and ask.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.change(screen.getByLabelText("Checkpoint name"),
                   { target: { value: "The night before" } });
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  expect(screen.getByText(/“The night before” is on the campaigns shelf/)).toBeInTheDocument();
  expect(api.getCampaign).not.toHaveBeenCalled();
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
  // MID-copytree, which is the whole case: the gate answer re-prices before it
  // copies, so waiting for the fork to have been asked for is what puts the
  // turn inside the copy rather than before it.
  await waitFor(() => expect(api.forkCampaign).toHaveBeenCalled());
  rerender(<ClockPanel cid="c" refreshKey={1} />);
  release(FORK_REPORT);
  await screen.findByText(/may not hold the latest turn/);

  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  // Exactly two copies for one landed checkpoint: the disowned one, and the one
  // the reader kept. The second names the state it was actually priced against,
  // and so does the skip.
  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.idempotencyKey);
  expect(keys[0]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  expect(keys[1]?.startsWith(keyFor(BIG.to, "rev-2"))).toBe(true);
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

  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.idempotencyKey);
  expect(keys[0]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  expect(keys[1]?.startsWith(keyFor(BIG.to, "rev-2"))).toBe(true);
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

test("a changed digest re-prices even when the token has not moved", async () => {
  // The token's documented limit, and the half the test above cannot reach: it
  // only sees writes THIS app made. A sync client landing a scheduled event
  // inside the span, or a hand edit to the store, changes what the skip would
  // do and moves no token at all — so the token alone cannot answer "is this
  // still the move I was shown", and the digest has to be compared too.
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  // The confirm-time re-price: the SAME token, a different move.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: WITH_EVENT });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));

  await screen.findByText(/preview it again/);
  expect(api.forkCampaign).not.toHaveBeenCalled();
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("a rejected gate re-reads the clock, not only the pricing", async () => {
  // `Now:` is the one number this panel shows that a re-preview cannot replace:
  // `/advance/preview` answers with a digest and a token and says nothing about
  // where the campaign's present is. So a clock another tab moved would sit on
  // screen through the whole recovery, and the next pricing would be seeded
  // from it (Codex review).
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: WITH_EVENT });
  // The first read is the campaign as the reader opened it; every read after
  // that is a campaign another tab has moved.
  vi.mocked(api.getCampaignClock).mockResolvedValueOnce(CLOCK);
  vi.mocked(api.getCampaignClock).mockResolvedValue(
    { ...CLOCK, now: "2026-12-31", friendly: "31 December 2026" });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/preview it again/);

  // The reader is told to preview again, and what they are looking at while
  // they do is the campaign's actual present.
  expect(await screen.findByText(/Now: 31 December 2026/)).toBeInTheDocument();
});

test("the checkpoint key names the move, not just the token", async () => {
  // The reload case, which no in-panel check can reach: the panel's state is
  // gone, so a fresh preview rebuilds the question from whatever the campaign
  // now says and there is nothing stale left to compare. The key is the only
  // thing that carries identity across that, and the token inside it cannot see
  // writes this app did not make — so a sync client landing an event inside the
  // span would leave the key identical and `/fork` would replay a copy taken
  // before it (Codex review).
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  // ...the page reloads, and the same operation is priced again against a span
  // something outside this app has changed. Same target, same token.
  cleanup();
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: WITH_EVENT });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);

  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.idempotencyKey);
  expect(keys).toHaveLength(2);
  expect(keys[0]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  expect(keys[1]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  // Same operation by target and token, different move — so a different key,
  // and the server takes a fresh copy instead of replaying the older one.
  expect(keys[1]).not.toBe(keys[0]);
});

test("skipping without a checkpoint re-prices too", async () => {
  // The gate has two answers and only one of them was guarded. This one writes
  // no checkpoint, so there is nothing to abandon — but it still moves the
  // clock and can still fire events, and a price nobody rechecked is exactly as
  // stale here (Codex review).
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: WITH_EVENT });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Skip without one"));

  await screen.findByText(/preview it again/);
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("a retry after the checkpoint landed re-prices before it advances", async () => {
  // The third way through the gate: the copy succeeded, its advance failed, and
  // the reader presses again. `checkpointed` short-circuits the copy, which
  // used to carry the re-price with it — so the retry advanced on a price from
  // before the copy.
  const WITH_EVENT: AdvanceDigest = { ...BIG, events: [
    { id: "the-coronation", name: "The coronation", date: "2027-02-01",
      friendly: "1 February 2027", note: "", fired: null, passed: false,
      in_days: 39 }] };
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "an advance needs a reason" });
  // ...and by the retry, something outside this app has changed the span.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: WITH_EVENT });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/an advance needs a reason/);
  expect(api.forkCampaign).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByText("Retry the skip"));
  await screen.findByText(/preview it again/);
  // No second copy — `checkpointed` still holds — and no advance on the stale
  // price either, which is the half that was missing.
  expect(api.forkCampaign).toHaveBeenCalledTimes(1);
  expect(api.advanceTime).toHaveBeenCalledTimes(1);
});

test("an unchanged digest under an unchanged token goes ahead", async () => {
  // The counterpart, so the comparison above is a check on the move rather than
  // a new way to refuse every checkpoint: the same digest object priced twice
  // must not read as a change.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  expect(api.forkCampaign).toHaveBeenCalled();
});

test("the copy carries the token it was priced against, not only its key", async () => {
  // This panel's own check is not binding: a whole request separates it from
  // the copy, and only the server holds the source's lock across one. Without
  // the token travelling with the fork, a campaign written in that gap gets
  // checkpointed anyway and the skip then refuses it — a `copytree` spent on a
  // state nobody asked for.
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: BIG });
  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  expect(vi.mocked(api.forkCampaign).mock.calls[0][3]).toEqual({
    idempotencyKey: expect.stringMatching(`^${keyFor(BIG.to, REV)}`),
    expectRevision: REV });
});

test("a checkpoint another tab outlived is retired by the re-price", async () => {
  // `refreshKey` is THIS tab's signal and does not fire for somebody else's
  // write, so the token is the only thing that notices. Without retiring the
  // marker, the reopened question offers "Retry the skip", takes no copy, and
  // skips on a restore point from before the other tab's write.
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValueOnce({ revision: REV, digest: BIG });
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: "rev-2", digest: BIG });
  vi.mocked(api.advanceTime).mockRejectedValueOnce({ detail: "an advance needs a reason" });

  await askToAdvance("90");
  await screen.findByText(/large time skip/);
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/an advance needs a reason/);
  expect(screen.getByText("Retry the skip")).toBeInTheDocument();

  // Dismiss and ask again — no turn landed here, so `refreshKey` never moved.
  fireEvent.click(screen.getByText("Cancel"));
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/large time skip/);

  // The marker is gone, so this is a fresh copy rather than a bare retry...
  expect(screen.getByText("Checkpoint, then advance")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Checkpoint, then advance"));
  await screen.findByText(/^Advanced/);
  const keys = vi.mocked(api.forkCampaign).mock.calls.map((c) => c[3]?.idempotencyKey);
  expect(keys[0]?.startsWith(keyFor(BIG.to, REV))).toBe(true);
  expect(keys[1]?.startsWith(keyFor(BIG.to, "rev-2"))).toBe(true);
});
