import { act, fireEvent, render, screen } from "@testing-library/react";
import { CompositionPanel } from "./CompositionPanel";

vi.mock("../api/client", () => ({
  api: { getComposition: vi.fn(), setSyncPin: vi.fn() },
}));
import { api } from "../api/client";

const CONFLICT = {
  ref: { kind: "locations", id: "saltmarch-harbor" }, name: "Saltmarch Harbour",
  state: "conflict", pinned: false, lock: null,
};
const UPDATE = {
  ref: { kind: "lore", id: "winifred" }, name: "Winifred",
  state: "update", pinned: false, lock: null,
};
const DIVERGED = {
  ref: { kind: "items", id: "sunblade" }, name: "Sunblade",
  state: "diverged", pinned: false, lock: null,
};
const INSYNC = {
  ref: { kind: "locations", id: "quay" }, name: "Quay",
  state: "insync", pinned: false, lock: null,
};
const LOCK = { version: "main", role: "npc", scenes: ["s1", "s2"] };

const onReview = vi.fn();
const onPinned = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (api.getComposition as any).mockResolvedValue({ rows: [] });
  (api.setSyncPin as any).mockResolvedValue({ pinned: [] });
});

async function renderPanel() {
  const { container } = render(
    <CompositionPanel cid="saltmarch-nights" onReview={onReview} onPinned={onPinned} />);
  await act(async () => {});
  return container;
}

/** The open record's sidebar. The state badge is deliberately rendered twice —
 *  once on the rail row, once beside the explanation — so a bare `getByText`
 *  finds two of it and says nothing about which one it meant. */
const sidebar = (container: HTMLElement) =>
  container.querySelector(".detail-sidebar") as HTMLElement;

test("one endpoint serves one row per ref, quiescent refs included", async () => {
  (api.getComposition as any).mockResolvedValue(
    { rows: [CONFLICT, UPDATE, DIVERGED, INSYNC] });
  await renderPanel();

  expect(screen.getByRole("button", { name: /Saltmarch Harbour/ }).textContent)
    .toContain("conflict");
  expect(screen.getByRole("button", { name: /Winifred/ }).textContent)
    .toContain("update pending");
  expect(screen.getByRole("button", { name: /Sunblade/ }).textContent)
    .toContain("campaign override");
  // The row the client-side join could never show: a materialized ref with
  // nothing pending. The endpoint enumerates the manifest, so it is here.
  expect(screen.getByRole("button", { name: /Quay/ }).textContent)
    .toContain("following the world");
});

test("a version lock rides beside the state rather than replacing it", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [
    { ref: { kind: "characters", id: "seraphine" }, name: "Seraphine",
      state: "update", pinned: false, lock: LOCK }] });
  const container = await renderPanel();

  fireEvent.click(screen.getByRole("button", { name: /Seraphine/ }));
  await act(async () => {});
  // Both facts, side by side: the sync ref has an update, and the actor is
  // pinned to a version. Their upgrade verbs are different calls, so collapsing
  // them into one status is how the wrong one gets fired.
  expect(sidebar(container).textContent).toContain("update pending");
  expect(sidebar(container).textContent).toContain("version-locked");
  expect(screen.getByText(/Pinned to world version “main” as npc, in 2 scenes/))
    .toBeInTheDocument();
  expect(screen.getByText(/only by being imported, never by accepting an update/))
    .toBeInTheDocument();
});

test("an actor with a lock and nothing pending is listed as following the world", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [
    { ref: { kind: "characters", id: "seraphine" }, name: "Seraphine",
      state: "insync", pinned: false, lock: LOCK }] });
  const container = await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Seraphine/ }));
  await act(async () => {});
  expect(sidebar(container).textContent).toContain("following the world");
  expect(screen.getByRole("button", { name: "See the change" })).toBeDisabled();
});

test("the banner counts what is pending and says how much of it is contested", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [CONFLICT, UPDATE] });
  await renderPanel();
  const banner = screen.getByText(/2 updates pending/);
  expect(banner.textContent).toContain("1 of them in conflict");
});

test("the banner hands the review a ref rather than accepting anything itself", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [CONFLICT, UPDATE] });
  await renderPanel();
  // No Accept anywhere: taking a world change is destructive and has no undo,
  // and the panel that owns that decision is the one that shows the diff.
  expect(screen.queryByRole("button", { name: /^Accept/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review world updates" }));
  expect(onReview).toHaveBeenCalledWith({ kind: "locations", id: "saltmarch-harbor" });
});

test("a row's detail sends the review to that ref", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [CONFLICT, UPDATE] });
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await act(async () => {});
  expect(screen.getByText(/taking the world's loses nothing/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "See the change" }));
  expect(onReview).toHaveBeenCalledWith({ kind: "lore", id: "winifred" });
});

test("a diverged record has nothing to review, and is told where its moves live", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [DIVERGED] });
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Sunblade/ }));
  await act(async () => {});
  expect(screen.getByRole("button", { name: "See the change" })).toBeDisabled();
  expect(screen.getByText(/Promote or push this record from its own editor/))
    .toBeInTheDocument();
});

test("pinning a ref writes the pin, re-reads, and tells the panel next door", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [UPDATE] });
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await act(async () => {});

  (api.getComposition as any).mockResolvedValue({ rows: [{ ...UPDATE, pinned: true }] });
  fireEvent.click(screen.getByRole("button", { name: "Stop offering world updates" }));
  await act(async () => {});

  expect(api.setSyncPin).toHaveBeenCalledWith(
    "saltmarch-nights", { kind: "lore", id: "winifred" }, true);
  // `IncomingReview` reads the same `/incoming` the pin just changed.
  expect(onPinned).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Resume world updates" })).toBeInTheDocument();
});

test("a pinned ref keeps its state on show but leaves the pending count and review", async () => {
  (api.getComposition as any).mockResolvedValue(
    { rows: [{ ...UPDATE, pinned: true }, CONFLICT] });
  const container = await renderPanel();
  // The banner advertises what the review panel will show, and the pin holds
  // the update out of it — one pending, not two.
  expect(screen.getByText(/1 update pending/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await act(async () => {});
  expect(sidebar(container).textContent).toContain("update pending");
  expect(sidebar(container).textContent).toContain("pinned");
  expect(screen.getByRole("button", { name: "See the change" })).toBeDisabled();
  expect(screen.getByText(/Nothing is rejected/)).toBeInTheDocument();
});

test("a resolve landing elsewhere re-reads the composition", async () => {
  (api.getComposition as any).mockResolvedValue({ rows: [CONFLICT, UPDATE] });
  const { rerender } = render(
    <CompositionPanel cid="saltmarch-nights" onReview={onReview} refreshKey={0} />);
  await act(async () => {});
  expect(api.getComposition).toHaveBeenCalledTimes(1);

  (api.getComposition as any).mockResolvedValue({ rows: [UPDATE] });
  rerender(<CompositionPanel cid="saltmarch-nights" onReview={onReview} refreshKey={1} />);
  await act(async () => {});
  expect(api.getComposition).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /Saltmarch Harbour/ })).not.toBeInTheDocument();
  expect(screen.getByText(/1 update pending/)).toBeInTheDocument();
});

test("a campaign holding nothing of its own says what would put a row here", async () => {
  await renderPanel();
  expect(screen.getByText(/A record joins the composition/)).toBeInTheDocument();
  expect(screen.queryByText(/updates pending/)).not.toBeInTheDocument();
});

test("a failed read is reported, because 'nothing outstanding' is the wrong answer", async () => {
  (api.getComposition as any).mockRejectedValue(new Error("campaign not found"));
  await renderPanel();
  expect(screen.getByRole("alert").textContent).toContain("campaign not found");
  expect(screen.queryByText(/A record joins the composition/)).not.toBeInTheDocument();

  (api.getComposition as any).mockResolvedValue({ rows: [UPDATE] });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await act(async () => {});
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Winifred/ })).toBeInTheDocument();
});
