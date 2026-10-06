import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { agingLabel } from "../aging";

vi.mock("../api/client", async () =>
  (await import("../testkit/ledgerMocks")).ledgerApiMock());
import { api, ApiError } from "../api/client";
import LedgerView from "./LedgerView";
import { EMPTY_LEDGER, Here, installLedgerMocks, renderLedger } from "../testkit/ledgerHarness";

const scene = (id: string, title: string, date = "") => ({ id, title, date });

const EMPTY = EMPTY_LEDGER;

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

beforeEach(() => { installLedgerMocks(); });

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


const column = () => within(screen.getByRole("complementary", { name: "Ledger sections" }));
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
  // Five, not four: a section whose rows are records carries the actions
  // column, and its heading is named for a screen reader rather than left
  // blank. The two LOG sections still have four, which the editing suite
  // below checks.
  expect(heads).toEqual(["ID", "FACT", "AS OF", "SCENE", "Actions"]);
  expect(cells(rowFor(/sea wall/)).slice(0, 4)).toEqual(
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
    .toEqual(["Row", "COMMITMENT", "DUE", "SCENE", "Actions"]);
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
    ["↔", "Sister Mara ↔ The Reeve" + "REVERSED · wary allies", "sworn", "The Long Tide"]);
  expect(cells(older)).toEqual([
    "→", "Sister Mara → The Reeve" + "trust 1, affection 0, tension 4 (he took the money)",
    "trust 3, affection 2, tension 1", "The Long Tide"]);
  // The first delta on a pair replaced nothing, and says so rather than
  // rendering an empty cell that reads as a missing value.
  expect(cells(first)[2]).toBe("—");
  // REVERSED, never UNDONE: undoing an undo is a redo, and the store does not
  // claim which of the two a reversal was.
  expect(older).not.toHaveTextContent(/UNDONE/);
  expect(first).not.toHaveTextContent(/REVERSED/);   // an absorb is unbadged
});

test("the timeline narrows to one pair through the server, not the page", async () => {
  // The route caps what it returns, so filtering here would search what the cap
  // already threw away — a long campaign's older arc for one pair would be
  // unreachable in the app while the store still held every row of it.
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    relationships: [{ id: "characters:mara->characters:reeve", kind: "feeling",
                      a: "characters:mara", b: "characters:reeve",
                      a_name: "Sister Mara", b_name: "The Reeve",
                      trust: 1, affection: 0, tension: 4, note: "",
                      type: "", since_scene: "", scene: scene("", "") }],
  });
  (api.campaignRelationshipHistory as any).mockResolvedValue(STANDINGS);
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: /relationship history/i }));
  await screen.findByRole("table");
  expect(api.campaignRelationshipHistory).toHaveBeenCalledWith("run", undefined);

  const picker = screen.getByRole("combobox", { name: /narrow the timeline/i });
  fireEvent.change(picker, { target: { value: "characters:mara->characters:reeve" } });
  await waitFor(() => expect(api.campaignRelationshipHistory).toHaveBeenCalledWith(
    "run", { a: "characters:mara", b: "characters:reeve" }));

  // and back to everyone, which is a read of its own rather than a re-filter
  (api.campaignRelationshipHistory as any).mockClear();
  fireEvent.change(picker, { target: { value: "" } });
  await waitFor(() => expect(api.campaignRelationshipHistory)
    .toHaveBeenCalledWith("run", undefined));
});

test("a narrowed timeline with nothing in it says so rather than sending you to play",
  async () => {
    (api.campaignLedger as any).mockResolvedValue({
      ...EMPTY,
      relationships: [{ id: "characters:mara->characters:reeve", kind: "feeling",
                        a: "characters:mara", b: "characters:reeve",
                        a_name: "Sister Mara", b_name: "The Reeve",
                        trust: 1, affection: 0, tension: 4, note: "",
                        type: "", since_scene: "", scene: scene("", "") }],
    });
    (api.campaignRelationshipHistory as any).mockResolvedValue([]);
    renderLedger();
    fireEvent.click(await column().findByRole("button", { name: /relationship history/i }));
    fireEvent.change(await screen.findByRole("combobox", { name: /narrow the timeline/i }),
                     { target: { value: "characters:mara->characters:reeve" } });

    expect(await screen.findByText(/Nothing has passed between these two yet/))
      .toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /back to play/i })).not.toBeInTheDocument();
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

// ------------------------------------------------ merged records (§12.5)
//
// The rows are effective: a record merged into another is not a row of its
// own, and the canonical says what was folded into it on its note line.

const MERGED_THREAD = {
  id: "winifred-s-chart", title: "Winifred's chart", status: "open",
  last_scene: "002", latest_beat: "Mara traced the coast.", aging: ok,
  scene: scene("002", "The Long Tide"),
  aliases: [{ ref: "thread:mara-s-map", title: "Mara's map", status: "open" }],
};

test("a merged thread names what was merged into it", async () => {
  await at(/Threads/, { ...EMPTY, plot: [MERGED_THREAD] });
  expect(await screen.findByText(/Merged: Mara's map/)).toBeInTheDocument();
});

test("a merged commitment names what was merged into it", async () => {
  const aging = { state: "stale" as const, days_since: 45, days_over: null, due_in: null };
  await at(/Commitments/, {
    ...EMPTY,
    commitments: [{ id: "winifred-s-promise", title: "Winifred's promise", kind: "promise",
                    status: "open", due: "", last_scene: "004",
                    latest_beat: "Sworn at the gate", aging,
                    scene: scene("004", "The Priory Door"),
                    aliases: [{ ref: "commitment:mara-s-oath", title: "Mara's oath",
                                status: "open" }] }],
  });
  // The merge note is a link now (to Reviewed links / merges), so the query
  // finds the link and the whole note line is its container.
  const note = (await screen.findByText(/Merged: Mara's oath/)).closest(".ledger-note")!;
  // aging · Merged · KIND · beat: the badge still leads, and the merge note
  // sits before the record's own parts.
  expect(note.textContent).toBe(
    `${agingLabel(aging)} · Merged: Mara's oath · PROMISE · Sworn at the gate`);
});

test("a row with no aliases says nothing about merging", async () => {
  await at(/Threads/, { ...EMPTY, plot: [{ ...MERGED_THREAD, aliases: [] }] });
  expect(await screen.findByText(/Mara traced the coast\./)).toBeInTheDocument();
  expect(screen.queryByText(/Merged:/)).toBeNull();
});

test("closing a merged thread addresses the canonical", async () => {
  await at(/Threads/, { ...EMPTY, plot: [MERGED_THREAD] });
  const row = rowFor(/Winifred's chart/);
  fireEvent.click(within(row).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(api.ledgerSaveThread).toHaveBeenCalledWith(
    "run", "winifred-s-chart", { status: "closed" }));
});

// ------------------------------------------------ editing the ledger by hand
//
// Until these controls the only writer of any of this was the absorb pass, so
// a thread the model never noticed had closed stayed open forever. What is
// checked here is that the right call goes out, that the two LOG sections
// offer nothing, and that the one rule the ledger keeps about facts survives:
// grimoire never edits one, the user may.

const THREADS = {
  ...EMPTY,
  plot: [
    { id: "warehouse", title: "Who fired the warehouse", status: "open",
      last_scene: "004", latest_beat: "The Reeve asked again", aging: ok,
      scene: scene("004", "The Priory Door") },
    { id: "settled", title: "The debt, settled", status: "closed",
      last_scene: "009", latest_beat: "", aging: ok,
      scene: scene("009", "The Long Tide") },
  ],
};

const OWED = {
  ...EMPTY,
  commitments: [
    { id: "pay", title: "Pay the Reeve", status: "open", kind: "promise",
      due: "midnight", last_scene: "004", latest_beat: "", aging: ok,
      scene: scene("004", "The Priory Door") },
    { id: "done", title: "Debt repaid", status: "fulfilled", kind: "promise",
      due: "", last_scene: "009", latest_beat: "Repaid", aging: ok,
      scene: scene("009", "The Long Tide") },
  ],
};

test("completed threads and commitments show clear status when history is enabled", async () => {
  await at(/Threads/, THREADS);
  expect(screen.queryByText("The debt, settled")).toBeNull();
  fireEvent.click(column().getByRole("checkbox", { name: /show retired and completed/i }));
  const closed = rowFor(/The debt, settled/);
  expect(closed).toHaveClass("complete");
  expect(closed).toHaveTextContent("Closed");
});

test("fulfilled commitments remain identifiable in history", async () => {
  await at(/Commitments/, OWED);
  expect(screen.queryByText("Debt repaid")).toBeNull();
  fireEvent.click(column().getByRole("checkbox", { name: /show retired and completed/i }));
  const done = rowFor(/Debt repaid/);
  expect(done).toHaveClass("complete");
  expect(done).toHaveTextContent("fulfilled");
});

const BONDS = {
  ...EMPTY,
  relationships: [
    { id: "mara->reeve", kind: "feeling", a: "characters:mara", b: "characters:reeve",
      a_name: "Sister Mara", b_name: "The Reeve", trust: 3, affection: 2, tension: 1,
      note: "owes him", type: "", since_scene: "", scene: scene("", "") },
    { id: "mara|reeve", kind: "bond", a: "characters:mara", b: "characters:reeve",
      a_name: "Sister Mara", b_name: "The Reeve", trust: 0, affection: 0, tension: 0,
      note: "", type: "wary allies", since_scene: "004",
      scene: scene("004", "The Priory Door") },
  ],
};

/** Render, choose a section, and wait for that section's table.
 *
 *  In that order: the ledger opens on Standing facts, so a fixture whose facts
 *  are empty has no table to wait for until the section has moved. */
async function at(section: RegExp, data: unknown) {
  (api.campaignLedger as any).mockResolvedValue(data);
  renderLedger();
  await screen.findByRole("complementary", { name: "Ledger sections" });
  fireEvent.click(column().getByRole("button", { name: section }));
  await screen.findByRole("table");
}

/** Open one row's editor. The control is quiet until hover, but it is always
 *  in the tree and in the tab order — which is the same thing a keyboard user
 *  gets, so it is the right thing to drive. */
async function openEditor(row: HTMLElement) {
  fireEvent.click(within(row).getByRole("button", { name: /^Edit / }));
  return await screen.findByRole("button", { name: "Save" });
}

/** A fact row by its id.
 *
 *  By id rather than by text, and it matters: a row that REPLACED something
 *  quotes the superseded sentence in its own note, so matching a retired
 *  fact's text finds the row above it first. */
const factRow = (id: string) => rowById(id);

test("a thread closes from its own row, without opening anything", async () => {
  await at(/Threads/, THREADS);
  const row = rowFor(/Who fired the warehouse/);
  fireEvent.click(within(row).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(api.ledgerSaveThread).toHaveBeenCalledWith(
    "run", "warehouse", { status: "closed" }));
});

test("a thread that is already closed offers no Close", async () => {
  await at(/Threads/, THREADS);
  fireEvent.click(column().getByRole("checkbox", { name: /show retired and completed/i }));
  const row = rowFor(/The debt, settled/);
  expect(within(row).queryByRole("button", { name: "Close" })).toBeNull();
  expect(within(row).getByRole("button", { name: /^Edit / })).toBeTruthy();
});

test("editing a thread sends ONLY what changed", async () => {
  // The campaign lock serializes the writes but not the browser's earlier
  // read, so a form that posted every field made each save a last-writer-wins
  // overwrite of the record as it looked when the editor opened: an absorb
  // advancing the status in between was reverted by a save that meant to fix
  // the title. An omitted field is what makes the store keep what it holds.
  await at(/Threads/, THREADS);
  await openEditor(rowFor(/Who fired the warehouse/));
  fireEvent.change(screen.getByLabelText("Thread"), { target: { value: "Who fired the tide-house" } });
  fireEvent.change(screen.getByLabelText("Add a beat"), { target: { value: "A witness came forward" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveThread).toHaveBeenCalledWith("run", "warehouse", {
    title: "Who fired the tide-house", beat: "A witness came forward",
  }));
});

test("a field the reader never touched is not in the payload at all", async () => {
  await at(/Threads/, THREADS);
  await openEditor(rowFor(/Who fired the warehouse/));
  fireEvent.change(screen.getByLabelText("Thread"), { target: { value: "Renamed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveThread).toHaveBeenCalled());
  const body = (api.ledgerSaveThread as any).mock.calls[0][2];
  expect(Object.keys(body)).toEqual(["title"]);
});

test("a field the reader EMPTIED is sent blank, because blank is an instruction", async () => {
  // Omitted keeps a commitment's deadline; `""` clears it. The two cannot
  // collapse into one or a deadline could never be lifted.
  await at(/Commitments/, OWED);
  await openEditor(rowFor(/Pay the Reeve/));
  fireEvent.change(screen.getByLabelText("Due"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveCommitment)
    .toHaveBeenCalledWith("run", "pay", { due: "" }));
});

test("a commitment is marked done from its row", async () => {
  await at(/Commitments/, OWED);
  fireEvent.click(within(rowFor(/Pay the Reeve/)).getByRole("button", { name: "Done" }));
  await waitFor(() => expect(api.ledgerSaveCommitment).toHaveBeenCalledWith(
    "run", "pay", { status: "fulfilled" }));
});

test("a fact is retired from its row, and a retired one cannot be retired again", async () => {
  await at(/Standing facts/, CHAIN);
  fireEvent.click(within(factRow("f4")).getByRole("button", { name: "Retire" }));
  await waitFor(() => expect(api.ledgerRetireFact).toHaveBeenCalledWith("run", "f4"));
  // The superseded row is on the page whatever the toggle says, and retiring
  // something already retired is not an action this offers.
  expect(within(factRow("f2")).queryByRole("button", { name: "Retire" })).toBeNull();
});

test("the user may correct a fact wording in place", async () => {
  // The rule the module argues and the guard enforces: grimoire never edits a
  // fact, the person whose campaign it is may — a typo is not a fact that
  // stopped being true.
  await at(/Standing facts/, CHAIN);
  await openEditor(factRow("f4"));
  fireEvent.change(screen.getByLabelText("Fact"),
    { target: { value: "The priory owes the Reeve for the sea gate." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveFact).toHaveBeenCalledWith("run", "f4", {
    text: "The priory owes the Reeve for the sea gate.",
  }));
});

test("a retired fact is still correctable — retirement is about truth, not wording", async () => {
  await at(/Standing facts/, CHAIN);
  await openEditor(factRow("f2"));
  expect(screen.getByLabelText("Fact")).toBeTruthy();
});

test("deleting takes two clicks and says how it differs from retiring", async () => {
  await at(/Standing facts/, CHAIN);
  await openEditor(factRow("f4"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await screen.findByText(/should never have existed/i);
  expect(api.ledgerDeleteFact).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.ledgerDeleteFact).toHaveBeenCalledWith("run", "f4"));
});

test("a refusal is shown in the editor it belongs to", async () => {
  (api.ledgerSaveFact as any).mockRejectedValue(new Error("a fact needs text"));
  await at(/Standing facts/, CHAIN);
  await openEditor(factRow("f4"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText(/a fact needs text/);
  // ...and the editor stays open, holding what was typed.
  expect(screen.getByLabelText("Fact")).toBeTruthy();
});

test("a feeling and a bond take different fields, because they are different records", async () => {
  await at(/^Relationships/, BONDS);
  await openEditor(rowFor(/Sister Mara → The Reeve/));
  expect(screen.getByLabelText("Trust")).toBeTruthy();
  expect(screen.queryByLabelText("Bond")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  await openEditor(rowFor(/Sister Mara ↔ The Reeve/));
  expect(screen.getByLabelText("Bond")).toBeTruthy();
  expect(screen.queryByLabelText("Trust")).toBeNull();
});

test("editing a feeling keeps the pair — it is the record identity", async () => {
  await at(/^Relationships/, BONDS);
  await openEditor(rowFor(/Sister Mara → The Reeve/));
  fireEvent.change(screen.getByLabelText("Trust"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  // The meters that did not move are absent, and the route merges them from
  // the stored record — sending them as 0 reset a 4/2/1 standing to nothing.
  await waitFor(() => expect(api.ledgerSaveRelationship).toHaveBeenCalledWith("run", {
    a: "characters:mara", b: "characters:reeve", trust: 5,
  }));
});

test("a meter that is typed nonsense does not reach the store as one", async () => {
  await at(/^Relationships/, BONDS);
  await openEditor(rowFor(/Sister Mara → The Reeve/));
  // Emptied rather than left alone, so it IS dirty and does go out — as a
  // number the store can hold rather than as NaN.
  fireEvent.change(screen.getByLabelText("Trust"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveRelationship).toHaveBeenCalled());
  expect((api.ledgerSaveRelationship as any).mock.calls[0][1].trust).toBe(0);
});

test("Enter cannot submit a second time while the first save is in flight", async () => {
  // Only the buttons were disabled, so Enter twice during a slow save sent the
  // write twice — two threads, or the same beat appended twice.
  let release: (v: unknown) => void = () => {};
  (api.ledgerSaveThread as any).mockReturnValue(new Promise((r) => { release = r; }));
  await at(/Threads/, THREADS);
  await openEditor(rowFor(/Who fired the warehouse/));
  const title = screen.getByLabelText("Thread");
  fireEvent.change(title, { target: { value: "Renamed" } });
  fireEvent.keyDown(title, { key: "Enter" });
  fireEvent.keyDown(title, { key: "Enter" });
  await waitFor(() => expect(api.ledgerSaveThread).toHaveBeenCalledTimes(1));
  await act(async () => { release({ ok: true }); });
});

test("+ New opens a blank of the same form the edit uses", async () => {
  await at(/Threads/, THREADS);
  fireEvent.click(screen.getByRole("button", { name: /\+ New thread/ }));
  fireEvent.change(await screen.findByLabelText("Thread"),
    { target: { value: "Who is copying the ledger" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  // A create sends the whole form, unlike an edit: there is no stored record
  // whose untouched fields could be preserved by omitting them.
  await waitFor(() => expect(api.ledgerCreateThread).toHaveBeenCalledWith("run", {
    title: "Who is copying the ledger", status: "open", beat: "", scene: "",
  }));
});

test("the timeline line is editable and its row cannot be deleted", async () => {
  // The record belongs to a scene: dropping it is what un-absorbing that scene
  // means, not a ledger edit.
  await at(/Timeline/, {
    ...EMPTY,
    chronicle: [{ id: "004", title: "The Priory Door", date: "1 Reaping",
                  one_line: "They meet at the door" }],
  });
  await openEditor(rowFor(/They meet at the door/));
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  fireEvent.change(screen.getByLabelText("What happened"),
    { target: { value: "They meet at the tide gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.ledgerSaveChronicleLine).toHaveBeenCalledWith("run", "004", {
    one_line: "They meet at the tide gate",
  }));
});

test("the two log sections offer nothing to edit", async () => {
  // They record what happened. Editing a log falsifies history rather than
  // correcting state, and Undo is how something in them is reversed.
  (api.campaignRelationshipHistory as any).mockResolvedValue(STANDINGS);
  (api.campaignChanges as any).mockResolvedValue([
    { ref: { kind: "lore", id: "pact" }, name: "The Pact",
      fields: [{ key: "body", label: "body" }], scene: scene("004", "The Priory Door") },
  ]);
  renderLedger();
  for (const label of [/Relationship history/, /Recent changes/]) {
    fireEvent.click(column().getByRole("button", { name: label }));
    await waitFor(() => expect(rows().length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: /^Edit$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /\+ New/ })).toBeNull();
  }
});

test("only one row editor is open at a time", async () => {
  // These writes are whole-record, so two editors is one of them losing.
  await at(/Standing facts/, CHAIN);
  await openEditor(factRow("f4"));
  await openEditor(factRow("f9"));
  expect(screen.getAllByRole("button", { name: "Save" })).toHaveLength(1);
});

// ------------------------------------------------ addresses (§12.1)
//
// The section and the row a reader is looking at are in the path, so a Todo
// item, a Story Graph link or a closure applied from review can open the
// ledger on exactly that row.

/** Where the router says the page is. */
const here = () => screen.getByTestId("here").textContent;

let scrolled: Element[] = [];
let scroll = vi.fn();
beforeEach(() => {
  scrolled = [];
  // jsdom has no layout, and so no `scrollIntoView`; the view calls it
  // optionally, and this records which row it was asked of.
  scroll = vi.fn(function (this: Element) { scrolled.push(this); });
  Element.prototype.scrollIntoView = scroll;
});

const highlighted = () => document.querySelectorAll("tr.highlighted");

test("a section address opens that section", async () => {
  (api.campaignLedger as any).mockResolvedValue(OWED);
  renderLedger("/campaigns/run/ledger/commitments");
  expect(await screen.findByRole("heading", { level: 1, name: "Commitments" }))
    .toBeInTheDocument();
  expect(await screen.findByText("Pay the Reeve")).toBeInTheDocument();
});

test("clicking a section changes the address", async () => {
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: /threads/i }));
  await waitFor(() => expect(here()).toBe("/campaigns/run/ledger/threads"));
  // Standing facts is the bare ledger, not `/ledger/facts` (Decision 22).
  fireEvent.click(column().getByRole("button", { name: /standing facts/i }));
  await waitFor(() => expect(here()).toBe("/campaigns/run/ledger"));
});

test("a row address highlights that row without opening its editor", async () => {
  (api.campaignLedger as any).mockResolvedValue(THREADS);
  renderLedger("/campaigns/run/ledger/threads/warehouse");
  const row = await waitFor(() => rowFor(/Who fired the warehouse/));
  await waitFor(() => expect(row).toHaveClass("highlighted"));
  expect(row).toHaveAttribute("data-row-key", "warehouse");
  expect(highlighted()).toHaveLength(1);
  expect(scrolled).toEqual([row]);
  expect(scroll).toHaveBeenCalledWith({ block: "center" });
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(within(row).getByRole("button", { name: /^Edit / }))
    .toHaveAttribute("aria-expanded", "false");
});

test("a row address with a slash in its id highlights that row", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY,
    plot: [{ ...THREADS.plot[0], id: "mara/map", title: "Mara's map" },
           { ...THREADS.plot[0], id: "act:2", title: "The coronation" }],
  });
  renderLedger("/campaigns/run/ledger/threads/mara%2Fmap");
  const row = await waitFor(() => rowFor(/Mara's map/));
  await waitFor(() => expect(row).toHaveClass("highlighted"));
  expect(rowFor(/The coronation/)).not.toHaveClass("highlighted");
});

test("an alias source's address highlights its canonical row", async () => {
  (api.campaignLedger as any).mockResolvedValue({ ...EMPTY, plot: [MERGED_THREAD] });
  renderLedger("/campaigns/run/ledger/threads/mara-s-map");
  const row = await waitFor(() => rowFor(/Winifred's chart/));
  await waitFor(() => expect(row).toHaveClass("highlighted"));
});

test("a row address to a closed thread shows and highlights it", async () => {
  (api.campaignLedger as any).mockResolvedValue(THREADS);
  renderLedger("/campaigns/run/ledger/threads/settled");
  // Not on the first table: it is filtered out until the address reveals it.
  await waitFor(() => expect(rowFor(/The debt, settled/)).toHaveClass("highlighted"));
  expect(scrolled).toEqual([rowFor(/The debt, settled/)]);
  expect(column().getByRole("checkbox", { name: /show retired and completed/i })).toBeChecked();
  // Once, not for ever: the reader may turn it back off.
  fireEvent.click(column().getByRole("checkbox", { name: /show retired and completed/i }));
  await waitFor(() => expect(screen.queryByText("The debt, settled")).toBeNull());
});

test("re-renders do not re-scroll", async () => {
  let release: (v: unknown) => void = () => {};
  (api.ledgerSaveThread as any).mockReturnValue(new Promise((r) => { release = r; }));
  // A new object per read, as the wire gives: the re-read must rebuild the
  // table, or this would prove nothing about the effect that scrolls.
  (api.campaignLedger as any).mockImplementation(() => Promise.resolve(structuredClone(THREADS)));
  renderLedger("/campaigns/run/ledger/threads/warehouse");
  const row = await waitFor(() => rowFor(/Who fired the warehouse/));
  await waitFor(() => expect(row).toHaveClass("highlighted"));
  // A busy toggle, then an epoch re-read of the whole ledger: neither is a new
  // address, so neither may pull the page back to the row.
  fireEvent.click(within(row).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(within(rowFor(/Who fired the warehouse/))
    .getByRole("button", { name: "Close" })).toBeDisabled());
  await act(async () => { release({ ok: true }); });
  await waitFor(() => expect(api.campaignLedger).toHaveBeenCalledTimes(2));
  expect(scroll).toHaveBeenCalledTimes(1);
});

/** The ledger with a button that moves the SAME mounted page to `to` -- the
 *  route is not keyed on `cid`, so a campaign switch is a re-render, not a
 *  remount, and that is the case these tests need. */
function renderLedgerThenGo(from: string, to: string) {
  function Go() {
    const navigate = useNavigate();
    return <button onClick={() => navigate(to)}>go</button>;
  }
  return render(
    <MemoryRouter initialEntries={[from]}>
      <Go />
      <Here />
      <Routes>
        <Route path="/campaigns/:cid/ledger/*" element={<LedgerView />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Two campaigns holding a thread under the same id: open in the first, and
 *  as `second` says in the other. */
function twoCampaigns(second: "open" | "closed") {
  const thread = (cid: string, status: string) => ({
    ...EMPTY,
    plot: [{ ...THREADS.plot[0] },
           { ...THREADS.plot[1], status, title: `The debt, in ${cid}` }],
  });
  (api.campaignLedger as any).mockImplementation((cid: string) => Promise.resolve(
    cid === "run" ? thread("run", "open") : thread("tide", second)));
}

test("the same address in another campaign reveals its closed row", async () => {
  twoCampaigns("closed");
  renderLedgerThenGo("/campaigns/run/ledger/threads/settled",
                     "/campaigns/tide/ledger/threads/settled");
  await waitFor(() => expect(rowFor(/The debt, in run/)).toHaveClass("highlighted"));
  const toggle = () => column().getByRole("checkbox", { name: /show retired and completed/i });
  expect(toggle()).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(here()).toBe("/campaigns/tide/ledger/threads/settled"));
  // Handled once per CAMPAIGN's address: this is a row nobody has been shown.
  await waitFor(() => expect(rowFor(/The debt, in tide/)).toHaveClass("highlighted"));
  expect(toggle()).toBeChecked();
  expect(scrolled[scrolled.length - 1]).toBe(rowFor(/The debt, in tide/));
});

test("the same address in another campaign scrolls to its row", async () => {
  twoCampaigns("open");
  renderLedgerThenGo("/campaigns/run/ledger/threads/settled",
                     "/campaigns/tide/ledger/threads/settled");
  await waitFor(() => expect(rowFor(/The debt, in run/)).toHaveClass("highlighted"));
  expect(scrolled).toEqual([rowFor(/The debt, in run/)]);
  fireEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(rowFor(/The debt, in tide/)).toHaveClass("highlighted"));
  await waitFor(() => expect(scroll).toHaveBeenCalledTimes(2));
  expect(scrolled[1]).toBe(rowFor(/The debt, in tide/));
});

test("an unknown section redirects to the facts", async () => {
  (api.campaignLedger as any).mockResolvedValue(CHAIN);
  renderLedger("/campaigns/run/ledger/nonsense");
  await waitFor(() => expect(here()).toBe("/campaigns/run/ledger"));
  expect(await screen.findByRole("heading", { level: 1, name: "Standing facts" }))
    .toBeInTheDocument();
});

test("the Merged note links to Reviewed links / merges", async () => {
  await at(/Threads/, { ...EMPTY, plot: [MERGED_THREAD] });
  const link = await screen.findByRole("link", { name: "Merged: Mara's map" });
  expect(link).toHaveAttribute("href", "/campaigns/run/ledger/continuity/reviewed");
});

test("a delete refused for merged records offers Delete anyway", async () => {
  (api.ledgerDeleteThread as any).mockRejectedValueOnce(new ApiError(
    409, "thread:warehouse has merged records", "has_merged_records",
    { kind: "has_merged_records", detail: "thread:warehouse has merged records" }));
  await at(/Threads/, THREADS);
  await openEditor(rowFor(/Who fired the warehouse/));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(await screen.findByText(
    "This record has merged records: unmerge them first, or delete anyway"))
    .toBeInTheDocument();
  expect(api.ledgerDeleteThread).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Delete anyway" }));
  await waitFor(() => expect(api.ledgerDeleteThread).toHaveBeenCalledTimes(2));
  expect((api.ledgerDeleteThread as any).mock.calls[1]).toEqual(["run", "warehouse", true]);
});

const THREAD_PARTIAL = "the thread was deleted, but its merges and links could not all be "
  + "removed (OSError); the delete can be undone";

test("a delete that landed but could not clear its links re-reads the ledger", async () => {
  // The thread is gone on the server, so a row drawn from the pre-delete read
  // would answer a second Delete with a 404. Re-read, and still say what
  // happened.
  (api.ledgerDeleteThread as any).mockRejectedValueOnce(new ApiError(
    500, THREAD_PARTIAL, "partial_delete",
    { kind: "partial_delete", landed: ["thread"], detail: THREAD_PARTIAL }));
  await at(/Threads/, THREADS);
  (api.campaignLedger as any).mockResolvedValue({ ...THREADS, plot: THREADS.plot.slice(1) });
  await openEditor(rowFor(/Who fired the warehouse/));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(await screen.findByText(THREAD_PARTIAL)).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/Who fired the warehouse/)).toBeNull());
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(api.campaignLedger).toHaveBeenCalledTimes(2);
});

test("a delete refused for another reason leaves the row and reads nothing again", async () => {
  (api.ledgerDeleteThread as any).mockRejectedValueOnce(new ApiError(
    409, "continuity.json cannot be read", "malformed",
    { kind: "malformed", detail: "continuity.json cannot be read" }));
  await at(/Threads/, THREADS);
  await openEditor(rowFor(/Who fired the warehouse/));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(await screen.findByText("continuity.json cannot be read")).toBeInTheDocument();
  expect(rowFor(/Who fired the warehouse/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  expect(api.campaignLedger).toHaveBeenCalledTimes(1);
});
