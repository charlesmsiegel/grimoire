import { fireEvent, render, screen, within } from "@testing-library/react";
import { PostActions, type PostAction } from "./PostActions";
import { postActions, type TranscriptActions } from "./TranscriptPost";

function actions(over: Partial<Record<"edit" | "cut", Partial<PostAction>>> = {}) {
  const edit = vi.fn();
  const cut = vi.fn();
  const list: PostAction[] = [
    { label: "Edit message", name: "Edit message 3", glyph: "✎", run: edit, ...over.edit },
    { label: "Delete this post and everything after it",
      name: "Delete message 3 and everything after it", glyph: "✂", danger: true, run: cut,
      ...over.cut },
  ];
  return { list, edit, cut };
}

const more = () => screen.getByRole("button", { name: "Actions for message 3" });

test("every action is a named button with its label as the tooltip, behind a shut ⋯", () => {
  const { list } = actions();
  render(<PostActions post={3} actions={list} />);
  const group = screen.getByRole("group", { name: "Message 3 actions" });
  const edit = within(group).getByRole("button", { name: "Edit message 3" });
  expect(edit).toHaveAttribute("title", "Edit message");
  // The menu reads its words; the toolbar hides them and keeps the glyph.
  expect(edit).toHaveTextContent("Edit message");
  expect(within(group).getByRole("button", { name: /^Delete message 3/ })).toHaveClass("danger");
  expect(more()).toHaveAttribute("aria-expanded", "false");
  expect(more()).toHaveAttribute("aria-controls", group.id);
});

test("the ⋯ opens the menu onto its first action, and a pick runs it and shuts it", () => {
  const { list, edit } = actions();
  render(<PostActions post={3} actions={list} />);
  fireEvent.click(more());
  expect(more()).toHaveAttribute("aria-expanded", "true");
  expect(more().parentElement).toHaveClass("open");
  expect(screen.getByRole("button", { name: "Edit message 3" })).toHaveFocus();

  fireEvent.click(screen.getByRole("button", { name: "Edit message 3" }));
  expect(edit).toHaveBeenCalledTimes(1);
  expect(more()).toHaveAttribute("aria-expanded", "false");
});

test("focus lands on the first action that can be pressed", () => {
  const { list } = actions({ edit: { disabled: true } });
  render(<PostActions post={3} actions={list} />);
  fireEvent.click(more());
  expect(screen.getByRole("button", { name: /^Delete message 3/ })).toHaveFocus();
});

test("Escape shuts the menu through the shortcut registry and hands focus back to the ⋯", () => {
  const { list } = actions();
  render(<PostActions post={3} actions={list} />);
  fireEvent.click(more());
  fireEvent.keyDown(window, { key: "Escape" });
  expect(more()).toHaveAttribute("aria-expanded", "false");
  expect(more()).toHaveFocus();
});

test("a press outside shuts the menu, and a press inside it does not", () => {
  const { list } = actions();
  render(<><p>elsewhere</p><PostActions post={3} actions={list} /></>);
  fireEvent.click(more());
  fireEvent.pointerDown(screen.getByRole("button", { name: "Edit message 3" }));
  expect(more()).toHaveAttribute("aria-expanded", "true");
  fireEvent.pointerDown(screen.getByText("elsewhere"));
  expect(more()).toHaveAttribute("aria-expanded", "false");
});

test("a disabled action does not run, and a toggle says whether it is on", () => {
  const { list, cut } = actions({ cut: { disabled: true, pressed: true } });
  render(<PostActions post={3} actions={list} />);
  const del = screen.getByRole("button", { name: /^Delete message 3/ });
  fireEvent.click(del);
  expect(cut).not.toHaveBeenCalled();
  expect(del).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Edit message 3" })).not.toHaveAttribute("aria-pressed");
});

test("a post with nothing to offer draws no ⋯ at all", () => {
  render(<PostActions post={3} actions={[]} />);
  expect(screen.queryByRole("button")).toBeNull();
});

// ---- what a post offers ----

const noop = () => {};
const ACTIONS: TranscriptActions = {
  openActor: noop, openReroll: noop, stepAlternate: noop, stepVariant: noop,
  edit: noop, toggleExcluded: noop, cancelEdit: noop, saveEdit: noop, saveRetcon: noop,
  pickImage: noop, cutFrom: noop, replayFrom: noop, branchFrom: noop,
  setRerollPrompt: noop, setRerollRoute: noop, reroll: noop, deleteResponse: noop,
  rerollResponse: noop, extendResponse: noop, activateVariant: noop,
  createCharacter: noop, testRules: noop, refreshTracker: noop,
};
const base = {
  index: 2, actor: undefined, speaker: "Seraphine", rolling: false, active: true, absorbed: false,
  rerollButton: false, canReplayAfter: true, actions: ACTIONS,
};

test("an active post's actions put the cut last, where a slip down the menu misses it", () => {
  const labels = postActions({ ...base, m: { role: "assistant", content: "Salt first." } })
    .map((a) => a.label);
  expect(labels).toEqual([
    "Edit message", "Hide from context", "Insert an image", "Test rules on this post",
    "Replay the turns after this post", "Branch from here",
    "Delete this post and everything after it",
  ]);
});

test("no action's glyph is an emoji", () => {
  // A full-colour picture among hairline glyphs reads as another app: the
  // image, cut and replay icons were 🖼 🗑 ⏩, and only the last of them is
  // an emoji by default -- the other two are whatever font answers first.
  const all = postActions({ ...base, rerollButton: true,
                            m: { role: "assistant", content: "Salt first." } });
  // A character with an emoji form at all (✂, ⚗) carries the text-style
  // selector, which is what keeps a colour emoji font from answering for it.
  for (const a of all) expect(a.glyph).not.toMatch(/\p{Emoji}(?!\uFE0E)/u);
});

test("an inactive scene's post offers only the read-only rule test", () => {
  const names = postActions({ ...base, active: false,
                              m: { role: "assistant", content: "Salt first." } })
    .map((a) => a.name);
  expect(names).toEqual(["Test rules on message 3"]);
});
