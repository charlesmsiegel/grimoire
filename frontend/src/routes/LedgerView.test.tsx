import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import LedgerView from "./LedgerView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    campaignLedger: vi.fn(),
    campaignChanges: vi.fn(),
    campaignRelationshipHistory: vi.fn(),
  },
}));
import { api } from "../api/client";

const scene = (id: string, title: string, date = "") => ({ id, title, date });

const EMPTY = {
  plot: [], commitments: [], facts: [], retired: [], relationships: [], chronicle: [],
  stale_after_days: 30,
};

/** Aging (#103) as the route returns it: computed at read time, never stored. */
const ok = { state: "ok", days_since: 2, days_over: null, due_in: null };

/** The screen's reason to exist, in data.
 *
 *  f4 and f7 simply stand. f9 replaced f2 — the pair the table has to show as a
 *  pair — and f5 was retired outright, with nothing in its place, which is the
 *  only row the toggle governs. */
const CHAIN = {
  ...EMPTY,
  facts: [
    { id: "f4", text: "Mara's priory owes the Reeve for the sea wall.",
      date: "1 Reaping", scene: scene("004", "The Priory Door", "1 Reaping") },
    { id: "f9", text: "Mara will not speak of the drowned aloud.",
      date: "3 Reaping", scene: scene("009", "The Long Tide", "3 Reaping") },
    { id: "f7", text: "Wyle carries the boat-nail out of the flats.",
      date: "4 Reaping", scene: scene("011", "Verdigris & Ash", "4 Reaping") },
  ],
  retired: [
    { id: "f2", text: "Mara speaks of the drowned freely.", date: "28 Sowing",
      scene: scene("002", "First Light", "28 Sowing"),
      superseded_by: "f9", retired_scene: scene("009", "The Long Tide", "3 Reaping") },
    { id: "f5", text: "The gate is watched.", date: "2 Reaping",
      scene: scene("005", "The Watch", "2 Reaping"),
      superseded_by: "", retired_scene: scene("008", "The Turning", "3 Reaping") },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch" }, body: "" });
  (api.campaignLedger as any).mockResolvedValue(EMPTY);
  (api.campaignChanges as any).mockResolvedValue([]);
  (api.campaignRelationshipHistory as any).mockResolvedValue([]);
});

/** The relationship timeline (#63) as the route returns it: newest first, with
 *  the standing each delta replaced. */
const STANDINGS = [
  { id: "rh3", ts: "2026-07-04T10:00:00Z", source: "undo", kind: "bond",
    a: "characters:mara", b: "characters:reeve", a_name: "Sister Mara",
    b_name: "The Reeve", label: "Sister Mara & The Reeve",
    before: "sworn", after: "wary allies", scene: scene("009", "The Long Tide") },
  { id: "rh2", ts: "2026-07-03T10:00:00Z", source: "absorb", kind: "feeling",
    a: "characters:mara", b: "characters:reeve", a_name: "Sister Mara",
    b_name: "The Reeve", label: "Sister Mara → The Reeve",
    before: "trust 3, affection 2, tension 1",
    after: "trust 1, affection 0, tension 4 (he took the money)",
    scene: scene("009", "The Long Tide") },
  { id: "rh1", ts: "2026-07-01T10:00:00Z", source: "absorb", kind: "feeling",
    a: "characters:mara", b: "characters:reeve", a_name: "Sister Mara",
    b_name: "The Reeve", label: "Sister Mara → The Reeve",
    before: "", after: "trust 3, affection 2, tension 1",
    scene: scene("004", "The Priory Door") },
];

function renderLedger(entry = "/campaigns/run/ledger") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <PaletteProvider>
        <Hotkey />
        <CommandPalette />
        <Routes>
          <Route path="/campaigns/:cid/ledger" element={<LedgerView />} />
          <Route path="/campaigns/:cid" element={<div>the play view</div>} />
        </Routes>
      </PaletteProvider>
    </MemoryRouter>,
  );
}
function Hotkey() { usePaletteHotkey(); return null; }

const column = () => within(screen.getByRole("complementary"));
const rows = () => screen.getAllByRole("row").slice(1);   // minus the header row
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
const rowFor = (text: RegExp) =>
  rows().find((r) => text.test(r.textContent ?? "")) as HTMLElement;
/** By the id in the first cell. A fact's text appears twice on this table by
 *  design — once as its own row, once quoted on the row that superseded it —
 *  so matching a chain row on its words picks whichever comes first. */
const rowById = (id: string) =>
  rows().find((r) => within(r).getAllByRole("cell")[0].textContent === id) as HTMLElement;

// ---- the column ------------------------------------------------------------

test("the column lists the seven sections with a live count each", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...CHAIN,
    plot: [{ id: "t", title: "The sea wall", status: "open", last_scene: "009",
             latest_beat: "Mara named it aloud.", scene: scene("009", "The Long Tide") }],
    commitments: [{ id: "c", title: "The Reeve's deadline", kind: "threat", status: "open",
                    due: "midnight", last_scene: "009", latest_beat: "Sworn.",
                    scene: scene("009", "The Long Tide") }],
    relationships: [{ id: "characters:mara->characters:reeve", kind: "feeling",
                      a: "characters:mara", b: "characters:reeve",
                      a_name: "Sister Mara", b_name: "The Reeve",
                      trust: 1, affection: 0, tension: 4, note: "He took the money.",
                      type: "", since_scene: "", scene: scene("", "") }],
    chronicle: [{ id: "009", one_line: "They argued.", date: "3 Reaping", title: "The Long Tide" }],
  });
  (api.campaignChanges as any).mockResolvedValue([
    { ref: { kind: "characters", id: "mara" }, name: "Sister Mara",
      scene: scene("009", "The Long Tide"), fields: [{ field: "current_state",
        label: "Current state", diff: [] }] },
  ]);
  (api.campaignRelationshipHistory as any).mockResolvedValue(STANDINGS);
  renderLedger();

  const facts = await column().findByRole("button", { name: /standing facts/i });
  // Four rows: three standing facts plus the superseded one that keeps its place.
  expect(facts).toHaveTextContent("4");
  expect(column().getByRole("button", { name: /threads/i })).toHaveTextContent("1");
  expect(column().getByRole("button", { name: /commitments/i })).toHaveTextContent("1");
  expect(column().getByRole("button", { name: /relationships/i })).toHaveTextContent("1");
  expect(column().getByRole("button", { name: /recent changes/i })).toHaveTextContent("1");
  expect(column().getByRole("button", { name: /timeline/i })).toHaveTextContent("1");
  expect(column().getByRole("button", { name: /relationship history/i }))
    .toHaveTextContent("3");
});

test("the campaign is named, and the way back to it is a link", async () => {
  renderLedger();
  expect(await column().findByRole("link", { name: /saltmarch/i }))
    .toHaveAttribute("href", "/campaigns/run");
  expect(column().getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
});

// ---- the table -------------------------------------------------------------

test("standing facts render as a real table: id, fact, as of, scene", async () => {
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  renderLedger();
  await screen.findByRole("table");

  const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(heads).toEqual(["ID", "FACT", "AS OF", "SCENE"]);
  expect(cells(rowFor(/sea wall/))).toEqual(
    ["f4", "Mara's priory owes the Reeve for the sea wall.", "1 Reaping", "The Priory Door"]);
});

test("a superseded fact keeps its row without the toggle, struck through and annotated",
  async () => {
    // The judgement this screen turns on: SHOW RETIRED starts OFF, and the fact
    // f9 overturned is on the page anyway. Hiding it by default would hide the
    // one thing facts.json keeps that a snapshot cannot.
    (api.campaignLedger as any).mockResolvedValue(CHAIN);
    renderLedger();
    await screen.findByRole("table");
    expect(column().getByRole("checkbox", { name: /show retired/i })).not.toBeChecked();

    const gone = rowById("f2");
    expect(gone).toHaveTextContent(/Mara speaks of the drowned freely./);
    expect(gone).toHaveClass("retired");
    expect(gone).toHaveTextContent(/RETIRED IN The Long Tide · REPLACED BY f9/);
    // and it is dated where it was RECORDED, not where it was ended: a retired
    // fact keeps the place in the ledger it was written into.
    expect(cells(gone)[2]).toBe("28 Sowing");
    expect(cells(gone)[3]).toBe("First Light");
  });

test("the fact that replaced it names what it replaced, and sits directly above it",
  async () => {
    (api.campaignLedger as any).mockResolvedValue(CHAIN);
    renderLedger();
    await screen.findByRole("table");

    const replacing = rowFor(/will not speak of the drowned aloud/);
    expect(replacing).toHaveTextContent(/SUPERSEDED f2 · “Mara speaks of the drowned freely.”/);
    expect(replacing).not.toHaveClass("retired");
    // Adjacency is the whole point: the pair is one sentence about the world
    // changing, and reading half of it in date order says nothing.
    const order = rows().map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(order).toEqual(["f4", "f9", "f2", "f7"]);
  });

test("a fact retired with nothing in its place is what the toggle is for", async () => {
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  renderLedger();
  await screen.findByRole("table");
  expect(screen.queryByText(/The gate is watched/)).not.toBeInTheDocument();

  fireEvent.click(column().getByRole("checkbox", { name: /show retired/i }));
  const lapsed = await waitFor(() => rowFor(/The gate is watched/));
  expect(lapsed).toHaveClass("retired");
  // It ended, and nothing replaced it — so the row says only that.
  expect(lapsed).toHaveTextContent(/RETIRED IN The Turning/);
  expect(lapsed).not.toHaveTextContent(/REPLACED BY/);
  // and the count follows the table rather than disagreeing with it
  expect(column().getByRole("button", { name: /standing facts/i })).toHaveTextContent("5");
});

test("a chain three deep reads newest to oldest, each link under the one that ended it",
  async () => {
    (api.campaignLedger as any).mockResolvedValue({
      ...EMPTY,
      facts: [{ id: "f3", text: "The bridge is rubble.", date: "", scene: scene("3", "Third") }],
      retired: [
        { id: "f1", text: "The bridge stands.", date: "", scene: scene("1", "First"),
          superseded_by: "f2", retired_scene: scene("2", "Second") },
        { id: "f2", text: "The bridge is closed.", date: "", scene: scene("2", "Second"),
          superseded_by: "f3", retired_scene: scene("3", "Third") },
      ],
    });
    renderLedger();
    await screen.findByRole("table");
    expect(rows().map((r) => within(r).getAllByRole("cell")[0].textContent))
      .toEqual(["f3", "f2", "f1"]);
  });

test("a supersession written in a circle by hand renders instead of hanging", async () => {
  // facts.json is hand-editable, so f1←f2 and f2←f1 can both be on disk. The
  // walk has to stop; an infinite loop is not a degraded row.
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    retired: [
      { id: "f1", text: "One.", date: "", scene: scene("1", "First"),
        superseded_by: "f2", retired_scene: scene("2", "Second") },
      { id: "f2", text: "Two.", date: "", scene: scene("2", "Second"),
        superseded_by: "f1", retired_scene: scene("1", "First") },
    ],
  });
  renderLedger();
  fireEvent.click(await column().findByRole("checkbox", { name: /show retired/i }));
  await waitFor(() => expect(rows()).toHaveLength(2));
});

// ---- the other five sections ----------------------------------------------

test("choosing a section swaps the table and its column labels", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...CHAIN,
    commitments: [{ id: "c", title: "The Reeve's deadline", kind: "threat", status: "open",
                    due: "midnight", last_scene: "009", latest_beat: "Sworn at the pier.",
                    scene: scene("009", "The Long Tide") }],
  });
  renderLedger();
  await screen.findByRole("table");

  fireEvent.click(column().getByRole("button", { name: /commitments/i }));
  expect(await screen.findByRole("heading", { level: 1, name: "Commitments" }))
    .toBeInTheDocument();
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Row", "COMMITMENT", "DUE", "SCENE"]);
  const owed = rowFor(/Reeve's deadline/);
  expect(owed).toHaveTextContent(/THREAT · Sworn at the pier./);
  expect(cells(owed)[2]).toBe("midnight");
  // A threat is the one thing here owed against you, and carries the alert mark.
  expect(within(owed).getAllByRole("cell")[0]).toHaveClass("alert");
});

test("relationships show both shapes: a directed meter and a dated bond", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    relationships: [
      { id: "characters:mara->characters:reeve", kind: "feeling",
        a: "characters:mara", b: "characters:reeve", a_name: "Sister Mara",
        b_name: "The Reeve", trust: 1, affection: 0, tension: 4,
        note: "He took the money.", type: "", since_scene: "", scene: scene("", "") },
      { id: "characters:mara|characters:wyle", kind: "bond",
        a: "characters:mara", b: "characters:wyle", a_name: "Sister Mara",
        b_name: "Ferrant Wyle", trust: 0, affection: 0, tension: 0, note: "",
        type: "kin", since_scene: "004", scene: scene("004", "The Priory Door") },
    ],
  });
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: /relationships/i }));

  const feeling = await waitFor(() => rowFor(/Sister Mara → The Reeve/));
  expect(feeling).toHaveTextContent(/TRUST 1 · AFFECTION 0 · TENSION 4 · He took the money./);
  const bond = rowFor(/Sister Mara ↔ Ferrant Wyle/);
  expect(bond).toHaveTextContent(/KIN/);
  expect(cells(bond)[3]).toBe("The Priory Door");
});

test("relationship history is the arc the current standing overwrote", async () => {
  // The section's reason to exist: `relationships.json` keeps only the far end
  // of this, and the two feeling rows are the same pair a scene apart.
  (api.campaignRelationshipHistory as any).mockResolvedValue(STANDINGS);
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: /relationship history/i }));

  expect(await screen.findByRole("heading", { level: 1, name: "Relationship history" }))
    .toBeInTheDocument();
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Row", "BETWEEN", "WAS", "SCENE"]);

  const [newest, older, first] = rows();
  expect(cells(newest)).toEqual(
    ["↔", "Sister Mara ↔ The Reeve" + "UNDONE · wary allies", "sworn", "The Long Tide"]);
  expect(cells(older)).toEqual([
    "→", "Sister Mara → The Reeve" + "trust 1, affection 0, tension 4 (he took the money)",
    "trust 3, affection 2, tension 1", "The Long Tide"]);
  // The first delta on a pair replaced nothing, and says so rather than
  // rendering an empty cell that reads as a missing value.
  expect(cells(first)[2]).toBe("—");
});

test("a broken relationship-history read costs its section and nothing else", async () => {
  (api.campaignRelationshipHistory as any).mockRejectedValue(new Error("nope"));
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  renderLedger();
  await screen.findByRole("table");

  fireEvent.click(column().getByRole("button", { name: /relationship history/i }));
  expect(await screen.findByText(/Every feeling and bond an absorb applies is kept here/))
    .toBeInTheDocument();
  // the ledger's own sections are untouched
  fireEvent.click(column().getByRole("button", { name: /standing facts/i }));
  expect(await waitFor(() => rowFor(/sea wall/))).toBeInTheDocument();
});

test("recent changes and the timeline come from their own reads", async () => {
  (api.campaignChanges as any).mockResolvedValue([
    { ref: { kind: "characters", id: "mara" }, name: "Sister Mara",
      scene: scene("009", "The Long Tide"),
      fields: [{ field: "current_state", label: "Current state", diff: [] }] },
  ]);
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    chronicle: [{ id: "009", one_line: "They argued until the tide turned.",
                  date: "3 Reaping", title: "The Long Tide" }],
  });
  renderLedger();

  fireEvent.click(await column().findByRole("button", { name: /recent changes/i }));
  expect(await waitFor(() => rowFor(/Sister Mara/))).toHaveTextContent(/Current state/);

  fireEvent.click(column().getByRole("button", { name: /timeline/i }));
  const beat = await waitFor(() => rowFor(/tide turned/));
  expect(cells(beat)[2]).toBe("3 Reaping");
  expect(cells(beat)[3]).toBe("The Long Tide");
});

// ---- empty, failed, and stale ---------------------------------------------

test("an empty section names what fills it rather than saying nothing here", async () => {
  renderLedger();
  expect(await screen.findByText(/Absorbing a scene records the truths/))
    .toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();

  fireEvent.click(column().getByRole("button", { name: /threads/i }));
  expect(await screen.findByText(/A thread opens when a scene leaves something in motion/))
    .toBeInTheDocument();
});

test("a failed read degrades to the empty state, never a stuck reading line", async () => {
  (api.campaignLedger as any).mockRejectedValue(new Error("nope"));
  (api.campaignChanges as any).mockRejectedValue(new Error("nope"));
  renderLedger();
  expect(await screen.findByText(/Absorbing a scene records the truths/)).toBeInTheDocument();
  expect(screen.queryByText(/Reading the ledger/)).not.toBeInTheDocument();
});

test("rows never outlive the campaign they came from", async () => {
  // The route is not keyed on the campaign, so a switch keeps this component
  // mounted: without the held cid, one game's facts would sit under the other's
  // name until the new read settled.
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  const { unmount } = renderLedger();
  await screen.findByRole("table");
  unmount();

  let settle: (v: unknown) => void = () => {};
  (api.campaignLedger as any).mockReturnValue(new Promise((r) => { settle = r; }));
  renderLedger("/campaigns/other/ledger");
  expect(await screen.findByText(/Reading the ledger/)).toBeInTheDocument();
  expect(screen.queryByText(/speaks of the drowned freely/)).not.toBeInTheDocument();
  settle(EMPTY);
});

// ---- ⌘K -------------------------------------------------------------------

test("the sections are offered to the palette", async () => {
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  renderLedger();
  await screen.findByRole("table");

  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const input = await screen.findByRole("combobox", { name: /search/i });
  fireEvent.change(input, { target: { value: "commitments" } });
  fireEvent.click(await screen.findByRole("option", { name: /commitments/i }));
  expect(await screen.findByRole("heading", { level: 1, name: "Commitments" }))
    .toBeInTheDocument();
});

test("an overdue commitment says how far past its deadline it is", async () => {
  // The badge leads the note: "overdue by 12 days" is the reason to read the
  // row, and a reader scanning for what has slipped should not have to reach
  // the end of a beat to find it.
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    commitments: [{ id: "the-debt", title: "Repay the moneylender", kind: "promise",
                    status: "open", due: "3 Reaping", last_scene: "004",
                    latest_beat: "Mara swore it.", scene: scene("004", "The Priory Door"),
                    aging: { state: "overdue", days_since: 40, days_over: 12, due_in: null } }],
  });
  renderLedger();
  fireEvent.click(await column().findByText("Commitments"));
  expect(await screen.findByText(/OVERDUE BY 12 DAYS/)).toBeInTheDocument();
  expect(screen.getByText(/Mara swore it\./)).toBeInTheDocument();
});

test("a thread nobody has touched is badged stale", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    plot: [{ id: "the-map", title: "The map", status: "open", last_scene: "004",
             latest_beat: "", scene: scene("004", "The Priory Door"),
             aging: { state: "stale", days_since: 45, days_over: null, due_in: null } }],
  });
  renderLedger();
  fireEvent.click(await column().findByText("Threads"));
  expect(await screen.findByText("STALE · 45 DAYS UNTOUCHED")).toBeInTheDocument();
});

test("a record inside the campaign's patience carries no badge", async () => {
  // An unbadged row is also what "cannot tell" looks like — no clock, no dated
  // scene — which is the honest rendering of an answer nothing supports.
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    plot: [{ id: "the-map", title: "The map", status: "open", last_scene: "004",
             latest_beat: "Mara found it.", scene: scene("004", "The Priory Door"),
             aging: ok }],
  });
  renderLedger();
  fireEvent.click(await column().findByText("Threads"));
  expect(await screen.findByText("Mara found it.")).toBeInTheDocument();
  expect(screen.queryByText(/STALE/)).not.toBeInTheDocument();
});
