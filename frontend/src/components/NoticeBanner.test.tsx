import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
