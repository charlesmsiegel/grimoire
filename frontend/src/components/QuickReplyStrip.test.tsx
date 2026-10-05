import { fireEvent, render, screen, within } from "@testing-library/react";
import type { QuickReply } from "../api/types";
import { QuickReplyStrip } from "./QuickReplyStrip";
import type { QuickReplyContext } from "./quickReplies";

const ctx: QuickReplyContext = { busy: false, rolling: false, renaming: false, sceneLocked: false,
  posts: 3, moduleKnown: true, pcless: false, ready: true, openerOffered: false, taskRunning: () => false };
const LOOK: QuickReply = { id: "a", label: "Look around", kind: "send", text: "I take in the room.", mode: "send" };
const STEER: QuickReply = { id: "b", label: "Steer", kind: "direct", text: "Bring in the rain.", mode: "send" };
const OPEN: QuickReply = { id: "c", label: "Write opener", kind: "opener" };

test("renders shown replies in order and nothing when none is shown", () => {
  const { container, rerender } = render(
    <QuickReplyStrip replies={[LOOK, OPEN, STEER]} ctx={ctx} onRun={() => {}} />);
  const bar = screen.getByRole("toolbar", { name: "Quick replies" });
  const buttons = within(bar).getAllByRole("button");
  expect(buttons.map((b) => b.textContent)).toEqual(["Look around", "Steer"]);
  expect(buttons[0]).toHaveAttribute("title", "I take in the room.");
  rerender(<QuickReplyStrip replies={[OPEN]} ctx={ctx} onRun={() => {}} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<QuickReplyStrip replies={[]} ctx={ctx} onRun={() => {}} />);
  expect(screen.queryByRole("toolbar")).toBeNull();
});

test("a click runs the reply; a disabled button does nothing", () => {
  const onRun = vi.fn();
  const { rerender } = render(<QuickReplyStrip replies={[LOOK]} ctx={ctx} onRun={onRun} />);
  fireEvent.click(screen.getByRole("button", { name: "Look around" }));
  expect(onRun).toHaveBeenCalledWith(LOOK);
  onRun.mockClear();
  rerender(<QuickReplyStrip replies={[LOOK]} ctx={{ ...ctx, busy: true }} onRun={onRun} />);
  const button = screen.getByRole("button", { name: "Look around" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(onRun).not.toHaveBeenCalled();
});
