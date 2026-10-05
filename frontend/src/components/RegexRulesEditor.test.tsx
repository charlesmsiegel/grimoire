import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RegexRulesEditor } from "./RegexRulesEditor";
import { api } from "../api/client";
import type { RegexBundle, RegexEntry, RegexLayer, RegexRule } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getRegex: vi.fn(), putRegex: vi.fn(), listConnections: vi.fn(),
    testRegex: vi.fn(), previewRegexImport: vi.fn(), importRegex: vi.fn(),
  },
}));

const rule = (id: string, over: Partial<RegexRule> = {}): RegexRule => ({
  id, name: id, enabled: true, pattern: "x", flags: "g", replacement: "", trim: [],
  targets: ["model"], applies: ["display", "prompt"], rewrite_stored: false,
  min_depth: null, max_depth: null, imported: null, ...over,
});

const STRIP = rule("r-strip", { name: "Strip asides", pattern: "\\(OOC:[^)]*\\)", replacement: "" });
const QUOTES = rule("r-quotes", { name: "Curly quotes", pattern: "\"", replacement: "”", flags: "gi" });
const GLOBAL_RULE = rule("r-glob", { name: "Fix dashes", pattern: "--", replacement: "—" });
const CONN_RULE = rule("r-conn", { name: "Drop think tags", pattern: "<think>.*?</think>" });

const inherited = (over: Partial<RegexEntry>[] = []): RegexEntry[] => [
  { level: "connection", rule: CONN_RULE, off: false, source: "conn-a" },
  { level: "global", rule: GLOBAL_RULE, off: false, source: "" },
].map((e, i) => ({ ...e, ...(over[i] ?? {}) }) as RegexEntry);

const bundle = (layer: RegexLayer, inh: RegexEntry[] = [],
                warnings: Record<string, string[]> = {}): RegexBundle =>
  ({ layer, inherited: inh, warnings });

const WORLD = { kind: "world" as const, wid: "realm" };
const GLOBAL = { kind: "global" as const };

/** The server answers a PUT with the bundle for what it stored, so this does too. */
function serve(layer: RegexLayer, inh: RegexEntry[] = [], warnings = {}) {
  vi.mocked(api.getRegex).mockResolvedValue(bundle(layer, inh, warnings));
  vi.mocked(api.putRegex).mockImplementation(async (_s, sent) => bundle(sent, inh, warnings));
}

beforeEach(() => {
  vi.mocked(api.getRegex).mockReset();
  vi.mocked(api.putRegex).mockReset();
  vi.mocked(api.listConnections).mockReset().mockResolvedValue(
    [{ id: "conn-a", name: "Local Llama" }] as never);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

async function open(name: string, scope: typeof WORLD | typeof GLOBAL = GLOBAL) {
  render(<RegexRulesEditor scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp("^" + name) }));
  return await screen.findByRole("heading", { name });
}

test("clicking a rule shows the read-only view with sidebar and no textarea", async () => {
  serve({ rules: [STRIP, QUOTES], off: [] });
  await open("Strip asides");
  expect(document.querySelector(".editor-body input[type=text], .editor-body textarea")).toBeNull();
  expect(screen.getByText("\\(OOC:[^)]*\\)")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  for (const heading of ["Flags", "Targets", "Applies", "Depth", "Stored text"]) {
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  }
});

test("Edit reveals the form", async () => {
  serve({ rules: [STRIP], off: [] });
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Pattern")).toHaveValue("\\(OOC:[^)]*\\)");
  expect(screen.getByLabelText("Name")).toHaveValue("Strip asides");
  expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
});

test("Cancel returns to the read-only view", async () => {
  serve({ rules: [STRIP], off: [] });
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByRole("heading", { name: "Strip asides" })).toBeInTheDocument();
  expect(api.putRegex).not.toHaveBeenCalled();
});

test("+ New rule opens the form directly", async () => {
  serve({ rules: [STRIP], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  expect(await screen.findByLabelText("Pattern")).toHaveValue("");
  expect(screen.getByRole("heading", { name: "New rule" })).toBeInTheDocument();
});

test("preset Display only sets both targets and only display", async () => {
  serve({ rules: [], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  fireEvent.click(await screen.findByRole("button", { name: "Display only" }));
  const targets = within(screen.getByRole("group", { name: "Targets" }));
  const applies = within(screen.getByRole("group", { name: "Applies" }));
  expect(targets.getByLabelText("Model posts")).toBeChecked();
  expect(targets.getByLabelText("Player posts")).toBeChecked();
  expect(applies.getByLabelText("What you read")).toBeChecked();
  expect(applies.getByLabelText("What the model is sent")).not.toBeChecked();
  // The groups stay editable afterwards.
  fireEvent.click(targets.getByLabelText("Player posts"));
  expect(targets.getByLabelText("Player posts")).not.toBeChecked();
});

test("saving a new rule PUTs the whole layer and selects it", async () => {
  serve({ rules: [STRIP], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Ellipses" } });
  fireEvent.change(screen.getByLabelText("Pattern"), { target: { value: "\\.{4,}" } });
  fireEvent.change(screen.getByLabelText("Replacement"), { target: { value: "…" } });
  fireEvent.change(screen.getByLabelText("Trim"), { target: { value: "a\n\nb" } });
  fireEvent.change(screen.getByLabelText("Max depth"), { target: { value: "3" } });
  fireEvent.click(screen.getByLabelText("Also rewrite stored text"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("heading", { name: "Ellipses" });
  const [, sent] = vi.mocked(api.putRegex).mock.calls[0];
  expect(sent.rules).toHaveLength(2);
  expect(sent.rules[0]).toEqual(STRIP);
  expect(sent.rules[1]).toMatchObject({
    name: "Ellipses", pattern: "\\.{4,}", replacement: "…", flags: "g", trim: ["a", "b"],
    targets: ["model"], applies: ["display", "prompt"], rewrite_stored: true,
    min_depth: null, max_depth: 3, enabled: true,
  });
  expect(sent.rules[1].id).toMatch(/^r-[0-9a-f]{8}$/);
});

test("the form warns that a pattern can hang the server", async () => {
  serve({ rules: [], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  const warning = await screen.findByText(/no timeout/);
  expect(warning).toHaveClass("field-hint");
  expect(warning).toHaveTextContent(/backtrack/);
  expect(warning).toHaveTextContent(/test pane/);
});

test("a new rule saved with Enabled off is sent disabled", async () => {
  // Spec section 2's way to keep a pattern that does not compile yet.
  serve({ rules: [], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  const enabled = await screen.findByLabelText("Enabled");
  expect(enabled).toBeChecked();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Later" } });
  fireEvent.change(screen.getByLabelText("Pattern"), { target: { value: "x" } });
  fireEvent.click(enabled);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0][1].rules[0]).toMatchObject({ name: "Later", enabled: false });
});

test("the form's Enabled box starts from the rule being edited", async () => {
  serve({ rules: [rule("r-off", { name: "Dormant", enabled: false })], off: [] });
  await open("Dormant");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Enabled")).not.toBeChecked();
});

test("toggling an inherited rule's Use here PUTs off", async () => {
  serve({ rules: [STRIP], off: [] }, inherited());
  render(<RegexRulesEditor scope={WORLD} />);
  const boxes = await screen.findAllByTitle("Use here");
  expect(boxes).toHaveLength(2);
  expect(boxes[1]).toBeChecked();
  fireEvent.click(boxes[1]);
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0]).toEqual([WORLD, { rules: [STRIP], off: ["r-glob"] }]);
});

test("switching an inherited rule back on takes it out of off", async () => {
  serve({ rules: [], off: ["r-glob"] }, inherited([{}, { off: true }]));
  render(<RegexRulesEditor scope={WORLD} />);
  const boxes = await screen.findAllByTitle("Use here");
  expect(boxes[1]).not.toBeChecked();
  fireEvent.click(boxes[1]);
  await waitFor(() => expect(api.putRegex).toHaveBeenCalled());
  expect(vi.mocked(api.putRegex).mock.calls[0][1]).toEqual({ rules: [], off: [] });
});

test("a rule the world switched off is shown off at a campaign, and cannot be ticked back", async () => {
  // `off` is this level's switch; `off_by: "world"` is one the campaign cannot
  // undo, and the rule never runs here -- so a ticked box would be a lie.
  serve({ rules: [], off: [] }, inherited([{ off_by: "world" }, {}]));
  render(<RegexRulesEditor scope={{ kind: "campaign", cid: "saltmarch" }} />);
  const boxes = await screen.findAllByRole("checkbox");
  expect(boxes[0]).not.toBeChecked();
  expect(boxes[0]).toBeDisabled();
  expect(boxes[0]).toHaveAttribute("title", "Switched off by the world");
  expect(boxes[1]).toBeChecked();
  expect(boxes[1]).toBeEnabled();
  const row = screen.getByRole("button", { name: /^Drop think tags/ });
  expect(row).toHaveClass("off");
  expect(row).toHaveTextContent("switched off by the world");
  // Its read-only view says so too.
  fireEvent.click(row);
  expect(await screen.findByText(/switched off by the world/i, { selector: ".detail-sidebar *" }))
    .toBeInTheDocument();
});

test("the Use here switch is absent at the global and connection levels", async () => {
  serve({ rules: [STRIP], off: [] });
  render(<RegexRulesEditor scope={{ kind: "connection", id: "conn-a" }} />);
  await screen.findByRole("button", { name: /^Strip asides/ });
  expect(screen.queryByTitle("Use here")).toBeNull();
});

test("inherited rows carry their level, a connection by name", async () => {
  serve({ rules: [], off: [] }, inherited());
  render(<RegexRulesEditor scope={WORLD} />);
  expect(await screen.findByText("connection: Local Llama")).toBeInTheDocument();
  expect(screen.getByText("global")).toBeInTheDocument();
});

test("an inherited rule opens read-only with no Edit", async () => {
  serve({ rules: [], off: [] }, inherited());
  await open("Fix dashes", WORLD);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.getByText(/Change it there/)).toBeInTheDocument();
});

test("↑ reorders and saves", async () => {
  serve({ rules: [STRIP, QUOTES], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "Move Curly quotes up" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0][1].rules.map((r) => r.id))
    .toEqual(["r-quotes", "r-strip"]);
  expect(screen.getByRole("button", { name: "Move Curly quotes up" })).toBeDisabled();
});

test("the enabled checkbox saves the layer with the rule switched off", async () => {
  serve({ rules: [STRIP, QUOTES], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByLabelText("Enable Strip asides"));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0][1].rules.map((r) => r.enabled)).toEqual([false, true]);
});

test("a 400 invalid_rule shows the message on the pattern field", async () => {
  serve({ rules: [STRIP], off: [] });
  const message = "pattern: does not compile: missing ), unterminated subpattern at position 0";
  vi.mocked(api.putRegex).mockRejectedValueOnce(
    Object.assign(new Error(message), {
      status: 400, detail: message, kind: "invalid_rule",
      body: { kind: "invalid_rule", index: 0, field: "pattern", detail: message },
    }));
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Pattern"), { target: { value: "(" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const note = await screen.findByText(message);
  expect(screen.getByLabelText("Pattern").closest(".field")).toContainElement(note);
  // Still in the form, nothing lost.
  expect(screen.getByLabelText("Pattern")).toHaveValue("(");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a refusal naming no form field is a banner", async () => {
  serve({ rules: [STRIP], off: [] });
  vi.mocked(api.putRegex).mockRejectedValueOnce(
    Object.assign(new Error("boom"), { status: 404, detail: "world not found" }));
  render(<RegexRulesEditor scope={WORLD} />);
  fireEvent.click(await screen.findByLabelText("Enable Strip asides"));
  expect(await screen.findByRole("alert")).toHaveTextContent("world not found");
});

test("a depth typed as prose is refused before it is sent", async () => {
  serve({ rules: [], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  fireEvent.change(await screen.findByLabelText("Min depth"), { target: { value: "-2" } });
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByLabelText("Min depth").closest(".field")).toHaveTextContent("whole number");
});

test("warnings and imported notes show in the detail view", async () => {
  const imported = rule("r-imp", {
    name: "From ST", imported: { from: "sillytavern", pattern: "/a/gi", notes: ["Flag u was dropped."] },
  });
  serve({ rules: [imported], off: [] }, [], { "r-imp": ["$5 refers to a group the pattern lacks"] });
  await open("From ST");
  expect(screen.getByText("$5 refers to a group the pattern lacks")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Imported notes" })).toBeInTheDocument();
  expect(screen.getByText("Flag u was dropped.")).toBeInTheDocument();
});

test("deleting a rule PUTs the layer without it", async () => {
  serve({ rules: [STRIP, QUOTES], off: [] });
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalled());
  expect(vi.mocked(api.putRegex).mock.calls[0][1].rules.map((r) => r.id)).toEqual(["r-quotes"]);
});

test("a read that fails offers to try again", async () => {
  vi.mocked(api.getRegex).mockRejectedValueOnce(new Error("down"));
  render(<RegexRulesEditor scope={GLOBAL} />);
  const again = await screen.findByRole("button", { name: "Try again" });
  vi.mocked(api.getRegex).mockResolvedValue(bundle({ rules: [STRIP], off: [] }));
  fireEvent.click(again);
  expect(await screen.findByRole("button", { name: /^Strip asides/ })).toBeInTheDocument();
});

test.each([
  ["global", GLOBAL],
  ["connection", { kind: "connection" as const, id: "conn-a" }],
])("a %s PUT carries no off even when the file it read had stale ids", async (_name, scope) => {
  serve({ rules: [STRIP, QUOTES], off: ["r-gone"] });
  render(<RegexRulesEditor scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: "Move Curly quotes up" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0][1].off).toEqual([]);
  fireEvent.click(await screen.findByLabelText("Enable Strip asides"));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.putRegex).mock.calls[1][1].off).toEqual([]);
});

test("a world PUT keeps the off ids it read", async () => {
  serve({ rules: [STRIP, QUOTES], off: ["r-glob"] }, inherited());
  render(<RegexRulesEditor scope={WORLD} />);
  fireEvent.click(await screen.findByRole("button", { name: "Move Curly quotes up" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.putRegex).mock.calls[0][1].off).toEqual(["r-glob"]);
});

test("a refusal naming a field the form does not draw is a banner", async () => {
  serve({ rules: [STRIP], off: [] });
  const message = "id: 'r-strip' is already used by an inherited rule";
  vi.mocked(api.putRegex).mockRejectedValueOnce(
    Object.assign(new Error(message), {
      status: 400, detail: message, kind: "invalid_rule",
      body: { kind: "invalid_rule", index: 0, field: "id", detail: message },
    }));
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Renamed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
});

test("a field note goes away once that field is edited", async () => {
  serve({ rules: [STRIP], off: [] });
  const message = "pattern: does not compile: bad";
  vi.mocked(api.putRegex).mockRejectedValueOnce(
    Object.assign(new Error(message), {
      status: 400, detail: message, kind: "invalid_rule",
      body: { kind: "invalid_rule", index: 0, field: "pattern", detail: message },
    }));
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Pattern"), { target: { value: "(" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText(message);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Other" } });
  expect(screen.getByText(message)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Pattern"), { target: { value: "(a)" } });
  expect(screen.queryByText(message)).toBeNull();
});

test("a write in flight at a scope change neither disables nor paints the new scope", async () => {
  vi.mocked(api.getRegex).mockImplementation(async (s) =>
    bundle(s.kind === "global"
      ? { rules: [STRIP, QUOTES], off: [] } : { rules: [GLOBAL_RULE], off: [] }));
  let release!: (b: RegexBundle) => void;
  vi.mocked(api.putRegex).mockImplementationOnce(
    () => new Promise<RegexBundle>((r) => { release = r; }));
  const { rerender } = render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "Move Curly quotes up" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  rerender(<RegexRulesEditor scope={WORLD} />);
  expect(await screen.findByRole("button", { name: /^Fix dashes/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+ New rule" })).toBeEnabled();
  expect(screen.getByLabelText("Enable Fix dashes")).toBeEnabled();
  release(bundle({ rules: [QUOTES, STRIP], off: [] }));
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByRole("button", { name: /^Curly quotes/ })).toBeNull();
  expect(screen.getByLabelText("Enable Fix dashes")).toBeEnabled();
});

test("the test pane sends the open form's rule as the draft", async () => {
  serve({ rules: [STRIP], off: [] });
  vi.mocked(api.testRegex).mockResolvedValue({ steps: [], result: "" });
  await open("Strip asides");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Pattern"), { target: { value: "Seraphine" } });
  fireEvent.change(screen.getByLabelText("Text to test"), { target: { value: "Seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByText("No rules to run at this level.");
  expect(vi.mocked(api.testRegex).mock.calls[0][0]).toMatchObject({
    scope: GLOBAL, draft: { id: "r-strip", pattern: "Seraphine" },
  });
});

test("the test pane offers the connections, except at a connection's own level", async () => {
  serve({ rules: [], off: [] });
  const { unmount } = render(<RegexRulesEditor scope={WORLD} />);
  expect(await screen.findByRole("option", { name: "Local Llama" })).toBeInTheDocument();
  unmount();
  render(<RegexRulesEditor scope={{ kind: "connection", id: "conn-a" }} />);
  await screen.findByText("Pick a rule to read it, or add one.");
  expect(screen.queryByLabelText("Connection")).toBeNull();
});

test("Import… opens the dialog, and a landed import reloads the rules", async () => {
  serve({ rules: [], off: [] });
  render(<RegexRulesEditor scope={GLOBAL} />);
  await screen.findByText("Pick a rule to read it, or add one.");
  fireEvent.click(screen.getByRole("button", { name: "Import…" }));
  const file = new File([JSON.stringify([{ scriptName: "Fix" }])], "r.json");
  vi.mocked(api.previewRegexImport).mockResolvedValue({ rows: [{
    index: 0, name: "Fix", verdict: "exact", notes: [], original: {},
    rule: { ...rule("x", { name: "Fix" }), id: undefined } as never,
  }] });
  vi.mocked(api.importRegex).mockResolvedValue({} as never);
  fireEvent.change(await screen.findByLabelText("SillyTavern regex file"), { target: { files: [file] } });
  const before = vi.mocked(api.getRegex).mock.calls.length;
  serve({ rules: [rule("r-new", { name: "Fix" })], off: [] });
  fireEvent.click(await screen.findByRole("button", { name: "Import selected" }));
  expect(await screen.findByRole("button", { name: /^Fix/ })).toBeInTheDocument();
  expect(vi.mocked(api.getRegex).mock.calls.length).toBeGreaterThan(before);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("an import that lands after the scope moved does not load the old level into the new one", async () => {
  vi.mocked(api.getRegex).mockImplementation(async (sc) =>
    bundle(sc.kind === "global" ? { rules: [STRIP], off: [] } : { rules: [GLOBAL_RULE], off: [] }));
  const { rerender } = render(<RegexRulesEditor scope={GLOBAL} />);
  await screen.findByRole("button", { name: /^Strip asides/ });
  fireEvent.click(screen.getByRole("button", { name: "Import…" }));
  const file = new File([JSON.stringify([{ scriptName: "Fix" }])], "r.json");
  vi.mocked(api.previewRegexImport).mockResolvedValue({ rows: [{
    index: 0, name: "Fix", verdict: "exact", notes: [], original: {},
    rule: { ...rule("x", { name: "Fix" }), id: undefined } as never,
  }] });
  let land!: () => void;
  vi.mocked(api.importRegex).mockImplementation(
    () => new Promise((r) => { land = () => r({} as never); }));
  fireEvent.change(await screen.findByLabelText("SillyTavern regex file"), { target: { files: [file] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import selected" }));
  await waitFor(() => expect(api.importRegex).toHaveBeenCalledTimes(1));

  rerender(<RegexRulesEditor scope={WORLD} />);
  expect(await screen.findByRole("button", { name: /^Fix dashes/ })).toBeInTheDocument();
  // The old dialog's onDone reloads the level it was opened on; that answer
  // belongs to the global level and must not paint the world's screen.
  land();
  await waitFor(() => expect(vi.mocked(api.getRegex).mock.calls
    .filter(([sc]) => sc.kind === "global")).toHaveLength(2));
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByRole("button", { name: /^Strip asides/ })).toBeNull();
  expect(screen.getByRole("button", { name: /^Fix dashes/ })).toBeInTheDocument();
});

test("Import… is held while a save of the level is in flight", async () => {
  // An import appends to the level as it stands on the server; a whole-layer
  // PUT landing after it would write the layer back without what it added.
  serve({ rules: [STRIP, QUOTES], off: [] });
  let release!: (b: RegexBundle) => void;
  vi.mocked(api.putRegex).mockImplementationOnce(
    () => new Promise<RegexBundle>((r) => { release = r; }));
  render(<RegexRulesEditor scope={GLOBAL} />);
  fireEvent.click(await screen.findByRole("button", { name: "Move Curly quotes up" }));
  await waitFor(() => expect(api.putRegex).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Import…" })).toBeDisabled();
  release(bundle({ rules: [QUOTES, STRIP], off: [] }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import…" })).toBeEnabled());
});

test("an imported rule with malformed notes still opens", async () => {
  const odd = rule("r-odd", {
    name: "Odd import", imported: { from: "sillytavern", pattern: "/a/" } as never,
  });
  serve({ rules: [odd], off: [] });
  await open("Odd import");
  expect(screen.getByRole("heading", { name: "Imported notes" })).toBeInTheDocument();
  expect(screen.getByText("/a/")).toBeInTheDocument();
});

test("a saved rule whose id the server re-minted is the one selected", async () => {
  // The id the form sent collides with an inherited rule's, so the server
  // stores the rule under a fresh one; the view must follow the stored rule.
  serve({ rules: [], off: [] }, inherited());
  vi.mocked(api.putRegex).mockImplementation(async (_s, sent) =>
    bundle({ ...sent, rules: sent.rules.map((r) => ({ ...r, id: "r-fresh" })) }, inherited()));
  render(<RegexRulesEditor scope={WORLD} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New rule" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Mine" } });
  fireEvent.change(screen.getByLabelText("Pattern"), { target: { value: "a" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("heading", { name: "Mine" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});
