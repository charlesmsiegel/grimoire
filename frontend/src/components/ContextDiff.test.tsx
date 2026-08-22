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
  return { id: "world", label: "World info", status: "unchanged", moved: false,
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

test("a change to a section neither side sent says so", () => {
  // The flags are EQUAL, so the difference-only rule would stay silent — and
  // the panel would show a textual change with no sign that it cannot be the
  // cause of anything, sending debugging at a non-cause.
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         base: facts({ dropped: true }),
                         head: facts({ dropped: true }),
                         diff: [{ op: "insert", text: "a line neither turn sent" }] })],
  })} />);
  screen.getByText(/on both sides/);
  screen.getByText(/saw neither version/);
});

test("a section dropped on neither side is not annotated", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed", diff: [{ op: "insert", text: "new" }] })],
  })} />);
  expect(screen.queryByText(/budget packer/)).toBeNull();
});

test("a comparison the live side has moved under says so rather than blanking", () => {
  render(<ContextDiff diff={diff()} recomputing />);
  screen.getByText(/A turn has landed since this was computed/);
});

test("a settled comparison carries no such notice", () => {
  render(<ContextDiff diff={diff()} />);
  expect(screen.queryByText(/recomputing/)).toBeNull();
});


test("a section that was only dragged elsewhere is still reported", () => {
  // Its CONTENT is unchanged, so filtering on status alone made a layout
  // reorder invisible — and order is not decoration: the packer drops from the
  // bottom of a tier and the model reads the prompt in sequence.
  render(<ContextDiff diff={diff({
    sections: [section({ status: "unchanged", moved: true })],
  })} />);
  screen.getByText("World info");
  screen.getByText("moved");
  screen.getByText(/Moved to a different position/);
  expect(screen.queryByText(/Nothing changed/)).toBeNull();
});

test("a section that moved AND changed carries both marks", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed", moved: true,
                         diff: [{ op: "insert", text: "new line" }] })],
  })} />);
  screen.getByText("changed");
  screen.getByText("moved");
  screen.getByText("new line");
});

test("a settled section carries no status chip at all", () => {
  // "unchanged" was rendered as a chip beside the rows that had really moved,
  // which made the one word doing the work easy to miss.
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed", diff: [{ op: "insert", text: "x" }] }),
               section({ id: "b", label: "Other" })],
  })} />);
  expect(screen.queryByText("unchanged")).toBeNull();
});

test("a snapshot with no task or timestamp is named by its id, not left blank", () => {
  // Both fields are only required to be strings. A heading reading " · " over
  // a real comparison is worse than one naming the turn by its number.
  render(<ContextDiff diff={diff({ base: side({ id: "000007", task: "", ts: "" }) })} />);
  screen.getByText(/000007/);
});

test("a pin taking effect is explained, not just counted", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         base: facts({ pinned: false }), head: facts({ pinned: true }) })],
  })} />);
  screen.getByText(/Pinned, so the packer left it alone/);
});

test("a pin coming off is explained too", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         base: facts({ pinned: true }), head: facts({ pinned: false }) })],
  })} />);
  screen.getByText(/No longer pinned/);
});

test("history trimmed off the front reports both counts", () => {
  // The one flag whose value is a number rather than a state: "4 messages were
  // cut" is the answer, and "trimmed changed" is not.
  render(<ContextDiff diff={diff({
    sections: [section({ id: "history", label: "Conversation history", status: "changed",
                         base: facts({ trimmed: 0 }), head: facts({ trimmed: 4 }) })],
  })} />);
  screen.getByText(/History trimmed: 0 → 4 messages cut from the front/);
});

test("a section the packer kept this time says the packer had dropped it", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed",
                         base: facts({ dropped: true }), head: facts({ dropped: false }) })],
  })} />);
  screen.getByText(/Kept this time; the budget packer had dropped it/);
});

test("a difference only the side totals record is reported, not denied", () => {
  // History cut from the FRONT leaves no section behind — the `history` row
  // carries how many messages went, not what they weighed — so two turns can
  // have identical sections and demonstrably different packer work.
  render(<ContextDiff diff={diff({
    base: side({ dropped_tokens: 1200 }),
    head: side({ id: "live", dropped_tokens: 3400 }),
    sections: [section()],
  })} />);
  screen.getByText(/1,200 → 3,400/);
  screen.getByText("+2,200");
  screen.getByText(/No section differs, but the two turns dropped different amounts/);
  expect(screen.queryByText(/Nothing changed/)).toBeNull();
});

test("identical sections and identical cuts really do say nothing changed", () => {
  render(<ContextDiff diff={diff({ sections: [section()] })} />);
  screen.getByText(/Nothing changed/);
  expect(screen.queryByText(/dropped to fit/)).toBeNull();
});

test("identical words at a different cost say why, rather than a bare delta", () => {
  // Conversation history counts per MESSAGE, so a change in how the transcript
  // groups moves the total while the joined text stays byte-identical — which
  // is what a PC rename does, her blocks reparsing from `user` to `assistant`
  // and merging runs that used to alternate.
  render(<ContextDiff diff={diff({
    sections: [section({ id: "history", label: "Conversation history", status: "changed",
                         base: facts({ tokens: 54 }), head: facts({ tokens: 42 }),
                         diff: [] })],
  })} />);
  screen.getByText(/Identical text, counted differently/);
  screen.getByText("−12");
});

test("a section with lines to show is not given the counted-differently note", () => {
  render(<ContextDiff diff={diff({
    sections: [section({ status: "changed", base: facts({ tokens: 10 }),
                         head: facts({ tokens: 20 }),
                         diff: [{ op: "insert", text: "new" }] })],
  })} />);
  expect(screen.queryByText(/counted differently/)).toBeNull();
});
