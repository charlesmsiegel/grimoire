import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { GreetingEditor } from "./GreetingEditor";

vi.mock("../api/client", () => ({
  // The editor branches on `instanceof ApiError` to tell a stale-record 409
  // from any other failure; declared in here because `vi.mock` is hoisted
  // above every top-level statement in the file.
  ApiError: class extends Error {
    constructor(public status: number, public detail: string, public kind?: string,
                public body?: Record<string, unknown>) {
      super(detail);
    }
  },
  api: {
    // The campaign-scope sidebar's LibraryPanel (#52). "already library
    // content, unedited" renders no button, so this suite is unchanged;
    // LibraryPanel.test.tsx owns the panel's own behaviour.
    libraryStatus: vi.fn().mockResolvedValue(
      { in_library: true, diverged: false, can_promote: false, can_push: false }),
    promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
    libraryDependents: vi.fn().mockResolvedValue([]), demoteFromLibrary: vi.fn(),
    listGreetings: vi.fn(), listCharacters: vi.fn(), listTags: vi.fn(), readGreeting: vi.fn(),
    createGreeting: vi.fn(), updateGreeting: vi.fn(), deleteGreeting: vi.fn(),
    setEdges: vi.fn(), importGreetings: vi.fn(), listEntities: vi.fn(),
    getGreetingSubjects: vi.fn(), setImageSubjects: vi.fn(), listUntaggedImages: vi.fn(), markGreeting: vi.fn(),
  },
}));
import { ApiError, api } from "../api/client";

const fail = (status: number, detail: string, kind?: string,
              body?: Record<string, unknown>) =>
  new (ApiError as any)(status, detail, kind, body);

beforeEach(() => {
  vi.clearAllMocks();
  (api.getGreetingSubjects as any).mockResolvedValue({});
  (api.setImageSubjects as any).mockResolvedValue({ ok: true });
  (api.listUntaggedImages as any).mockResolvedValue([]);
  (api.listGreetings as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  (api.listTags as any).mockResolvedValue({ vip: "VIP" });
  (api.listEntities as any).mockResolvedValue([
    { id: "counting-house", name: "The Counting House" },
    { id: "the-quay", name: "The Quay" },
  ]);
  (api.createGreeting as any).mockResolvedValue({ id: "open" });
  (api.setEdges as any).mockResolvedValue({ ok: true });
  (api.importGreetings as any).mockResolvedValue({ greetings: ["g1"] });
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  (api.updateGreeting as any).mockResolvedValue({ ok: true });
});

// --- rail search and mark filters ------------------------------------------

const CAST = [
  { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  { id: "winifred", name: "Winifred", default_version: "default", versions: [{ id: "default", name: "default" }] },
  { id: "mara", name: "Mara", default_version: "default", versions: [{ id: "default", name: "default" }] },
];
/** name, source character, present characters, mark */
function greeting(id: string, name: string, character: string, present: string[], mark?: string) {
  return { id, name, character, version: "default", present, requires_tags: [],
           predecessor_join: "all" as const, ...(mark ? { mark } : {}) };
}
const RAIL = [
  greeting("dawn", "Saltmarch Dawn", "seraphine", []),
  greeting("ledger", "The Ledger", "winifred", ["mara"], "played"),
  greeting("word", "A Quiet Word", "mara", [], "skipped"),
  greeting("vow", "Vow of Silence", "winifred", [], "completed"),
];
const railOf = (c: HTMLElement) => c.querySelector(".editor-list") as HTMLElement;

test("mark chips hide their group, and are absent in world scope", async () => {
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
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
  (api.listGreetings as any).mockResolvedValue(
    RAIL.map(({ mark, ...g }) => g));   // a world list carries no marks
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");
  expect(within(rail).queryByRole("button", { name: /^played/ })).toBeNull();
  expect(within(rail).queryByRole("button", { name: /^skip/ })).toBeNull();
  // ...but search is offered in both scopes
  expect(within(rail).getByLabelText("Search greetings")).toBeInTheDocument();
});

test("search matches the greeting name, its source character, and present characters", async () => {
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
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
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container, rerender } = render(
    <GreetingEditor scope={{ kind: "campaign", id: "a" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await within(rail).findByText("Saltmarch Dawn");

  fireEvent.click(within(rail).getByRole("button", { name: /^skip 1$/ }));
  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "ledger" } });
  await waitFor(() => expect(within(rail).queryByText("Saltmarch Dawn")).toBeNull());

  rerender(<GreetingEditor scope={{ kind: "campaign", id: "b" }} wid="w" />);
  // campaign b opens on its whole list, with nothing carried over
  await waitFor(() => expect(within(rail).getByText("Saltmarch Dawn")).toBeInTheDocument());
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
  expect(within(rail).getByLabelText("Search greetings")).toHaveValue("");
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
  (api.listGreetings as any).mockResolvedValue([
    greeting("cafe", decomposed, "seraphine", []),
  ]);
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  await waitFor(() => expect(rail.querySelectorAll(".row")).toHaveLength(1));

  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: composed } });
  await waitFor(() => expect(rail.querySelectorAll(".row")).toHaveLength(1));   // still matched
});

// Codex review round 2. An empty list before the read lands is not "no
// matches" -- and a query on a character's name cannot be judged at all until
// the character list is in, since `charName` falls back to the raw id.
test("the status line says nothing until both lists have loaded", async () => {
  let releaseGreetings: (v: any) => void = () => {};
  let releaseChars: (v: any) => void = () => {};
  (api.listGreetings as any).mockReturnValue(new Promise((r) => { releaseGreetings = r; }));
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { releaseChars = r; }));
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
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
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
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
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  (api.readGreeting as any).mockResolvedValue({
    meta: greeting("word", "A Quiet Word", "mara", []),
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  (api.updateGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("A Quiet Word"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalled());

  fireEvent.click(within(rail).getByRole("button", { name: /^skip 1$/ }));   // would hide it
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
  // and a search it cannot match still leaves it there
  fireEvent.change(within(rail).getByLabelText("Search greetings"), { target: { value: "zzz" } });
  await waitFor(() => expect(within(rail).queryByText("Saltmarch Dawn")).toBeNull());
  expect(within(rail).getByText("A Quiet Word")).toBeInTheDocument();
});

test("clicking a greeting shows a read-only rendered view; Edit reveals the form", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "Hello **world**", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    body: "#Rooftop Setting#\n\nFirst line\nSecond line",
    edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await screen.findByText("Rooftop Setting");
  expect(screen.queryByRole("heading", { name: /rooftop setting/i })).toBeNull();
  expect(container.querySelector(".detail-rendered br")).not.toBeNull();
});


test("creating a greeting posts the draft then sets edges", async () => {
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  // Version and Present characters stay hidden until a character is picked
  expect(screen.queryByLabelText("Version")).toBeNull();
  expect(screen.queryByText("Present characters")).toBeNull();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Cold open" } });
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));
  await waitFor(() =>
    expect(api.createGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, expect.objectContaining({
      name: "Cold open", character: "", version: "",
    })),
  );
});

test("version options follow the selected character", async () => {
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  // the version select now offers 'default'
  const versionSelect = screen.getByLabelText("Version") as HTMLSelectElement;
  expect([...versionSelect.options].map((o) => o.value)).toContain("default");
});

test("import-from-character posts the selected character + version", async () => {
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "v2", name: "Seraphine (alt)" }] },
    { id: "rowan", name: "Rowan", default_version: "main", versions: [{ id: "main", name: "Rowan" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "v2", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "v2", present: ["seraphine", "rowan"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  (api.updateGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" onOpenCharacter={onOpenCharacter} />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const present = within(container.querySelector(".detail-sidebar") as HTMLElement)
    .getByText("Present characters").closest(".side-section") as HTMLElement;
  // source character's chip carries its variant label; co-present stays plain
  fireEvent.click(within(present).getByRole("button", { name: "Seraphine (alt)" }));
  expect(onOpenCharacter).toHaveBeenCalledWith("seraphine", "v2");        // primary -> greeting version
  fireEvent.click(within(present).getByRole("button", { name: "Rowan" }));
  expect(onOpenCharacter).toHaveBeenCalledWith("rowan", "main");          // co-present -> its default
});

test("the view sidebar shows the full dependency picture", async () => {
  (api.listTags as any).mockResolvedValue({ vip: "VIP" });
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    { id: "prologue", name: "Prologue", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "finale", name: "Finale", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "secret", name: "Secret", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: ["vip"], predecessor_join: "any" },
    body: "hi", edges: { leads_to: ["finale"], excludes: ["secret"] }, predecessors: ["prologue"],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const dep = within(side).getByText("Depends on").closest(".side-section") as HTMLElement;
  expect(within(dep).getByText("Prologue")).toBeInTheDocument();
  expect(within(dep).getByText("any unlocks it")).toBeInTheDocument();
  expect(within(within(side).getByText("Unlocks").closest(".side-section") as HTMLElement).getByText("Finale")).toBeInTheDocument();
  expect(within(within(side).getByText("Excludes").closest(".side-section") as HTMLElement).getByText("Secret")).toBeInTheDocument();
  expect(within(within(side).getByText("Requires tags").closest(".side-section") as HTMLElement).getByText("VIP")).toBeInTheDocument();
});

test("clicking a Depends-on scene navigates to that greeting", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "any" },
    { id: "prologue", name: "Prologue", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockImplementation((_w: string, id: string) => Promise.resolve({
    meta: { id, name: id === "prologue" ? "Prologue" : "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "any" },
    body: "x", edges: { leads_to: [], excludes: [] }, predecessors: id === "open" ? ["prologue"] : [],
  }));
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  const dep = within(container.querySelector(".detail-sidebar") as HTMLElement)
    .getByText("Depends on").closest(".side-section") as HTMLElement;
  fireEvent.click(within(dep).getByRole("button", { name: "Prologue" }));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "prologue"));
});

test("editing a greeting toggles present characters and saves them", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] },
  });
  (api.updateGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, rev: "r1",
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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

test("editing a greeting sets leads_to edges", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    { id: "reckoning", name: "Reckoning", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  const leadsTo = screen.getByText("Leads to").closest(".field") as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Reckoning" }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.setEdges).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open", { leads_to: ["reckoning"], excludes: [] }),
  );
});

const IMG_BODY = "scene ![M](/api/worlds/w/greetings/open/images/embed-aaa111bbb222)";

function mockOpenWithImage(subjects: Record<string, string[]> = {}) {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: IMG_BODY, edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.getGreetingSubjects as any).mockResolvedValue(subjects);
}

test("greeting image shows subject chips and opens the picker", async () => {
  mockOpenWithImage({ "embed-aaa111bbb222": ["seraphine"] });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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

test("focus prop opens that greeting in view mode", async () => {
  mockOpenWithImage({});
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" focus="open" />);
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "open"));
  expect(await screen.findByRole("button", { name: /^edit$/i })).toBeInTheDocument();
});

const UNTAGGED = [
  { gid: "open", greeting_name: "Open", name: "embed-one", url: "/api/worlds/w/greetings/open/images/embed-one" },
  { gid: "open", greeting_name: "Open", name: "embed-two", url: "/api/worlds/w/greetings/open/images/embed-two" },
];

test("rail button opens the tagging queue; save/no-subjects advance it", async () => {
  (api.listUntaggedImages as any).mockResolvedValue(UNTAGGED);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
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
  (api.listUntaggedImages as any).mockResolvedValue(UNTAGGED);
  render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /tag images \(2\)/i }));
  await screen.findByText(/tagging 1 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText(/tagging 2 \/ 2/i);
  expect(api.setImageSubjects).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
  expect(await screen.findByRole("button", { name: /new greeting/i })).toBeInTheDocument();
});


test("campaign scope: marks a greeting as won't-do from the sidebar", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: null },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.markGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gala"));
  fireEvent.click(await screen.findByRole("button", { name: "Won't do" }));
  await waitFor(() => expect(api.markGreeting).toHaveBeenCalledWith("run", "g1", "skipped"));
});

test("campaign scope: played greetings show a disabled status control", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gala"));
  expect(await screen.findByRole("button", { name: "Mark complete" })).toBeDisabled();
  expect(screen.getByText(/started this greeting in a scene/i)).toBeInTheDocument();
});

test("campaign scope: a played greeting's Clear is enabled and clears an orphaned mark", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.markGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gala"));
  const clearBtn = await screen.findByRole("button", { name: /^clear$/i });
  expect(clearBtn).not.toBeDisabled();          // #315: the only in-app way back from a burned greeting
  fireEvent.click(clearBtn);
  await waitFor(() => expect(api.markGreeting).toHaveBeenCalledWith("run", "g1", "none"));
});

test("campaign scope: clearing a played greeting that IS still stamped surfaces the backend's 409", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "played" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.markGreeting as any).mockRejectedValue(
    { detail: "greeting was played in a scene; its mark cannot be changed" });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gala"));
  fireEvent.click(await screen.findByRole("button", { name: /^clear$/i }));
  expect(await screen.findByText(/mark cannot be changed/i)).toBeInTheDocument();
});

test("campaign scope: hides the tagging queue and never fetches untagged images", async () => {
  (api.listGreetings as any).mockResolvedValue([]);
  render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await screen.findByRole("button", { name: "+ New greeting" });
  expect(api.listUntaggedImages).not.toHaveBeenCalled();
});

test("view shows the Offscreen chip for a pcless greeting", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "cabal", name: "Cabal", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all", pcless: true },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "cabal", name: "Cabal", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all", pcless: true },
    body: "The cult meets.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cabal"));
  await screen.findByText("NPC-only opener");
});

test("the form's Offscreen toggle is sent on save", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /offscreen \(no pc\)/i }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "open", expect.objectContaining({ pcless: true })));
});

// ---- external edits: the save precondition (#35) ----

async function openGreetingForEdit() {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  (api.updateGreeting as any).mockRejectedValue(
    fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  await openGreetingForEdit();
  fireEvent.click(screen.getByRole("button", { name: /^save greeting$/i }));

  await screen.findByRole("alert");
  expect(screen.getByText(/changed on disk while you had it open/i)).toBeInTheDocument();
  // Edges are written only after the body lands, so a refusal touches nothing.
  expect(api.setEdges).not.toHaveBeenCalled();
});

test("overwriting a greeting retries against the rev the refusal reported", async () => {
  (api.updateGreeting as any).mockRejectedValueOnce(
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
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: ["seraphine"], requires_tags: [], predecessor_join: "all", location },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default",
            present: ["seraphine"], requires_tags: [], predecessor_join: "all", location },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
}

test("the view sidebar shows the location as a chip that navigates to it", async () => {
  withLocation("counting-house");
  const onOpenLocation = vi.fn();
  const { container } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" onOpenLocation={onOpenLocation} />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));

  // the read-only view has arrived once its Edit button has
  await screen.findByRole("button", { name: /^edit$/i });
  const aside = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(aside).getByText("Location")).toBeInTheDocument();
  fireEvent.click(within(aside).getByRole("button", { name: "The Counting House" }));
  expect(onOpenLocation).toHaveBeenCalledWith("counting-house");
});

test("a greeting with no location gets no Location section", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default",
      present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await screen.findByRole("button", { name: /^edit$/i });
  const aside = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(aside).queryByText("Location")).toBeNull();
});

test("the form picks a location from the scope's own list and saves it", async () => {
  withLocation("");
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  // the picker is scoped, not world-only: a campaign's own locations must show
  expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations");
  fireEvent.click(await within(rail).findByText("Open"));
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
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" onOpenLocation={onOpenLocation} />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  (api.listEntities as any).mockRejectedValue(new Error("offline"));
  withLocation("counting-house");
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
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
  (api.listGreetings as any).mockResolvedValue(RAIL);
  (api.listCharacters as any).mockResolvedValue(CAST);
  (api.libraryDependents as any).mockResolvedValue([]);
  (api.demoteFromLibrary as any).mockResolvedValue({ copied_down: [], dependents: [] });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
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
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: ["vow"], excludes: [] }, predecessors: [], rev: "r1",
  });
  const { container } = render(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
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

test("a refresh that lands after the reader starts editing is discarded", async () => {
  (api.listGreetings as any).mockResolvedValue([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]);
  let land: (v: any) => void = () => {};
  const detail = {
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  };
  (api.readGreeting as any).mockResolvedValue(detail);
  const { container, rerender } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" refreshKey={0} />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  await screen.findByRole("button", { name: /^edit$/i });

  // the other view writes; this refresh read is slow
  (api.readGreeting as any).mockImplementation(() => new Promise((res) => { land = res; }));
  rerender(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" refreshKey={1} />);

  // ...and while it is out, the reader starts a draft
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Half-written" } });
  land(detail);

  // The read was launched while this greeting was merely being READ. Landing
  // it now would replace the form and drop the mode back to view -- the draft
  // gone with no Save and no Cancel.
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Half-written"));
});

test("the editor holds still while the map's own write is on the wire", async () => {
  (api.listGreetings as any).mockResolvedValue([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  const { container } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w"
                    hold="The plot map is still writing these links." />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  // Both writers send whole arrays, so two overlapping saves can each discard
  // the other's edit in the one plotmap.json underneath.
  expect(screen.getByRole("button", { name: "Save greeting" })).toBeDisabled();
  expect(api.updateGreeting).not.toHaveBeenCalled();
});

test("an unsaved edge change is reported, so the map can hold still for it", async () => {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  const onEdgeDraft = vi.fn();
  const { container } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" onEdgeDraft={onEdgeDraft} />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  await waitFor(() => expect(onEdgeDraft).toHaveBeenLastCalledWith(false));

  const leadsTo = screen.getByText("Leads to").parentElement as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Vow of Silence" }));
  // The untouched-list rule only protects drafts nobody touched; once the
  // chips move, this save WILL replace the arrays, so the map must wait.
  await waitFor(() => expect(onEdgeDraft).toHaveBeenLastCalledWith(true));
});

test("Delete is held while the map is writing, as Save is", async () => {
  (api.listGreetings as any).mockResolvedValue([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  const { container } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w"
                    hold="The plot map is still writing these links." />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  // `delete_greeting` sweeps this id out of every other greeting's edges, so
  // it and an in-flight map write are two unlocked rewrites of one plotmap.
  expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  expect(api.deleteGreeting).not.toHaveBeenCalled();
});

test("busy stays true until the second of two overlapping saves settles", async () => {
  (api.listGreetings as any).mockResolvedValue([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  const gate: (() => void)[] = [];
  (api.updateGreeting as any).mockImplementation(() => new Promise<void>((res) => gate.push(res)));
  const onBusy = vi.fn();
  const { container } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" onBusy={onBusy} />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  const save = screen.getByRole("button", { name: "Save greeting" });
  fireEvent.click(save);
  await waitFor(() => expect(onBusy).toHaveBeenCalledWith(true));
  // the button is held while its own write is out, so a second cannot start
  await waitFor(() => expect(save).toBeDisabled());
  expect(api.updateGreeting).toHaveBeenCalledTimes(1);

  gate[0]();
  await waitFor(() => expect(onBusy).toHaveBeenLastCalledWith(false));
});

test("a graph write rebases a clean draft's chips without touching the form", async () => {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]);
  const before = {
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  };
  (api.readGreeting as any).mockResolvedValue(before);
  const { container, rerender } = render(
    <GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" refreshKey={0} />);
  const rail = await waitFor(() => railOf(container));
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Half-written" } });

  // the map draws an edge on this greeting while the draft sits open
  (api.readGreeting as any).mockResolvedValue({
    ...before, edges: { leads_to: ["vow"], excludes: [] },
  });
  rerender(<GreetingEditor scope={{ kind: "world", id: "w" }} wid="w" refreshKey={1} />);

  // The typing survives -- and the chips catch up, so touching one later does
  // not send a pre-map array back over the edge that was just drawn.
  await waitFor(() => {
    const leadsTo = screen.getByText("Leads to").parentElement as HTMLElement;
    expect(within(leadsTo).getByRole("button", { name: "Vow of Silence" }))
      .toHaveClass("chip", "on");
  });
  expect(screen.getByLabelText("Name")).toHaveValue("Half-written");
});
