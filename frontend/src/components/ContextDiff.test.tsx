import { render, screen } from "@testing-library/react";
import { ContextDiff } from "./ContextDiff";
import { type PromptDiff, type PromptDiffSection } from "../api/client";

function side(over: Partial<PromptDiff["base"]> = {}): PromptDiff["base"] {
  return { id: "000001", task: "chat", ts: "2026-08-06T11:00:00Z", model: "m",
           total_tokens: 100, dropped_tokens: 0, budget_tokens: 4000, ...over };
}

function facts(over: Partial<NonNullable<PromptDiffSection["base"]>> = {}) {
  return { label: "World info", tokens: 10, dropped: false, trimmed: 0,
           pinned: false, ...over };
}

function section(over: Partial<PromptDiffSection> = {}): PromptDiffSection {
  return { id: "world", label: "World info", status: "unchanged",
           base: facts(), head: facts(), diff: [], ...over };
}

function diff(over: Partial<PromptDiff> = {}): PromptDiff {
  return { base: side(), head: side({ id: "live", task: "live", ts: "" }),
           sections: [], ...over };
}

test("an identical pair says so rather than showing an empty list", () => {
  render(<ContextDiff diff={diff({ sections: [section()] })} />);
  screen.getByText(/Nothing changed/);
  expect(screen.queryByText("World info")).toBeNull();
  // ...and not ALSO a count of what was left out: after "every section is
  // identical", a line saying some were not shown reads as a hedge on it.
  expect(screen.queryByText(/not shown/)).toBeNull();
});

test("unchanged sections are accounted for beside a list of changed ones", () => {
  render(<ContextDiff diff={diff({
    sections: [section(), section({ id: "lore", label: "Lore", status: "changed",
                                    diff: [{ op: "insert", text: "new" }] })],
  })} />);
  screen.getByText("Lore");
  screen.getByText(/1 unchanged section not shown/);
});

test("a changed section shows the lines that moved", () => {
  render(<ContextDiff diff={diff({
    sections: [section({
      status: "changed",
      diff: [{ op: "equal", text: "a keep by the sea" },
             { op: "delete", text: "the gate is shut" },
             { op: "insert", text: "the gate is open" }],
    })],
  })} />);

  screen.getByText("World info");
  screen.getByText("the gate is shut");
  screen.getByText("the gate is open");
  expect(screen.getByText("the gate is shut").className).toContain("diff-delete");
  expect(screen.getByText("the gate is open").className).toContain("diff-insert");
});

test("an elided run reads as a count, not as content", () => {
  // The one row whose `text` is empty: rendering it as a blank line would put a
  // line in the prompt that was never there.
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         diff: [{ op: "skip", text: "", count: 412 },
                                { op: "insert", text: "and then" }] })],
  })} />);
  screen.getByText(/412 unchanged lines/);
});

test("a section the packer dropped is called out even with no lines to show", () => {
  // The case the whole feature exists for: identical words, and the model never
  // saw them.
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         base: facts({ dropped: false }),
                         head: facts({ dropped: true }), diff: [] })],
  })} />);
  screen.getByText(/the model did not see this/);
});

test("an added section carries its whole text", () => {
  // The reader is looking at one panel, so a section only the other side has is
  // nowhere else on screen.
  render(<ContextDiff diff={diff({
    sections: [section({ id: "pact", label: "Harbor Pact", status: "added", base: null,
                         diff: [{ op: "insert", text: "The pact was signed at dusk." }] })],
  })} />);
  screen.getByText("added");
  screen.getByText("The pact was signed at dusk.");
});

test("a section renamed since the captured turn names its old label", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed", label: "Background",
                         base: facts({ label: "World lore" }),
                         head: facts({ label: "Background" }) })],
  })} />);
  screen.getByText(/Renamed from/);
  screen.getByText(/World lore/);
});

test("the two totals are shown with the delta between them", () => {
  render(<ContextDiff diff={diff({ base: side({ total_tokens: 1000 }),
                                   head: side({ id: "live", total_tokens: 1240 }) })} />);
  screen.getByText(/1,000 → 1,240 tok/);
  screen.getByText("+240");
});

test("a comparison across a budget change says so", () => {
  // Otherwise the reader is left wondering why a section survived on one side
  // and not the other.
  render(<ContextDiff diff={diff({ base: side({ budget_tokens: 4000 }),
                                   head: side({ id: "live", budget_tokens: 8000 }) })} />);
  screen.getByText(/Packed to different budgets/);
});

test("a comparison across a model change says so", () => {
  render(<ContextDiff diff={diff({ base: side({ model: "old-model" }),
                                   head: side({ id: "live", model: "new-model" }) })} />);
  screen.getByText(/Different models/);
});

test("the live end is named as a preview, not as a timestamp it does not have", () => {
  render(<ContextDiff diff={diff()} />);
  screen.getByText(/Live preview/);
});

test("comparing against the live preview says the live side is recomposed", () => {
  // `{{random}}` and `{{roll}}` resolve at render time, so a section built out
  // of them can read as changed when nothing in the campaign moved. The panel
  // is only worth trusting if it says so.
  render(<ContextDiff diff={diff()} />);
  screen.getByText(/composed fresh/);
});

test("two captured turns are not hedged, because both were recorded", () => {
  render(<ContextDiff diff={diff({ head: side({ id: "000002", ts: "2026-08-06T12:00:00Z" }) })} />);
  expect(screen.queryByText(/composed fresh/)).toBeNull();
});
