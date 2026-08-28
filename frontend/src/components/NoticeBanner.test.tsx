import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { NoticeBanner } from "./NoticeBanner";
import { api, type Notice } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { dismissNotices: vi.fn(), restoreNotices: vi.fn() } };
});

const HOLIDAY: Notice = {
  key: "holiday:739383:Saltmarch Eve", kind: "holiday", name: "Saltmarch Eve",
  in_days: 3, friendly: "13 May 2026",
};
const EVENT: Notice = {
  key: "event:739381:the-envoy-arrives", kind: "event", name: "The envoy arrives",
  in_days: 1, friendly: "11 May 2026",
};

beforeEach(() => {
  // Cleared before the defaults are re-set: without it a spy carries the calls
  // every earlier test made, and a `not.toHaveBeenCalled()` assertion is
  // reading the previous test rather than this one.
  vi.clearAllMocks();
  vi.mocked(api.dismissNotices).mockResolvedValue({ ok: true, marked: [] });
  vi.mocked(api.restoreNotices).mockResolvedValue({ ok: true, forgotten: [] });
});

test("nothing to warn about renders nothing at all", () => {
  const { container } = render(<NoticeBanner cid="c" notices={[]} />);
  expect(container).toBeEmptyDOMElement();
});

test("a notice names the thing, the lead time and the day", () => {
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  expect(screen.getByText("Saltmarch Eve")).toBeTruthy();
  expect(screen.getByText(/in 3 days/)).toBeTruthy();
  expect(screen.getByText(/13 May 2026/)).toBeTruthy();
});

test("a notice one day out says tomorrow, not '1 days'", () => {
  render(<NoticeBanner cid="c" notices={[EVENT]} />);
  expect(screen.getByText(/tomorrow/)).toBeTruthy();
  expect(screen.queryByText(/1 days/)).toBeNull();
});

test("dismissing marks the key and takes the warning away", async () => {
  render(<NoticeBanner cid="c" notices={[HOLIDAY, EVENT]} scene="001--a" />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith(
    "c", [HOLIDAY.key], "001--a"));
  expect(screen.queryByLabelText("Dismiss Saltmarch Eve")).toBeNull();
  // The other notice is untouched: a dismissal is about one occurrence.
  expect(screen.getByText("The envoy arrives")).toBeTruthy();
});

test("a failed dismissal puts the row back", async () => {
  // The one outcome a reader cannot tell from success is a banner that looks
  // dismissed and is not — so an optimistic hide has to be reversible.
  vi.mocked(api.dismissNotices).mockRejectedValue(new Error("offline"));
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(screen.getByLabelText("Dismiss Saltmarch Eve")).toBeTruthy());
});

// ---- the undo, which a permanent dismissal has to have -------------------

test("a dismissed notice leaves an Undo behind", async () => {
  // A dismissal is permanent for that occurrence — the store refuses to
  // overwrite an acknowledgement — so a misclick with no way back would silence
  // a holiday until its day had gone by.
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  expect(await screen.findByLabelText("Undo dismissing Saltmarch Eve")).toBeTruthy();
  expect(screen.getByText(/Saltmarch Eve dismissed/)).toBeTruthy();
});

test("Undo restores the key even after the owner has refetched the row away", async () => {
  // The owning surface refetches as soon as the write lands, so by the time
  // Undo is clicked the row is gone from `notices` — the banner has to be
  // holding its own copy of it.
  const { rerender } = render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalled());
  rerender(<NoticeBanner cid="c" notices={[]} />);
  fireEvent.click(await screen.findByLabelText("Undo dismissing Saltmarch Eve"));
  await waitFor(() => expect(api.restoreNotices).toHaveBeenCalledWith("c", [HOLIDAY.key]));
});

test("Undo is disabled until the dismissal it would take back has landed", async () => {
  // Both writes take the campaign lock, so in flight together they serialize in
  // whichever order they arrive: a forget that wins finds nothing to forget,
  // then the mark it beat lands, leaving the occurrence dismissed while the
  // banner has already reported it restored.
  let land: (v: any) => void = () => {};
  vi.mocked(api.dismissNotices).mockReturnValue(new Promise((r) => { land = r; }));
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  const undo = await screen.findByLabelText("Undo dismissing Saltmarch Eve");
  expect(undo).toBeDisabled();
  // Clicked anyway: `fireEvent` dispatches straight at the handler, which is
  // exactly the case the in-function guard exists for — the disabled attribute
  // is the affordance, not the serialization.
  fireEvent.click(undo);
  expect(api.restoreNotices).not.toHaveBeenCalled();
  await act(async () => { land({ ok: true, marked: [HOLIDAY.key] }); });
  await waitFor(() => expect(
    screen.getByLabelText("Undo dismissing Saltmarch Eve")).not.toBeDisabled());
});

test("a failed Undo leaves the row dismissed", async () => {
  vi.mocked(api.restoreNotices).mockRejectedValue(new Error("offline"));
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  fireEvent.click(await screen.findByLabelText("Undo dismissing Saltmarch Eve"));
  await waitFor(() => expect(
    screen.getByLabelText("Undo dismissing Saltmarch Eve")).toBeTruthy());
});

// ---- the store retires acknowledgements of its own accord ------------------

test("a notice whose acknowledgement the store retired warns again", async () => {
  // Deleting an event drops every notice keyed to its id, so a recreation of
  // the same id is not born already dismissed -- and `pending` then offers the
  // key again. The optimistic list must let go of it, or the banner filters out
  // a live warning until something happens to unmount it.
  const { rerender } = render(<NoticeBanner cid="c" notices={[EVENT]} />);
  fireEvent.click(screen.getByLabelText("Dismiss The envoy arrives"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalled());
  // The owner refetches: the ledger holds the acknowledgement, so the row is
  // gone from `notices` and only the Undo receipt is left.
  rerender(<NoticeBanner cid="c" notices={[]} />);
  await screen.findByLabelText("Undo dismissing The envoy arrives");
  // Retired, and offered again.
  rerender(<NoticeBanner cid="c" notices={[EVENT]} />);
  expect(await screen.findByLabelText("Dismiss The envoy arrives")).toBeTruthy();
});

test("the window before the owner refetches is not read as a retirement", async () => {
  // The guard on the test above rather than a second bug: between a dismissal
  // landing and the owner's refetch arriving, the key is still in `notices` for
  // ordinary reasons, and "in `notices` again" alone would un-dismiss every row
  // a moment after it was dismissed.
  render(<NoticeBanner cid="c" notices={[EVENT]} />);
  fireEvent.click(screen.getByLabelText("Dismiss The envoy arrives"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalled());
  expect(screen.queryByLabelText("Dismiss The envoy arrives")).toBeNull();
});


// ---- one occurrence key is not unique across campaigns --------------------

test("a campaign change forgets what was dismissed in the last one", async () => {
  // The banner stays mounted across a `cid` navigation, and the same built-in
  // holiday on the same day generates the same key in every campaign. Without
  // the reset, dismissing Midwinter in one campaign hides the legitimate,
  // unacknowledged Midwinter in the next one opened.
  const { rerender } = render(<NoticeBanner cid="a" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith("a", [HOLIDAY.key], ""));
  rerender(<NoticeBanner cid="b" notices={[HOLIDAY]} />);
  // Campaign b's ledger has no acknowledgement, so its warning is live again —
  // and b's banner carries no leftover "dismissed" receipt from a.
  expect(await screen.findByLabelText("Dismiss Saltmarch Eve")).toBeTruthy();
  expect(screen.queryByLabelText("Undo dismissing Saltmarch Eve")).toBeNull();
});

test("a dismissal that settles after a campaign switch does not touch the new one", async () => {
  // The render-time reset clears state on a `cid` change, but a request already
  // in flight closes over the OLD campaign. Its rejection must not remove the
  // row B optimistically hid, and its `finally` must not clear B's guard early
  // and re-open the mark/forget race.
  let failA: (e: unknown) => void = () => {};
  vi.mocked(api.dismissNotices).mockReturnValueOnce(
    new Promise((_res, rej) => { failA = rej; }));
  const { rerender } = render(<NoticeBanner cid="a" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));

  // Campaign B, same occurrence key, its own dismissal still in flight.
  vi.mocked(api.dismissNotices).mockReturnValueOnce(new Promise(() => {}));
  rerender(<NoticeBanner cid="b" notices={[HOLIDAY]} />);
  fireEvent.click(await screen.findByLabelText("Dismiss Saltmarch Eve"));
  const undo = await screen.findByLabelText("Undo dismissing Saltmarch Eve");
  expect(undo).toBeDisabled();

  await act(async () => { failA(new Error("offline")); });
  // B's optimistic row is still hidden, and B's Undo is still guarded.
  expect(screen.queryByLabelText("Dismiss Saltmarch Eve")).toBeNull();
  expect(screen.getByLabelText("Undo dismissing Saltmarch Eve")).toBeDisabled();
});


// ---- two banners over one ledger ------------------------------------------

/** The scene panel and the new-scene chooser both render a banner over the same
 *  campaign-wide ledger, so the interesting cases need two of them, a ledger
 *  they share, and control over the order the writes reach it in. `refetch` is
 *  what `noticesChanged` does in the app: every write emits, and every surface
 *  showing the ledger reloads.
 */
function twoBanners() {
  const ledger = new Set<string>();
  const queue: (() => void)[] = [];
  const owners: (() => void)[] = [];
  const rowsNow = (n: Notice) => (ledger.has(n.key) ? [] : [n]);

  function Owner({ notice }: { notice: Notice }) {
    const [rows, setRows] = useState<Notice[]>(() => rowsNow(notice));
    useEffect(() => {
      const reload = () => setRows(rowsNow(notice));
      owners.push(reload);
      return () => { owners.splice(owners.indexOf(reload), 1); };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return <NoticeBanner cid="c" notices={rows} />;
  }

  const enqueue = (run: () => void) => new Promise<any>((res) => {
    queue.push(() => { const v = run(); owners.forEach((f) => f()); res(v); });
  });
  vi.mocked(api.dismissNotices).mockImplementation((_c, keys) =>
    enqueue(() => { keys.forEach((k) => ledger.add(k)); return { ok: true, marked: keys }; }));
  vi.mocked(api.restoreNotices).mockImplementation((_c, keys) =>
    enqueue(() => { keys.forEach((k) => ledger.delete(k)); return { ok: true, forgotten: keys }; }));

  /** Land the write queued at `at`, and let the refetch it triggers settle. */
  const land = (at: number) => act(async () => { queue.splice(at, 1)[0](); });
  return { ledger, queue, land, Owner };
}

test("a write still in flight is not read as the store retiring its key", async () => {
  // Both banners dismiss the same occurrence, then the first is undone. The
  // undo lands between the second banner's mark going out and coming back, so
  // the key leaves `notices` and comes back for a reason that is not this
  // banner's own dismissal -- which, read as a retirement, would drop the
  // receipt of a dismissal that then lands anyway, leaving the occurrence
  // acknowledged with the Undo it needs gone from the screen.
  const { ledger, queue, land, Owner } = twoBanners();
  render(<><Owner notice={EVENT} /><Owner notice={EVENT} /></>);
  const rows = screen.getAllByLabelText("Dismiss The envoy arrives");
  expect(rows).toHaveLength(2);
  fireEvent.click(rows[0]);                 // mark A
  fireEvent.click(rows[1]);                 // mark B
  await land(0);                            // mark A lands; queue = [mark B]
  fireEvent.click(screen.getAllByLabelText("Undo dismissing The envoy arrives")[0]);
  await land(1);                            // forget A lands first
  await land(0);                            // then mark B
  await waitFor(() => expect(queue).toHaveLength(0));

  // The ledger holds the acknowledgement, so neither banner offers the warning
  // -- and the banner whose mark wrote it still offers the way back.
  expect(ledger.has(EVENT.key)).toBe(true);
  expect(screen.queryAllByLabelText("Dismiss The envoy arrives")).toHaveLength(0);
  expect(screen.queryAllByLabelText("Undo dismissing The envoy arrives")).toHaveLength(1);
});
