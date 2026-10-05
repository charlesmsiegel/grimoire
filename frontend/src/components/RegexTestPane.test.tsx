import { fireEvent, render, screen } from "@testing-library/react";
import { RegexTestPane } from "./RegexTestPane";
import { api } from "../api/client";
import type { RegexRule } from "../api/client";

vi.mock("../api/client", () => ({ api: { testRegex: vi.fn() } }));

const GLOBAL = { kind: "global" as const };

const STEPS = [
  { rule_id: "r-a", level: "global" as const, name: "Fix dashes", applied: true, reason: null,
    matches: 2, text_after: "Mara — and Winifred — wait" },
  { rule_id: "r-b", level: "world" as const, name: "Strip asides", applied: false,
    reason: "targets player posts only", matches: 0, text_after: "Mara — and Winifred — wait" },
];

beforeEach(() => {
  vi.mocked(api.testRegex).mockReset().mockResolvedValue({ steps: STEPS, result: STEPS[1].text_after });
});

test("runs the trace and shows each step with its reason", async () => {
  render(<RegexTestPane scope={GLOBAL} />);
  fireEvent.change(screen.getByLabelText("Text to test"), { target: { value: "Mara -- and Winifred -- wait" } });
  fireEvent.change(screen.getByLabelText("Role"), { target: { value: "user" } });
  fireEvent.change(screen.getByLabelText("Phase"), { target: { value: "prompt" } });
  fireEvent.change(screen.getByLabelText("Depth"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Run" }));

  expect(await screen.findByText("Fix dashes")).toBeInTheDocument();
  expect(api.testRegex).toHaveBeenCalledWith({
    scope: GLOBAL, text: "Mara -- and Winifred -- wait", role: "user", phase: "prompt", depth: 3,
  });
  // The applied step shows its count and the text it left, the change marked.
  expect(screen.getByText("2 matches")).toBeInTheDocument();
  const marks = [...document.querySelectorAll("mark")].map((m) => m.textContent);
  expect(marks).toEqual(["— and Winifred —"]);
  // The skipped one is greyed and says why.
  const skipped = screen.getByText("Strip asides").closest("li")!;
  expect(skipped).toHaveClass("off");
  expect(skipped).toHaveTextContent("targets player posts only");
});

test("passes the draft rule", async () => {
  const draft: RegexRule = {
    id: "r-draft", name: "Mine", enabled: true, pattern: "Seraphine", flags: "g", replacement: "Mara",
    trim: [], targets: ["model"], applies: ["display"], rewrite_stored: false,
    min_depth: null, max_depth: null, imported: null,
  };
  render(<RegexTestPane scope={{ kind: "world", wid: "realm" }} draft={draft} />);
  fireEvent.change(screen.getByLabelText("Text to test"), { target: { value: "Seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByText("Fix dashes");
  expect(vi.mocked(api.testRegex).mock.calls[0][0]).toMatchObject({
    scope: { kind: "world", wid: "realm" }, draft, role: "model", phase: "display", depth: 0,
  });
});

test("starts from the initial text, role, depth and connection, and sends the connection", async () => {
  render(<RegexTestPane scope={GLOBAL} connections={[{ id: "conn-a", name: "Local Llama" }]}
                        initial={{ text: "Saltmarch", role: "user", depth: 2, connection: "conn-a" }} />);
  expect(screen.getByLabelText("Text to test")).toHaveValue("Saltmarch");
  expect(screen.getByLabelText("Role")).toHaveValue("user");
  expect(screen.getByLabelText("Depth")).toHaveValue(2);
  expect(screen.getByLabelText("Connection")).toHaveValue("conn-a");
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByText("Fix dashes");
  expect(api.testRegex).toHaveBeenCalledWith(expect.objectContaining({ connection: "conn-a", depth: 2 }));
});

test("offers no connection choice at connection scope", () => {
  render(<RegexTestPane scope={{ kind: "connection", id: "conn-a" }}
                        connections={[{ id: "conn-a", name: "Local Llama" }]} />);
  expect(screen.queryByLabelText("Connection")).toBeNull();
});

test("a depth that is not a whole number disables Run", () => {
  render(<RegexTestPane scope={GLOBAL} />);
  fireEvent.change(screen.getByLabelText("Depth"), { target: { value: "-1" } });
  expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
});

test("a refusal is shown and leaves no stale trace", async () => {
  vi.mocked(api.testRegex).mockRejectedValueOnce(new Error("pattern: bad escape"));
  render(<RegexTestPane scope={GLOBAL} />);
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("bad escape");
  expect(screen.queryByText("Fix dashes")).toBeNull();
});
