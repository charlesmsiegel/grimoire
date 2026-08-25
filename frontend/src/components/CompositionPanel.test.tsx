import { act, fireEvent, render, screen } from "@testing-library/react";
import { CompositionPanel } from "./CompositionPanel";

vi.mock("../api/client", () => ({
  api: {
    getIncoming: vi.fn(), listDiverged: vi.fn(), listAppearances: vi.fn(),
    listCharacters: vi.fn(), listPCs: vi.fn(),
  },
}));
import { api } from "../api/client";

const CONFLICT = {
  ref: { kind: "locations", id: "saltmarch-harbor" }, status: "conflict",
  world: { name: "Saltmarch Harbour", body: "The harbour is blockaded." },
  mine: { name: "Saltmarch Harbour", body: "A busy port town." },
};
const UPDATE = {
  ref: { kind: "lore", id: "winifred" }, status: "update",
  world: { name: "Winifred", body: "Kept the tide ledger." },
  mine: { name: "Winifred", body: "Kept the tide ledger." },
};
const LOCKED = { kind: "characters", id: "seraphine", version: "main",
                 role: "npc", scenes: ["s1", "s2"] };

const onReview = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (api.getIncoming as any).mockResolvedValue([]);
  (api.listDiverged as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
});

async function renderPanel() {
  const { container } = render(<CompositionPanel cid="saltmarch-nights" onReview={onReview} />);
  await act(async () => {});
  return container;
}

/** The open record's sidebar. The state badge is deliberately rendered twice —
 *  once on the rail row, once beside the explanation — so a bare `getByText`
 *  finds two of it and says nothing about which one it meant. */
const sidebar = (container: HTMLElement) =>
  container.querySelector(".detail-sidebar") as HTMLElement;

test("the three systems fold into one row per ref, not three lists", async () => {
  (api.getIncoming as any).mockResolvedValue([CONFLICT, UPDATE]);
  (api.listDiverged as any).mockResolvedValue([
    { ref: { kind: "items", id: "sunblade" }, name: "Sunblade" }]);
  (api.listAppearances as any).mockResolvedValue([LOCKED]);
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine" }]);
  await renderPanel();

  expect(screen.getByRole("button", { name: /Saltmarch Harbour/ }).textContent)
    .toContain("conflict");
  expect(screen.getByRole("button", { name: /Winifred/ }).textContent)
    .toContain("update pending");
  expect(screen.getByRole("button", { name: /Sunblade/ }).textContent)
    .toContain("campaign override");
  // `/appearances` answers with ids and no names on purpose; the campaign's own
  // character listing is where the name comes from.
  expect(screen.getByRole("button", { name: /Seraphine/ })).toBeInTheDocument();
});

test("a ref the world moved AND this campaign changed is one conflict, not two rows", async () => {
  (api.getIncoming as any).mockResolvedValue([CONFLICT]);
  (api.listDiverged as any).mockResolvedValue([
    { ref: { kind: "locations", id: "saltmarch-harbor" }, name: "Saltmarch Harbour" }]);
  await renderPanel();
  const rows = screen.getAllByRole("button").filter((b) => b.className.startsWith("row"));
  expect(rows).toHaveLength(1);
  expect(rows[0].textContent).toContain("conflict");
  expect(rows[0].textContent).not.toContain("campaign override");
});

test("a version lock rides beside the state rather than replacing it", async () => {
  (api.getIncoming as any).mockResolvedValue([
    { ...UPDATE, ref: { kind: "characters", id: "seraphine" },
      world: { name: "Seraphine", version: "main", body: "" },
      mine: { name: "Seraphine", version: "main", body: "" } }]);
  (api.listAppearances as any).mockResolvedValue([LOCKED]);
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine" }]);
  const container = await renderPanel();

  fireEvent.click(screen.getByRole("button", { name: /Seraphine/ }));
  await act(async () => {});
  // Both facts, side by side: the sync ref has an update, and the actor is
  // pinned. Their upgrade verbs are different calls, so collapsing them into one
  // status is how the wrong one gets fired.
  expect(sidebar(container).textContent).toContain("update pending");
  expect(sidebar(container).textContent).toContain("version-locked");
  expect(screen.getByText(/Pinned to world version “main” as npc, in 2 scenes/))
    .toBeInTheDocument();
  expect(screen.getByText(/only by being imported, never by accepting an update/))
    .toBeInTheDocument();
});

test("an actor with a lock and nothing pending is listed as following the world", async () => {
  (api.listAppearances as any).mockResolvedValue([LOCKED]);
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine" }]);
  const container = await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Seraphine/ }));
  await act(async () => {});
  expect(sidebar(container).textContent).toContain("following the world");
  expect(screen.getByRole("button", { name: "See the change" })).toBeDisabled();
});

test("the banner counts what is pending and says how much of it is contested", async () => {
  (api.getIncoming as any).mockResolvedValue([CONFLICT, UPDATE]);
  await renderPanel();
  const banner = screen.getByText(/2 updates pending/);
  expect(banner.textContent).toContain("1 of them in conflict");
});

test("the banner hands the review a ref rather than accepting anything itself", async () => {
  (api.getIncoming as any).mockResolvedValue([CONFLICT, UPDATE]);
  await renderPanel();
  // No Accept anywhere: taking a world change is destructive and has no undo,
  // and the panel that owns that decision is the one that shows the diff.
  expect(screen.queryByRole("button", { name: /^Accept/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review world updates" }));
  expect(onReview).toHaveBeenCalledWith({ kind: "locations", id: "saltmarch-harbor" });
});

test("a row's detail sends the review to that ref", async () => {
  (api.getIncoming as any).mockResolvedValue([CONFLICT, UPDATE]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await act(async () => {});
  expect(screen.getByText(/taking the world's loses nothing/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "See the change" }));
  expect(onReview).toHaveBeenCalledWith({ kind: "lore", id: "winifred" });
});

test("a diverged record has nothing to review, and is told where its moves live", async () => {
  (api.listDiverged as any).mockResolvedValue([
    { ref: { kind: "items", id: "sunblade" }, name: "Sunblade" }]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Sunblade/ }));
  await act(async () => {});
  expect(screen.getByRole("button", { name: "See the change" })).toBeDisabled();
  expect(screen.getByText(/Promote or push this record from its own editor/))
    .toBeInTheDocument();
});

test("a resolve landing elsewhere re-reads the three sources", async () => {
  // Both panels can be open at once and read the same `/incoming`. Without this
  // the rows and the pending count go on reporting a change already accepted,
  // and one accept can resolve several rows.
  (api.getIncoming as any).mockResolvedValue([CONFLICT, UPDATE]);
  const { rerender } = render(
    <CompositionPanel cid="saltmarch-nights" onReview={onReview} refreshKey={0} />);
  await act(async () => {});
  expect(api.getIncoming).toHaveBeenCalledTimes(1);

  (api.getIncoming as any).mockResolvedValue([UPDATE]);
  rerender(<CompositionPanel cid="saltmarch-nights" onReview={onReview} refreshKey={1} />);
  await act(async () => {});
  expect(api.getIncoming).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /Saltmarch Harbour/ })).not.toBeInTheDocument();
  expect(screen.getByText(/1 update pending/)).toBeInTheDocument();
});

test("a campaign with nothing outstanding says so in all three senses", async () => {
  await renderPanel();
  expect(screen.getByText(/no world change waiting/)).toBeInTheDocument();
  expect(screen.queryByText(/updates pending/)).not.toBeInTheDocument();
  // ...and does not claim the fourth sense it cannot see.
  expect(screen.getByText(/without pinning is not among the three reads/))
    .toBeInTheDocument();
});

test("a failed read is reported, because 'nothing outstanding' is the wrong answer", async () => {
  (api.getIncoming as any).mockRejectedValue(new Error("campaign not found"));
  await renderPanel();
  expect(screen.getByRole("alert").textContent).toContain("campaign not found");
  expect(screen.queryByText(/no world change waiting/)).not.toBeInTheDocument();

  (api.getIncoming as any).mockResolvedValue([UPDATE]);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await act(async () => {});
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Winifred/ })).toBeInTheDocument();
});

test("the empty body says what this view cannot enumerate yet", async () => {
  (api.getIncoming as any).mockResolvedValue([UPDATE]);
  await renderPanel();
  // Honest about its own blind spot: no read here lists the manifest, so a
  // record the campaign follows with nothing pending is simply absent.
  expect(screen.getByText(/no read this panel makes reports\s+them/)).toBeInTheDocument();
  expect(screen.getByText(/edited here but never pinned/)).toBeInTheDocument();
});
