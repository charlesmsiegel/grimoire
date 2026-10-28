import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { GreetingEditor } from "./GreetingEditor";

vi.mock("../api/client", async () => (await import("../testkit/greetingEditorMocks")).greetingApiMock());
import { api } from "../api/client";
import type { CharacterSummary, Greeting } from "../api/types";
import { fixture } from "../testkit/fixture";
import { Wrap, GREETINGS_DEFAULTS, greetingsWith, renderGreetings, greetingFixture, deferred,
         greeting, mockOpenWithImage, fail, installGreetingMocks } from "../testkit/greetingEditorHarness";

beforeEach(installGreetingMocks);

test("the greetings root browses and its addressed new form can be cancelled", async () => {
  function AtAddress() {
    const location = useLocation();
    return <GreetingEditor {...GREETINGS_DEFAULTS} isNew={location.search === "?new=1"} />;
  }
  render(<MemoryRouter initialEntries={["/worlds/realm/greetings"]}><AtAddress /></MemoryRouter>);
  expect(await screen.findByText(/Choose a greeting to read/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "+ New greeting" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  expect(await screen.findByText(/Choose a greeting to read/)).toBeInTheDocument();
  expect(api.createGreeting).not.toHaveBeenCalled();
});
test("a greeting row is a link", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "tide-watch", name: "Tide Watch", character: "", version: "", present: [],
      requires_tags: [], predecessor_join: "all" },
  ]));
  renderGreetings({ selected: null });
  expect(await screen.findByRole("link", { name: /Tide Watch/ }))
    .toHaveAttribute("href", "/worlds/realm/greetings/tide-watch");
});

test("a bookmarked address that fails to read reports the failure instead of leaving a blank form", async () => {
  vi.mocked(api.readGreeting).mockRejectedValueOnce(new Error("not found"));
  renderGreetings({ selected: "ghost" });
  await screen.findByText("Error: not found");
});

test("leaving a greeting for the section root survives its own late read", async () => {
  const slow = deferred<any>();
  vi.mocked(api.readGreeting).mockImplementationOnce(() => slow.promise);
  const { rerender } = renderGreetings({ selected: "tide-watch" });
  rerender(greetingsWith({ selected: null }));
  slow.resolve(greetingFixture("tide-watch", "Tide Watch"));
  await screen.findByRole("link", { name: /New greeting/ });
  expect(screen.queryByRole("heading", { name: "Tide Watch" })).toBeNull();
});

// --- rail search and mark filters ------------------------------------------

const CAST = [
  { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  { id: "winifred", name: "Winifred", default_version: "default", versions: [{ id: "default", name: "default" }] },
  { id: "mara", name: "Mara", default_version: "default", versions: [{ id: "default", name: "default" }] },
];
/** name, source character, present characters, mark */
const RAIL = fixture<Greeting[]>([
  greeting("dawn", "Saltmarch Dawn", "seraphine", []),
  greeting("ledger", "The Ledger", "winifred", ["mara"], "played"),
  greeting("word", "A Quiet Word", "mara", [], "skipped"),
  greeting("vow", "Vow of Silence", "winifred", [], "completed"),
]);
const railOf = (c: HTMLElement) => c.querySelector(".editor-list") as HTMLElement;

test("mark chips hide their group, and are absent in world scope", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container } = render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");

  // everything is listed by default -- a rail that silently starts short is worse
  expect(within(rail).getByText("The Ledger")).toBeInTheDocument();
  const skipChip = within(rail).getByRole("button", { name: /^skip 1$/ });
  expect(skipChip).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(skipChip);
  await waitFor(() => expect(within(rail).queryByText("A Quiet Word")).toBeNull());
  expect(skipChip).toHaveAttribute("aria-pressed", "false");
  expect(within(rail).getByText("1 hidden")).toBeInTheDocument();
  expect(within(rail).getByText("The Ledger")).toBeInTheDocument();   // other marks untouched

  fireEvent.click(skipChip);                                          // and back
  await waitFor(() => expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument());
});

test("world scope has no mark chips, since a world has no play history", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(
    RAIL.map(({ mark, ...g }) => g));   // a world list carries no marks
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");
  expect(within(rail).queryByRole("button", { name: /^played/ })).toBeNull();
  expect(within(rail).queryByRole("button", { name: /^skip/ })).toBeNull();
  // ...but search is offered in both scopes
  expect(within(rail).getByLabelText("Search greetings")).toBeInTheDocument();
});

test("search matches the greeting name, its source character, and present characters", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container } = render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");

  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "mara" } });
  await waitFor(() => expect(within(rail).queryByText("Saltmarch Dawn")).toBeNull());
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();   // source character
  expect(within(rail).getByText("The Ledger")).toBeInTheDocument();     // present character
  expect(within(rail).queryByText("Vow of Silence")).toBeNull();

  // substring, case-insensitive, on the name itself
  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "SALT" } });
  await waitFor(() => expect(within(rail).getByText("Saltmarch Dawn")).toBeInTheDocument());
  expect(within(rail).queryByText("The Ledger")).toBeNull();

  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "nothing here" } });
  await waitFor(() => expect(within(rail).getByText("No greetings match.")).toBeInTheDocument());
});

// Codex review, finding 1. This component is reused across a scope change, so
// filters describing one list would silently omit rows from the next -- and a
// campaign -> world -> campaign trip hides the chips in the middle leg while
// the exclusion they represent is still in force.
test("search and mark filters reset when the scope changes", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container, rerender } = render(
    <Wrap scope={{ kind: "campaign", id: "a" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");

  fireEvent.click(within(rail).getByRole("button", { name: /^skip 1$/ }));
  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "ledger" } });
  await waitFor(() => expect(within(rail).queryByText("Saltmarch Dawn")).toBeNull());

  within(rail).getByLabelText("Search greetings").focus();
  rerender(<Wrap scope={{ kind: "campaign", id: "b" }} wid="w" />);
  // campaign b opens on its whole list, with nothing carried over
  await waitFor(() => expect(within(rail).getByText("Saltmarch Dawn")).toBeInTheDocument());
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
  expect(within(rail).getByLabelText("Search greetings")).toHaveValue("");
  expect(screen.getByLabelText("Search greetings")).toHaveFocus();
  expect(within(rail).getByRole("button", { name: /^skip 1$/ })).toHaveAttribute("aria-pressed", "true");
});

// Names come from hand-written markdown and imported cards, so an accented one
// can be stored decomposed while the reader types it composed. The two render
// identically; without normalizing they never match.
test("search matches a decomposed name typed in composed form", async () => {
  // Built from escapes rather than pasted: a literal would be normalized
  // somewhere in the toolchain and the fixture would quietly stop testing this.
  const decomposed = "Café Meeting";   // e + U+0301 combining acute
  const composed = "Café";              // the same glyph as one code point
  expect(decomposed).not.toContain(composed);  // the premise, asserted
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    greeting("cafe", decomposed, "seraphine", []),
  ]));
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await waitFor(() => expect(rail.querySelectorAll(".row")).toHaveLength(1));

  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: composed } });
  await waitFor(() => expect(rail.querySelectorAll(".row")).toHaveLength(1));   // still matched
});

// Codex review round 2. An empty list before the read lands is not "no
// matches" -- and a query on a character's name cannot be judged at all until
// the character list is in, since `charName` falls back to the raw id.
test("the status line says nothing until both lists have loaded", async () => {
  let releaseGreetings: (v: Greeting[]) => void = () => {};
  let releaseChars: (v: CharacterSummary[]) => void = () => {};
  vi.mocked(api.listGreetings).mockReturnValue(new Promise((r) => { releaseGreetings = r; }));
  vi.mocked(api.listCharacters).mockReturnValue(new Promise((r) => { releaseChars = r; }));
  const { container } = render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  const status = within(rail).getByRole("status");

  expect(status).toHaveTextContent("");                    // nothing has been read yet
  await act(async () => { releaseGreetings(RAIL); });
  expect(status).toHaveTextContent("");                    // greetings in, names still out
  await act(async () => { releaseChars(CAST); });
  await waitFor(() => expect(within(rail).getByText("Saltmarch Dawn")).toBeInTheDocument());
  expect(status).toHaveTextContent("");                    // everything shown: still nothing to say

  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "zzz" } });
  await waitFor(() => expect(status).toHaveTextContent("No greetings match."));
});

// Filtering happens while focus is still in the search box, so the result
// count has to live in a region that exists before the text changes.
test("the result count is a live region", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  const { container } = render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");

  const status = within(rail).getByRole("status");
  expect(status).toHaveAttribute("aria-live", "polite");
  expect(status).toHaveTextContent("");                    // present before it has anything to say
  fireEvent.click(within(rail).getByRole("button", { name: /^skip 1$/ }));
  await waitFor(() => expect(status).toHaveTextContent("1 hidden"));
});

// The body of the open greeting is on screen either way; dropping its row would
// leave that content with no visible source in the list that supposedly holds it.
test("the open greeting stays listed even when the filters would hide it", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: greeting("word", "A Quiet Word", "mara", []),
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  vi.mocked(api.updateGreeting).mockResolvedValue({ ok: true });
  const { container } = render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" selected="word" />);
  const rail = await waitFor(() => railOf(container));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());

  fireEvent.click(within(rail).getByRole("button", { name: /^skip 1$/ }));   // would hide it
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
  // and a search it cannot match still leaves it there
  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "zzz" } });
  await waitFor(() => expect(within(rail).queryByText("Saltmarch Dawn")).toBeNull());
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
});

test("clicking a greeting shows a read-only rendered view; Edit reveals the form", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "Hello **world**", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  // read-only: markdown rendered, no editable textarea
  expect(screen.getByText("world")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();
  expect(screen.getByText("Present characters")).toBeInTheDocument();
  // Edit switches into the form
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("greeting body demotes scene-label headings and keeps single newlines", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    body: "#Rooftop Setting#\n\nFirst line\nSecond line",
    edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await screen.findByText("Rooftop Setting");
  expect(screen.queryByRole("heading", { name: /rooftop setting/i })).toBeNull();
  expect(container.querySelector(".detail-rendered br")).not.toBeNull();
});


test("creating a greeting posts the draft then sets edges", async () => {
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Open" } });
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "default" } });
  fireEvent.change(screen.getByLabelText(/predecessor join/i), { target: { value: "any" } });
  fireEvent.click(screen.getByRole("button", { name: "VIP" }));
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));
  await waitFor(() =>
    expect(api.createGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, expect.objectContaining({
      name: "Open", character: "seraphine", version: "default",
      predecessor_join: "any", requires_tags: ["vip"],
    })),
  );
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open", { leads_to: [], excludes: [] }));
});

test("creating a narrator-only greeting needs no character or version", async () => {
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  // A shared greeting needs no primary character, but can still name its full cast.
  expect(screen.queryByLabelText("Version")).toBeNull();
  expect(screen.getByText("Present characters")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Cold open" } });
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));
  await waitFor(() =>
    expect(api.createGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, expect.objectContaining({
      name: "Cold open", character: "", version: "",
    })),
  );
});

test("version options follow the selected character", async () => {
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  // the version select now offers 'default'
  const versionSelect = screen.getByLabelText<HTMLSelectElement>("Version");
  expect([...versionSelect.options].map((o) => o.value)).toContain("default");
});

test("import-from-character posts the selected character + version", async () => {
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "default" } });
  fireEvent.click(screen.getByRole("button", { name: /import greetings from this/i }));
  await waitFor(() =>
    expect(api.importGreetings).toHaveBeenCalledWith("w", { character: "seraphine", version: "default" }),
  );
});

test("clicking a present character opens that character at the right version", async () => {
  const onOpenCharacter = vi.fn();
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "v2", name: "Seraphine (alt)" }] },
    { id: "rowan", name: "Rowan", default_version: "main", versions: [{ id: "main", name: "Rowan" }] },
  ]);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "v2", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "v2", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  vi.mocked(api.updateGreeting).mockResolvedValue({ ok: true });
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" onOpenCharacter={onOpenCharacter} selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const present = within(container.querySelector(".detail-sidebar") as HTMLElement)
    .getByText("Present characters").closest(".side-section") as HTMLElement;
  // every chip names the character AND the version it opens at: the primary at
  // the greeting's version, a co-present character at its default
  fireEvent.click(within(present).getByRole("button", { name: "Seraphine:Seraphine (alt)" }));
  expect(onOpenCharacter).toHaveBeenCalledWith("seraphine", "v2");        // primary -> greeting version
  fireEvent.click(within(present).getByRole("button", { name: "Rowan:Rowan" }));
  expect(onOpenCharacter).toHaveBeenCalledWith("rowan", "main");          // co-present -> its default
});

test("a present chip whose version cannot be resolved falls back to the bare name", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "gone", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "gone", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const present = within(container.querySelector(".detail-sidebar") as HTMLElement)
    .getByText("Present characters").closest(".side-section") as HTMLElement;
  // the primary's stored version is no longer offered; an unknown co-present
  // character has no name to resolve at all -- neither gets a ":" with nothing after it
  expect(within(present).getByRole("button", { name: "Seraphine" })).toBeInTheDocument();
  expect(within(present).getByRole("button", { name: "rowan" })).toBeInTheDocument();
});

test("the view sidebar shows the full dependency picture", async () => {
  vi.mocked(api.listTags).mockResolvedValue({ vip: "VIP" });
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    { id: "prologue", name: "Prologue", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "finale", name: "Finale", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "secret", name: "Secret", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: ["vip"], predecessor_join: "any" },
    body: "hi", edges: { leads_to: ["finale"], excludes: ["secret"] }, predecessors: ["prologue"],
  }));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const dep = within(side).getByText("Depends on").closest(".side-section") as HTMLElement;
  expect(within(dep).getByText("Prologue")).toBeInTheDocument();
  expect(within(dep).getByText("any unlocks it")).toBeInTheDocument();
  expect(within(within(side).getByText("Unlocks").closest(".side-section") as HTMLElement).getByText("Finale")).toBeInTheDocument();
  expect(within(within(side).getByText("Excludes").closest(".side-section") as HTMLElement).getByText("Secret")).toBeInTheDocument();
  expect(within(within(side).getByText("Requires tags").closest(".side-section") as HTMLElement).getByText("VIP")).toBeInTheDocument();
});

test("a Depends-on chip is a link to that greeting's own address, not a same-page select", async () => {
  // A `<button onClick={() => select(id)}>` here used to leave the address
  // bar naming the greeting the reader started from -- Back did nothing, a
  // reload showed the wrong record, and a copied link sent someone else
  // elsewhere. It has to be a real `Link` to the predecessor's own address.
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "any" },
    { id: "prologue", name: "Prologue", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "any" },
    body: "x", edges: { leads_to: [], excludes: [] }, predecessors: ["prologue"],
  }));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const dep = within(container.querySelector(".detail-sidebar") as HTMLElement)
    .getByText("Depends on").closest(".side-section") as HTMLElement;
  expect(within(dep).getByRole("link", { name: "Prologue" })).toHaveAttribute("href", "/section/prologue");
});

test("editing a greeting toggles present characters and saves them", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] },
  }));
  vi.mocked(api.updateGreeting).mockResolvedValue({ ok: true });
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  const present = screen.getByText("Present characters").closest(".field") as HTMLElement;
  fireEvent.click(within(present).getByRole("button", { name: "Rowan" }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ present: ["seraphine", "rowan"] })),
  );
});

test("editing a greeting can re-point its character and version (#17)", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, rev: "r1",
  }));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

  // the selects are live on an existing greeting now, not fixed after creation
  const charSelect = screen.getByLabelText("Character");
  expect(charSelect).not.toBeDisabled();
  fireEvent.change(charSelect, { target: { value: "rowan" } });
  // picking a character resets the version until one is chosen — Save holds off
  expect(screen.getByRole("button", { name: /save greeting/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "main" } });

  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      // the primary follows the re-point into the cast: the old character's
      // own chip becomes the new one's, so the speaker is present at its own opener
      expect.objectContaining({ character: "rowan", version: "main", present: ["rowan"] })),
  );
});

test("re-pointing adds the new primary to a cast that lacks the old one", async () => {
  // Codex review: the old primary may be unchecked (or the greeting narrator-
  // only) while co-present characters are listed -- the new speaker still has
  // to end up in its own opener's cast.
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "winifred", name: "Winifred", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["winifred"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["winifred"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, rev: "r1",
  }));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "rowan" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "main" } });
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ character: "rowan", present: ["winifred", "rowan"] })),
  );
});

test("editing a greeting sets leads_to edges", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    { id: "reckoning", name: "Reckoning", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  const leadsTo = screen.getByText("Leads to").closest(".field") as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Reckoning" }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.setEdges).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open", { leads_to: ["reckoning"], excludes: [] }),
  );
});

test("greeting image shows subject chips and opens the picker", async () => {
  mockOpenWithImage({ "embed-aaa111bbb222": ["seraphine"] });
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.getGreetingSubjects).toHaveBeenCalledWith("w", "open"));
  const extras = await waitFor(() => {
    const el = container.querySelector(".img-extras");
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
  expect(within(extras).getByText("Seraphine")).toBeInTheDocument();
  fireEvent.click(within(extras).getByRole("button", { name: /subjects/i }));
  expect(screen.getByText(/present in this greeting/i)).toBeInTheDocument();
});

test("saving the picker PUTs subjects and refreshes", async () => {
  mockOpenWithImage({});
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.getGreetingSubjects).toHaveBeenCalledWith("w", "open"));
  // click-and-verify atomically: a subjects re-render can rebuild the markdown
  // DOM, detaching a button grabbed earlier
  await waitFor(() => {
    const extras = container.querySelector(".img-extras") as HTMLElement;
    expect(extras).not.toBeNull();
    fireEvent.click(within(extras).getByRole("button", { name: /subjects/i }));
    expect(screen.getByRole("dialog", { name: /image subjects/i })).toBeInTheDocument();
  });
  fireEvent.click(await screen.findByRole("button", { name: "Seraphine" }));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith(
    "w", "open", "embed-aaa111bbb222", ["seraphine"]));
});

test("the route's record opens that greeting in view mode", async () => {
  mockOpenWithImage({});
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  expect(await screen.findByRole("button", { name: /^edit$/i })).toBeInTheDocument();
});

const UNTAGGED = [
  { gid: "open", greeting_name: "Open", name: "embed-one", url: "/api/worlds/w/greetings/open/images/embed-one" },
  { gid: "open", greeting_name: "Open", name: "embed-two", url: "/api/worlds/w/greetings/open/images/embed-two" },
];

test("rail button opens the tagging queue; save/no-subjects advance it", async () => {
  vi.mocked(api.listUntaggedImages).mockResolvedValue(UNTAGGED);
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /tag images \(2\)/i }));
  await screen.findByText(/tagging 1 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: "Seraphine" }));
  fireEvent.click(screen.getByRole("button", { name: /save & next/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith("w", "open", "embed-one", ["seraphine"]));
  await screen.findByText(/tagging 2 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: /no subjects/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith("w", "open", "embed-two", []));
  await screen.findByText(/all images tagged/i);
});

test("skip advances without a PUT and close leaves the queue", async () => {
  vi.mocked(api.listUntaggedImages).mockResolvedValue(UNTAGGED);
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /tag images \(2\)/i }));
  await screen.findByText(/tagging 1 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText(/tagging 2 \/ 2/i);
  expect(api.setImageSubjects).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
  expect(await screen.findByRole("link", { name: /new greeting/i })).toBeInTheDocument();
});


test("campaign scope: marks a greeting as won't-do from the sidebar", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: null },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  vi.mocked(api.markGreeting).mockResolvedValue({ ok: true });
  render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" selected="g1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Won't do" }));
  await waitFor(() => expect(api.markGreeting).toHaveBeenCalledWith("run", "g1", "skipped"));
});

test("campaign scope: played greetings show a disabled status control", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" selected="g1" />);
  expect(await screen.findByRole("button", { name: "Mark complete" })).toBeDisabled();
  expect(screen.getByText(/started this greeting in a scene/i)).toBeInTheDocument();
});

test("campaign scope: a played greeting's Clear is enabled and clears an orphaned mark", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  vi.mocked(api.markGreeting).mockResolvedValue({ ok: true });
  render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" selected="g1" />);
  const clearBtn = await screen.findByRole("button", { name: /^clear$/i });
  expect(clearBtn).not.toBeDisabled();          // #315: the only in-app way back from a burned greeting
  fireEvent.click(clearBtn);
  await waitFor(() => expect(api.markGreeting).toHaveBeenCalledWith("run", "g1", "none"));
});

test("campaign scope: clearing a played greeting that IS still stamped surfaces the backend's 409", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  vi.mocked(api.markGreeting).mockRejectedValue(
    { detail: "greeting was played in a scene; its mark cannot be changed" });
  render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" selected="g1" />);
  fireEvent.click(await screen.findByRole("button", { name: /^clear$/i }));
  expect(await screen.findByText(/mark cannot be changed/i)).toBeInTheDocument();
});

test("campaign scope: hides the tagging queue and never fetches untagged images", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue([]);
  render(<Wrap scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByRole("link", { name: "+ New greeting" });
  expect(api.listUntaggedImages).not.toHaveBeenCalled();
});

test("view shows the Offscreen chip for a pcless greeting", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "cabal", name: "Cabal", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all", pcless: true },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "cabal", name: "Cabal", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all", pcless: true },
    body: "The cult meets.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="cabal" />);
  await screen.findByText("NPC-only opener");
});

test("the form's Offscreen toggle is sent on save", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /offscreen \(no pc\)/i }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "open", expect.objectContaining({ pcless: true })));
});

// ---- external edits: the save precondition (#35) ----

async function openGreetingForEdit() {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: [], requires_tags: [], predecessor_join: "all" },
  ]));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
}

test("a greeting save echoes back the rev it was read at", async () => {
  await openGreetingForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ rev: "r1" })),
  );
});

test("a stale greeting save is refused, and the plot map is left alone", async () => {
  vi.mocked(api.updateGreeting).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openGreetingForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/changed on disk while you had it open/i)).toBeInTheDocument();
  // Edges are written only after the body lands, so a refusal touches nothing.
  expect(api.setEdges).not.toHaveBeenCalled();
});

test("overwriting a greeting retries against the rev the refusal reported", async () => {
  vi.mocked(api.updateGreeting).mockRejectedValueOnce(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openGreetingForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /overwrite with mine/i }));

  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenLastCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ rev: "r2" })),
  );
});

// --- location (#218) -------------------------------------------------------

/** A greeting read that carries `location`, plus the row that lists it. */
function withLocation(location: string) {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: ["seraphine"], requires_tags: [], predecessor_join: "all", location },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default",
            present: ["seraphine"], requires_tags: [], predecessor_join: "all", location },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
}

test("the view sidebar shows the location as a chip that navigates to it", async () => {
  withLocation("counting-house");
  const onOpenLocation = vi.fn();
  const { container } = render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" onOpenLocation={onOpenLocation} selected="open" />);

  // the read-only view has arrived once its Edit button has
  await screen.findByRole("button", { name: /^edit$/i });
  const aside = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(aside).getByText("Location")).toBeInTheDocument();
  fireEvent.click(within(aside).getByRole("button", { name: "The Counting House" }));
  expect(onOpenLocation).toHaveBeenCalledWith("counting-house");
});

test("a greeting with no location gets no Location section", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await screen.findByRole("button", { name: /^edit$/i });
  const aside = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(aside).queryByText("Location")).toBeNull();
});

test("the form picks a location from the scope's own list and saves it", async () => {
  withLocation("");
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  // the picker is scoped, not world-only: a campaign's own locations must show
  expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations");
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

  const select = await screen.findByLabelText("Location");
  fireEvent.change(select, { target: { value: "the-quay" } });
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ location: "the-quay" })));
});

test("the form can clear a location back to none", async () => {
  withLocation("counting-house");
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

  const select = await screen.findByLabelText("Location");
  expect((select as HTMLSelectElement).value).toBe("counting-house");
  fireEvent.change(select, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open",
      expect.objectContaining({ location: "" })));
});

test("a location the picker cannot offer is shown rather than silently blanked", async () => {
  // Deleted since the greeting was written, or a list that failed to load. A
  // controlled <select> would otherwise render blank while the field still
  // holds the id, and the next save would carry it through unseen.
  withLocation("the-drowned-library");
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

  const select = await screen.findByLabelText<HTMLSelectElement>("Location");
  expect(select.value).toBe("the-drowned-library");
  expect(within(select).getByText(/the-drowned-library \(missing\)/)).toBeInTheDocument();
});

test("a deleted location is labelled, not offered as a link to nowhere", async () => {
  // The chip navigates to the location record; for an id the campaign no longer
  // has, that is a section switch onto a 404 with no explanation. It reads the
  // same way the edit-mode picker does instead (#412 review).
  withLocation("the-drowned-library");
  const onOpenLocation = vi.fn();
  const { container } = render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" onOpenLocation={onOpenLocation} selected="open" />);
  await screen.findByRole("button", { name: /^edit$/i });

  const aside = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(aside).getByText(/the-drowned-library \(missing\)/)).toBeInTheDocument();
  expect(within(aside).queryByRole("button", { name: /the-drowned-library/ })).toBeNull();
  expect(onOpenLocation).not.toHaveBeenCalled();
});

test("a failed locations read shows the stored id without calling it missing", async () => {
  // An empty list means three different things -- not read yet, read and
  // failed, read and there are none -- and only the last is evidence about
  // this greeting's location. Saying "missing" on the others invents a fact.
  vi.mocked(api.listEntities).mockRejectedValue(new Error("offline"));
  withLocation("counting-house");
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

  const select = await screen.findByLabelText<HTMLSelectElement>("Location");
  expect(select.value).toBe("counting-house");
  expect(within(select).getByText("counting-house")).toBeInTheDocument();
  expect(within(select).queryByText(/missing/)).toBeNull();
});

test("a demoted world greeting is cleared, not left as a pre-filled new draft", async () => {
  // clearing only the id leaves `mode` on "view" with the deleted greeting still
  // in `form`, so the editor falls through to the NEW-greeting form holding it
  // -- and Save then recreates the world greeting the demote just removed
  vi.mocked(api.listGreetings).mockResolvedValue(RAIL);
  vi.mocked(api.listCharacters).mockResolvedValue(CAST);
  vi.mocked(api.libraryDependents).mockResolvedValue([]);
  vi.mocked(api.demoteFromLibrary).mockResolvedValue({ copied_down: [], dependents: [] });
  const { container } = render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="dawn" />);
  await screen.findByRole("button", { name: "Remove from library…" });

  fireEvent.click(screen.getByRole("button", { name: "Remove from library…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove and copy down" }));

  await waitFor(() => expect(api.demoteFromLibrary).toHaveBeenCalled());
  // the blank create form, with nothing of the deleted greeting left in it --
  // pressing Save here must not be able to put the world greeting back
  await screen.findByRole("button", { name: "Create greeting" });
  expect(container.querySelector("textarea")).toHaveValue("");
  expect(screen.getByLabelText("Name")).toHaveValue("");
});

test("a save whose chips were never touched does not resend the edges", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: ["vow"], excludes: [] }, predecessors: [], rev: "r1",
  }));
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="dawn" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Saltmarch Dusk" } });
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));

  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalled());
  // The write replaces the greeting's WHOLE pair of arrays, so a body edit
  // that resent an untouched chip list would revert whatever the plot map drew
  // after this record was loaded (#9).
  expect(api.setEdges).not.toHaveBeenCalled();

  // ...and a touched one still writes
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  // the "Leads to" chip, not the rail row of the same name
  const leadsTo = screen.getByText("Leads to").parentElement as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Vow of Silence" }));
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "dawn", { leads_to: [], excludes: [] }));
});
