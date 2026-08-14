import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import TimelineView from "./TimelineView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";

vi.mock("../api/client", () => ({
  api: { getCampaign: vi.fn(), campaignTimeline: vi.fn() },
}));
import { api } from "../api/client";

const EMPTY = { scenes: [], threads: [] };

const scene = (over: Partial<Record<string, unknown>> & { id: string; title: string }) => ({
  one_line: "", summary: "", date: "", location: "",
  done: false, pcless: false, beats: [], ...over,
});

/** A campaign a few scenes deep, in the state one actually lives in: the older
 *  scenes absorbed and carrying beats, the newest one still in play with no
 *  summary of its own. */
const PLAYED = {
  threads: [
    { id: "a-debt", title: "A debt unpaid", status: "open" },
    { id: "sea-wall", title: "The sea wall", status: "closed" },
  ],
  scenes: [
    scene({ id: "001--first-light", title: "First Light", one_line: "They met at the pier.",
            summary: "They met at the pier. At length.", date: "28 Sowing",
            location: "The Pier", done: true,
            beats: [{ thread: "sea-wall", title: "The sea wall", status: "closed",
                      text: "Winifred named the debt." }] }),
    scene({ id: "002--the-long-tide", title: "The Long Tide",
            one_line: "They argued until the tide turned.", date: "3 Reaping",
            done: true,
            beats: [{ thread: "sea-wall", title: "The sea wall", status: "closed",
                      text: "Seraphine mended it." },
                    { thread: "a-debt", title: "A debt unpaid", status: "open",
                      text: "And opened another." }] }),
    scene({ id: "003--verdigris", title: "Verdigris & Ash", date: "4 Reaping" }),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch" }, body: "" });
  (api.campaignTimeline as any).mockResolvedValue(EMPTY);
});

function renderTimeline(entry = "/campaigns/run/timeline") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <PaletteProvider>
        <Hotkey />
        <CommandPalette />
        <Routes>
          <Route path="/campaigns/:cid/timeline" element={<TimelineView />} />
          <Route path="/campaigns/:cid" element={<div>the play view</div>} />
          <Route path="/campaigns/:cid/scenes/:sid" element={<div>the scene</div>} />
        </Routes>
      </PaletteProvider>
    </MemoryRouter>,
  );
}
function Hotkey() { usePaletteHotkey(); return null; }

const column = () => within(screen.getByRole("complementary"));
const cards = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("timeline-card"));
const titles = () => cards().map((c) => within(c).getByRole("heading").textContent);
const cardFor = (text: RegExp) =>
  cards().find((c) => text.test(c.textContent ?? "")) as HTMLElement;

// ---- the cards -------------------------------------------------------------

test("every scene is a card, in play order, dated and titled", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));
  // Play order, which is the server's order — not recency, and not by date.
  expect(titles()).toEqual(["First Light", "The Long Tide", "Verdigris & Ash"]);

  const first = cardFor(/First Light/);
  expect(first).toHaveTextContent("28 Sowing");
  expect(first).toHaveTextContent("They met at the pier.");
  expect(within(first).getByText("The Pier")).toBeInTheDocument();
  expect(within(first).getByText("ABSORBED")).toBeInTheDocument();
});

test("a scene that was never absorbed still gets a card, and says so", async () => {
  // The ordinary case: a campaign being played is a scene ahead of its absorb,
  // so a blank card would be the state most people see most of the time.
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  const open = await waitFor(() => cardFor(/Verdigris/));
  expect(open).toHaveTextContent(/Not absorbed yet/);
  expect(within(open).getByText("IN PLAY")).toBeInTheDocument();
});

test("a card is the way back into its scene", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  const link = await screen.findByRole("link", { name: "The Long Tide" });
  expect(link).toHaveAttribute("href", "/campaigns/run/scenes/002--the-long-tide");
});

test("beats render on the scene they landed in, each naming its thread", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  const tide = await waitFor(() => cardFor(/The Long Tide/));
  expect(tide).toHaveTextContent(/The sea wall · closed/);
  expect(tide).toHaveTextContent("Seraphine mended it.");
  expect(tide).toHaveTextContent(/A debt unpaid · open/);
  expect(tide).toHaveTextContent("And opened another.");
  // and a scene with no beats carries none rather than an empty list
  expect(cardFor(/Verdigris/).querySelector(".timeline-beats")).toBeNull();
});

// ---- the filters -----------------------------------------------------------

test("a thread chip narrows the timeline to the scenes it moved in", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));

  fireEvent.click(column().getByRole("button", { name: /a debt unpaid/i }));
  await waitFor(() => expect(titles()).toEqual(["The Long Tide"]));
  // The count beside the chip is the same number the page now shows.
  expect(column().getByRole("button", { name: /a debt unpaid/i })).toHaveTextContent("1");
});

test("threads are multi-select, and OR — two chips widen rather than narrow", async () => {
  (api.campaignTimeline as any).mockResolvedValue({
    ...PLAYED,
    scenes: [...PLAYED.scenes,
             scene({ id: "004--the-turning", title: "The Turning", date: "5 Reaping",
                     beats: [{ thread: "a-debt", title: "A debt unpaid", status: "open",
                               text: "Called in." }] })],
  });
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(4));

  fireEvent.click(column().getByRole("button", { name: /a debt unpaid/i }));
  await waitFor(() => expect(titles()).toEqual(["The Long Tide", "The Turning"]));
  fireEvent.click(column().getByRole("button", { name: /the sea wall/i }));
  await waitFor(() =>
    expect(titles()).toEqual(["First Light", "The Long Tide", "The Turning"]));
});

test("the thread chip on a beat is a filter, not a label", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  const tide = await waitFor(() => cardFor(/The Long Tide/));
  fireEvent.click(within(tide).getByRole("button", { name: /a debt unpaid/i }));
  await waitFor(() => expect(titles()).toEqual(["The Long Tide"]));
  expect(column().getByRole("button", { name: /a debt unpaid/i }))
    .toHaveAttribute("aria-pressed", "true");
});

test("absorbed and not-absorbed are the two halves of the campaign", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));

  fireEvent.click(column().getByRole("button", { name: /^not absorbed$/i }));
  await waitFor(() => expect(titles()).toEqual(["Verdigris & Ash"]));
  fireEvent.click(column().getByRole("button", { name: /^absorbed$/i }));
  await waitFor(() => expect(titles()).toEqual(["First Light", "The Long Tide"]));
});

test("the span bounds the timeline by the campaign's own moments", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));

  // The options are the dates the campaign has, in play order — there is
  // nothing to type into, because a native date is whatever the calendar
  // provider formats.
  const from = column().getByRole("combobox", { name: /from/i });
  expect(within(from).getAllByRole("option").map((o) => o.textContent))
    .toEqual(["The beginning", "28 Sowing", "3 Reaping", "4 Reaping"]);

  fireEvent.change(from, { target: { value: "1" } });        // 3 Reaping
  await waitFor(() => expect(titles()).toEqual(["The Long Tide", "Verdigris & Ash"]));
  fireEvent.change(column().getByRole("combobox", { name: /to/i }), { target: { value: "1" } });
  await waitFor(() => expect(titles()).toEqual(["The Long Tide"]));
});

test("an undated scene keeps the place the dated scene before it gave it", async () => {
  // The carry-forward. Without it every scene that has not been given a
  // datetime — most of an in-progress campaign — would drop out of any span
  // the reader picked, which is the opposite of what a span is for.
  (api.campaignTimeline as any).mockResolvedValue({
    threads: [],
    scenes: [
      scene({ id: "001", title: "Before Time", done: true }),
      scene({ id: "002", title: "First Light", date: "28 Sowing" }),
      scene({ id: "003", title: "The Small Hours" }),
      scene({ id: "004", title: "The Long Tide", date: "3 Reaping" }),
    ],
  });
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(4));

  // "up to 28 Sowing" keeps the undated scene that FOLLOWS it — it happened
  // after that date and before the next — along with everything earlier.
  fireEvent.change(column().getByRole("combobox", { name: /to/i }), { target: { value: "0" } });
  await waitFor(() =>
    expect(titles()).toEqual(["Before Time", "First Light", "The Small Hours"]));

  // And the other bound is where the scene before every dated one drops out:
  // it genuinely precedes the first known moment, so it is in no span that
  // starts at one.
  fireEvent.change(column().getByRole("combobox", { name: /from/i }), { target: { value: "0" } });
  await waitFor(() => expect(titles()).toEqual(["First Light", "The Small Hours"]));
});

test("filters that match nothing say so, and offer the way out", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));

  fireEvent.click(column().getByRole("button", { name: /a debt unpaid/i }));
  fireEvent.click(column().getByRole("button", { name: /^not absorbed$/i }));
  expect(await screen.findByText(/No scene matches these filters/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /clear them/i }));
  await waitFor(() => expect(cards()).toHaveLength(3));
});

test("the pinned control clears every filter at once, and is dead until there is one",
  async () => {
    (api.campaignTimeline as any).mockResolvedValue(PLAYED);
    renderTimeline();
    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(column().getByRole("button", { name: /no filters/i })).toBeDisabled();

    fireEvent.click(column().getByRole("button", { name: /^absorbed$/i }));
    fireEvent.click(column().getByRole("button", { name: /the sea wall/i }));
    fireEvent.click(await column().findByRole("button", { name: /clear filters/i }));
    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(column().getByRole("button", { name: /no filters/i })).toBeDisabled();
  });

// ---- the column, empty, failed and stale -----------------------------------

test("the campaign is named, and the way back to it is a link", async () => {
  renderTimeline();
  expect(await column().findByRole("link", { name: /saltmarch/i }))
    .toHaveAttribute("href", "/campaigns/run");
  expect(column().getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
});

test("a campaign with no scenes names what fills the page", async () => {
  renderTimeline();
  expect(await screen.findByText(/The timeline fills in as you play/)).toBeInTheDocument();
  // and there is no span control over zero moments
  expect(column().queryByRole("combobox", { name: /from/i })).not.toBeInTheDocument();
});

test("a campaign whose threads have never moved says so rather than showing a gap",
  async () => {
    (api.campaignTimeline as any).mockResolvedValue({
      threads: [], scenes: [scene({ id: "001", title: "First Light" })],
    });
    renderTimeline();
    expect(await column().findByText(/No thread has moved in a scene yet/)).toBeInTheDocument();
  });

test("a failed read degrades to the empty state, never a stuck reading line", async () => {
  (api.campaignTimeline as any).mockRejectedValue(new Error("nope"));
  renderTimeline();
  expect(await screen.findByText(/The timeline fills in as you play/)).toBeInTheDocument();
  expect(screen.queryByText(/Reading the timeline/)).not.toBeInTheDocument();
});

test("cards never outlive the campaign they came from", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  const { unmount } = renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));
  unmount();

  let settle: (v: unknown) => void = () => {};
  (api.campaignTimeline as any).mockReturnValue(new Promise((r) => { settle = r; }));
  renderTimeline("/campaigns/other/timeline");
  expect(await screen.findByText(/Reading the timeline/)).toBeInTheDocument();
  expect(screen.queryByText(/First Light/)).not.toBeInTheDocument();
  settle(EMPTY);
});

// ---- ⌘K --------------------------------------------------------------------

test("the threads are offered to the palette", async () => {
  (api.campaignTimeline as any).mockResolvedValue(PLAYED);
  renderTimeline();
  await waitFor(() => expect(cards()).toHaveLength(3));

  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const input = await screen.findByRole("combobox", { name: /search/i });
  fireEvent.change(input, { target: { value: "sea wall" } });
  fireEvent.click(await screen.findByRole("option", { name: /the sea wall/i }));
  await waitFor(() => expect(titles()).toEqual(["First Light", "The Long Tide"]));
});
