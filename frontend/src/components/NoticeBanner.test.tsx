import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NoticeBanner } from "./NoticeBanner";
import { api, type Notice } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { dismissNotices: vi.fn() } };
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
  vi.mocked(api.dismissNotices).mockResolvedValue({ ok: true, marked: [] });
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

test("dismissing marks the key and takes the row away", async () => {
  const onDismissed = vi.fn();
  render(<NoticeBanner cid="c" notices={[HOLIDAY, EVENT]} scene="001--a"
                       onDismissed={onDismissed} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith(
    "c", [HOLIDAY.key], "001--a"));
  expect(screen.queryByText("Saltmarch Eve")).toBeNull();
  // The other notice is untouched: a dismissal is about one occurrence.
  expect(screen.getByText("The envoy arrives")).toBeTruthy();
  await waitFor(() => expect(onDismissed).toHaveBeenCalledWith(HOLIDAY.key));
});

test("dismissing the last notice empties the banner", async () => {
  const { container } = render(<NoticeBanner cid="c" notices={[HOLIDAY]} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

test("a failed dismissal puts the row back", async () => {
  // The one outcome a reader cannot tell from success is a banner that looks
  // dismissed and is not — so an optimistic hide has to be reversible.
  vi.mocked(api.dismissNotices).mockRejectedValue(new Error("offline"));
  const onDismissed = vi.fn();
  render(<NoticeBanner cid="c" notices={[HOLIDAY]} onDismissed={onDismissed} />);
  fireEvent.click(screen.getByLabelText("Dismiss Saltmarch Eve"));
  await waitFor(() => expect(screen.getByText("Saltmarch Eve")).toBeTruthy());
  expect(onDismissed).not.toHaveBeenCalled();
});
