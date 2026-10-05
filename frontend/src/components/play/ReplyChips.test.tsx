import { fireEvent, render, screen, within } from "@testing-library/react";
import type { Actor } from "../../api/client";
import ReplyChips from "./ReplyChips";

const CAST: Actor[] = [
  { kind: "pcs", id: "winifred", name: "Winifred", role: "player" },
  { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  { kind: "characters", id: "seraphine", name: "Seraphine", role: "npc" },
];

function setup(over: Partial<React.ComponentProps<typeof ReplyChips>> = {}) {
  const onReply = vi.fn();
  render(<ReplyChips cast={CAST} sittingOut={[]} disabled={false} onReply={onReply} {...over} />);
  return { onReply, group: screen.getByRole("group", { name: "Reply as" }) };
}

test("renders Grimoire and the NPCs, but not the player", () => {
  const { group } = setup();
  const names = within(group).getAllByRole("button").map((b) => b.textContent);
  expect(names).toEqual(["Grimoire", "Mara", "Seraphine"]);
});

test("clicking a chip replies as that reference", () => {
  const { onReply, group } = setup();
  fireEvent.click(within(group).getByRole("button", { name: "Mara" }));
  expect(onReply).toHaveBeenCalledWith("characters:mara");
  fireEvent.click(within(group).getByRole("button", { name: "Grimoire" }));
  expect(onReply).toHaveBeenLastCalledWith("grimoire");
});

test("a sitting-out chip is dimmed but still enabled", () => {
  const { onReply, group } = setup({ sittingOut: ["characters:mara"] });
  const mara = within(group).getByRole("button", { name: "Mara" });
  expect(mara).toHaveClass("sitting-out");
  expect(mara).toHaveAttribute("title", "Sitting out — tap to have them reply anyway");
  expect(mara).toBeEnabled();
  expect(within(group).getByRole("button", { name: "Seraphine" })).not.toHaveClass("sitting-out");
  fireEvent.click(mara);
  expect(onReply).toHaveBeenCalledWith("characters:mara");
});

test("disabled disables every chip", () => {
  const { group } = setup({ disabled: true });
  for (const b of within(group).getAllByRole("button")) expect(b).toBeDisabled();
});
