import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useCallback } from "react";
import CommandPalette, { usePaletteHotkey } from "./CommandPalette";
import { PaletteProvider, highlight, matches, usePaletteSource, type PaletteItem } from "./palette";

const CAST: PaletteItem[] = [
  { id: "c:aud", group: "IN THIS CAMPAIGN", label: "Sister Aud",
    meta: "character · in scene", badge: "SA", to: "/campaigns/saltmarch#aud" },
  { id: "t:debt", group: "IN THIS CAMPAIGN", label: "Aud's debt to the priory",
    meta: "thread · open", to: "/campaigns/saltmarch/ledger" },
];
const SCENES: PaletteItem[] = [
  { id: "s:iv", group: "SCENES", label: "The Priory Door",
    meta: "where Aud first appears", to: "/campaigns/saltmarch/scenes/iv" },
];
const ELSEWHERE: PaletteItem[] = [
  { id: "w:aud", group: "ELSEWHERE", label: "Sister Aud", meta: "world record · saltmarch",
    to: "/worlds/saltmarch" },
];

let added: string[] = [];

/** A page that contributes, exactly as CampaignView will. */
function Page({ items }: { items: PaletteItem[] }) {
  const source = useCallback(() => items, [items]);
  usePaletteSource(source);
  return null;
}

function Harness({ sources = [CAST, SCENES, ELSEWHERE] }: { sources?: PaletteItem[][] }) {
  usePaletteHotkey();
  return (
    <>
      {sources.map((items, i) => <Page key={i} items={items} />)}
      <CommandPalette />
      <Routes>
        <Route path="/" element={<h1>Home</h1>} />
        <Route path="/worlds/:wid" element={<h1>World</h1>} />
        <Route path="/campaigns/saltmarch/scenes/:sid" element={<h1>Scene</h1>} />
        <Route path="/campaigns/saltmarch/ledger" element={<h1>Ledger</h1>} />
      </Routes>
    </>
  );
}

function open(sources?: PaletteItem[][]) {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <PaletteProvider><Harness sources={sources} /></PaletteProvider>
    </MemoryRouter>,
  );
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  return screen.getByRole("combobox", { name: /search/i });
}

beforeEach(() => { added = []; });

test("is closed until ⌘K — it is not persistent nav wearing a shortcut", () => {
  render(
    <MemoryRouter><PaletteProvider><Harness /></PaletteProvider></MemoryRouter>,
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("Ctrl-K opens it too, for the platform that is not a Mac", () => {
  render(
    <MemoryRouter><PaletteProvider><Harness /></PaletteProvider></MemoryRouter>,
  );
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  expect(screen.getByRole("dialog", { name: /go anywhere/i })).toBeInTheDocument();
});

test("groups the campaign first, then its scenes, then everywhere else", () => {
  // A name that exists in three places has to read as three different offers,
  // not one ambiguous list — that ordering is the whole grouping rule.
  open([ELSEWHERE, SCENES, CAST]);   // registered in the *wrong* order on purpose
  const headings = Array.from(document.querySelectorAll(".palette-group"))
    .map((el) => el.textContent);
  expect(headings).toEqual(["IN THIS CAMPAIGN", "SCENES", "ELSEWHERE"]);
});

test("filters on the label and on the meta line", () => {
  const input = open();
  fireEvent.change(input, { target: { value: "aud" } });
  // Sister Aud twice — the campaign's cast and the world's roster are two
  // different offers about the same person, and the grouping is what says so.
  expect(screen.getAllByRole("option", { name: /sister aud/i })).toHaveLength(2);
  // "The Priory Door" matches only through its meta, and that is still a hit
  // worth showing rather than a name the search cannot find.
  expect(screen.getByRole("option", { name: /priory door/i })).toBeInTheDocument();

  fireEvent.change(input, { target: { value: "zzz" } });
  expect(screen.queryAllByRole("option")).toHaveLength(0);
  expect(screen.getByText(/nothing matches/i)).toBeInTheDocument();
});

test("↑↓ wrap around the list and ⏎ opens the selected row", () => {
  const input = open();
  fireEvent.change(input, { target: { value: "aud" } });
  // Three hits: the cast row, the thread, and the world record. Up from the
  // first wraps to the last, which is the world record.
  fireEvent.keyDown(input, { key: "ArrowUp" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByRole("heading", { name: "World" })).toBeInTheDocument();
  // it closed behind itself
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("⏎ on a scene goes to that scene", () => {
  const input = open();
  fireEvent.change(input, { target: { value: "priory door" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByRole("heading", { name: "Scene" })).toBeInTheDocument();
});

test("Esc closes without going anywhere", () => {
  const input = open();
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Home" })).toBeInTheDocument();
});

test("an action row runs rather than navigates, and says so", () => {
  const ACTION: PaletteItem[] = [{
    id: "a:add", group: "ELSEWHERE", label: "Add Aud to this scene",
    action: true, run: () => added.push("aud"),
  }];
  const input = open([ACTION]);
  fireEvent.change(input, { target: { value: "add aud" } });
  const row = screen.getByRole("option", { name: /add aud/i });
  expect(row).toHaveTextContent("ACTION");
  fireEvent.click(row);
  expect(added).toEqual(["aud"]);
  expect(screen.getByRole("heading", { name: "Home" })).toBeInTheDocument();
});

test("two sources offering the same id yield one row, not two", () => {
  const dupe: PaletteItem[] = [CAST[0]];
  const input = open([CAST, dupe]);
  fireEvent.change(input, { target: { value: "sister aud" } });
  expect(screen.getAllByRole("option", { name: /sister aud/i })).toHaveLength(1);
});

test("a shrinking result list cannot leave the cursor past the end", () => {
  // Otherwise ⏎ opens nothing and no row looks selected.
  const input = open();
  fireEvent.change(input, { target: { value: "aud" } });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.change(input, { target: { value: "debt" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByRole("heading", { name: "Ledger" })).toBeInTheDocument();
});

test("reopening starts empty — it does not remember last night's query", () => {
  const input = open();
  fireEvent.change(input, { target: { value: "aud" } });
  fireEvent.keyDown(input, { key: "Escape" });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(screen.getByRole("combobox", { name: /search/i })).toHaveValue("");
});

test("highlight splits the label around the match, and copes with a miss", () => {
  expect(highlight("Sister Aud", "aud")).toEqual(["Sister ", "Aud", ""]);
  expect(highlight("Sister Aud", "")).toEqual(["Sister Aud", "", ""]);
  // matched through the meta line: nothing in the label to highlight
  expect(highlight("The Priory Door", "aud")).toEqual(["The Priory Door", "", ""]);
});

test("matches is substring, case-insensitive, over label and meta", () => {
  const item = CAST[0];
  expect(matches(item, "AUD")).toBe(true);
  expect(matches(item, "in scene")).toBe(true);
  expect(matches(item, "")).toBe(true);
  expect(matches(item, "reeve")).toBe(false);
});
