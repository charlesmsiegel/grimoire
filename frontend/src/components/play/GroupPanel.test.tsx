import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Actor, GroupSettings } from "../../api/client";
import GroupPanel from "./GroupPanel";

const setSceneGroup = vi.fn();

vi.mock("../../api/client", () => ({
  api: { setSceneGroup: (...a: unknown[]) => setSceneGroup(...a) },
}));

const CAST: Actor[] = [
  { kind: "pcs", id: "seraphine", name: "Seraphine", role: "player" },
  { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  { kind: "characters", id: "winifred", name: "Winifred", role: "npc" },
];

const BASE: GroupSettings = {
  order: "directed", order_list: [], talkativeness: {}, sitting_out: [], auto_rounds: 0,
};

const onChange = vi.fn();
const onClose = vi.fn();

function setup(settings: Partial<GroupSettings> = {}, cast: Actor[] = CAST) {
  return render(<GroupPanel cid="c1" sid="s1" cast={cast}
    settings={{ ...BASE, ...settings }} onChange={onChange} onClose={onClose} />);
}

/** A PUT the test resolves by hand, so edits can be made while it is in flight. */
function deferred() {
  let resolve!: (v: unknown) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  setSceneGroup.mockImplementation(async (_c: string, _s: string, s: GroupSettings) =>
    ({ ok: true, settings: s }));
});

test("changing the order to List saves it", async () => {
  setup();
  fireEvent.change(screen.getByLabelText("Speaker order"), { target: { value: "list" } });
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup).toHaveBeenCalledWith("c1", "s1", { ...BASE, order: "list" });
  await waitFor(() => expect(onChange).toHaveBeenCalledWith({ ...BASE, order: "list" }));
});

test("offers the four orders", () => {
  setup();
  const options = Array.from(screen.getByLabelText("Speaker order").querySelectorAll("option"))
    .map((o) => o.textContent);
  expect(options).toEqual(["Directed", "Manual", "List", "Natural"]);
});

test("the talkativeness slider shows in Directed and Natural only", () => {
  const { rerender } = setup({ order: "directed" });
  expect(screen.getByLabelText("Mara talkativeness")).toBeInTheDocument();
  const props = { cid: "c1", sid: "s1", cast: CAST, onChange, onClose };
  rerender(<GroupPanel {...props} settings={{ ...BASE, order: "natural" }} />);
  expect(screen.getByLabelText("Mara talkativeness")).toBeInTheDocument();
});

test.each(["list", "manual"] as const)("no talkativeness slider in %s", (order) => {
  setup({ order });
  expect(screen.queryByLabelText("Mara talkativeness")).not.toBeInTheDocument();
});

test("an unset talkativeness shows 100 in Directed and 50 in Natural", () => {
  const { unmount } = setup({ order: "directed" });
  expect(screen.getByLabelText("Mara talkativeness")).toHaveValue("100");
  unmount();
  setup({ order: "natural" });
  expect(screen.getByLabelText("Mara talkativeness")).toHaveValue("50");
});

test("a stored talkativeness wins over the mode's default", () => {
  setup({ order: "natural", talkativeness: { "characters:mara": 15 } });
  expect(screen.getByLabelText("Mara talkativeness")).toHaveValue("15");
});

test("moving the slider stores an explicit value", async () => {
  setup({ order: "natural" });
  fireEvent.change(screen.getByLabelText("Mara talkativeness"), { target: { value: "80" } });
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].talkativeness).toEqual({ "characters:mara": 80 });
});

test("only NPCs get controls, and the slider steps by five", () => {
  setup();
  expect(screen.queryByLabelText("Seraphine sits out")).not.toBeInTheDocument();
  const slider = screen.getByLabelText("Winifred talkativeness");
  expect(slider).toHaveAttribute("min", "0");
  expect(slider).toHaveAttribute("max", "100");
  expect(slider).toHaveAttribute("step", "5");
});

test("checking Mara sits out sends her reference", async () => {
  setup();
  fireEvent.click(screen.getByLabelText("Mara sits out"));
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].sitting_out).toEqual(["characters:mara"]);
});

test("sit out is offered in every mode, and unchecking removes the reference", async () => {
  setup({ order: "manual", sitting_out: ["characters:mara"] });
  const box = screen.getByLabelText("Mara sits out");
  expect(box).toBeChecked();
  fireEvent.click(box);
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].sitting_out).toEqual([]);
});

test("automatic rounds is a 0 to 5 number, disabled in Manual", async () => {
  const { unmount } = setup({ order: "manual" });
  expect(screen.getByLabelText("Automatic rounds")).toBeDisabled();
  unmount();
  setup();
  const rounds = screen.getByLabelText("Automatic rounds");
  expect(rounds).toBeEnabled();
  expect(rounds).toHaveAttribute("min", "0");
  expect(rounds).toHaveAttribute("max", "5");
  fireEvent.change(rounds, { target: { value: "3" } });
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].auto_rounds).toBe(3);
});

test("List mode lists the NPCs in order and Move Winifred up writes the order", async () => {
  setup({ order: "list" });
  fireEvent.click(screen.getByRole("button", { name: "Move Winifred up" }));
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].order_list)
    .toEqual(["characters:winifred", "characters:mara"]);
});

test("List order follows the stored list, with newcomers after it", () => {
  setup({ order: "list", order_list: ["characters:winifred"] });
  const up = screen.getAllByRole("button", { name: /^Move .* up$/ }).map((b) => b.getAttribute("aria-label") ?? b.textContent);
  expect(up).toEqual(["Move Winifred up", "Move Mara up"]);
});

test("the list controls are absent outside List mode", () => {
  setup({ order: "directed" });
  expect(screen.queryByRole("button", { name: "Move Mara down" })).not.toBeInTheDocument();
});

test("reordering keeps an absent character's stored position", async () => {
  // Seraphine-the-NPC left the scene but still holds the middle slot.
  const cast: Actor[] = [
    { kind: "characters", id: "mara", name: "Mara", role: "npc" },
    { kind: "characters", id: "winifred", name: "Winifred", role: "npc" },
  ];
  setup({ order: "list",
          order_list: ["characters:mara", "characters:gone", "characters:winifred"] }, cast);
  fireEvent.click(screen.getByRole("button", { name: "Move Winifred up" }));
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledOnce());
  expect(setSceneGroup.mock.calls[0][2].order_list)
    .toEqual(["characters:winifred", "characters:gone", "characters:mara"]);
});

test("a rejected save shows an alert and leaves the controls on the prior value", async () => {
  setSceneGroup.mockRejectedValue(Object.assign(new Error("x"), { detail: "scene is busy" }));
  setup();
  const select = screen.getByLabelText("Speaker order");
  fireEvent.change(select, { target: { value: "list" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("scene is busy");
  expect(select).toHaveValue("directed");
  expect(onChange).not.toHaveBeenCalled();
});

test("two quick edits both reach the server", async () => {
  const first = deferred();
  setSceneGroup.mockReset();
  setSceneGroup.mockReturnValueOnce(first.promise);
  setSceneGroup.mockImplementation(async (_c: string, _s: string, s: GroupSettings) =>
    ({ ok: true, settings: s }));
  setup({ order: "natural" });
  fireEvent.change(screen.getByLabelText("Mara talkativeness"), { target: { value: "80" } });
  fireEvent.click(screen.getByLabelText("Winifred sits out"));
  // Sent one at a time: the second waits for the first.
  expect(setSceneGroup).toHaveBeenCalledOnce();
  first.resolve({ ok: true, settings: { ...BASE, order: "natural",
    talkativeness: { "characters:mara": 80 } } });
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledTimes(2));
  const second = setSceneGroup.mock.calls[1][2];
  expect(second.talkativeness).toEqual({ "characters:mara": 80 });
  expect(second.sitting_out).toEqual(["characters:winifred"]);
  // The box stays checked while the first PUT's answer lands.
  await waitFor(() => expect(screen.getByLabelText("Winifred sits out")).toBeChecked());
});

test("a failure after a confirmed save reverts to the last confirmed settings", async () => {
  const first = deferred();
  const second = deferred();
  setSceneGroup.mockReset();
  setSceneGroup.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  setup();
  fireEvent.click(screen.getByLabelText("Mara sits out"));
  fireEvent.click(screen.getByLabelText("Winifred sits out"));
  first.resolve({ ok: true, settings: { ...BASE, sitting_out: ["characters:mara"] } });
  await waitFor(() => expect(setSceneGroup).toHaveBeenCalledTimes(2));
  second.reject(Object.assign(new Error("x"), { detail: "nope" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  expect(screen.getByLabelText("Mara sits out")).toBeChecked();
  expect(screen.getByLabelText("Winifred sits out")).not.toBeChecked();
});

test("Close calls onClose", () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(onClose).toHaveBeenCalledOnce();
});
