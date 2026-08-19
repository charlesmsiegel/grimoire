import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CastChanges from "./CastChanges";

const castChanges = vi.fn();
const addToCast = vi.fn();
const removeFromCast = vi.fn();
const dismissSuggestion = vi.fn();
const createEmergentCast = vi.fn();

vi.mock("../../api/client", () => ({
  api: {
    castChanges: (...a: unknown[]) => castChanges(...a),
    addToCast: (...a: unknown[]) => addToCast(...a),
    removeFromCast: (...a: unknown[]) => removeFromCast(...a),
    dismissSuggestion: (...a: unknown[]) => dismissSuggestion(...a),
    createEmergentCast: (...a: unknown[]) => createEmergentCast(...a),
  },
}));

const CHANGES = {
  enter: [{ kind: "characters", id: "mara", name: "Mara", mentioned_by: ["Seraphine"] }],
  leave: [{ kind: "characters", id: "seraphine", name: "Seraphine",
            quote: "Seraphine slips out." }],
  unknown: [{ name: "Winifred", mentioned_by: ["Seraphine"] }],
};
const NOTHING = { enter: [], leave: [], unknown: [] };

const changed = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  castChanges.mockResolvedValue(CHANGES);
  addToCast.mockResolvedValue({ ok: true });
  removeFromCast.mockResolvedValue({ ok: true });
  dismissSuggestion.mockResolvedValue({ ok: true });
  createEmergentCast.mockResolvedValue({ character: "winifred", version: "default",
                                         name: "Winifred" });
});

function renderPanel(posts = 2, refreshKey = 1) {
  return render(<CastChanges cid="run" sid="s1" posts={posts} refreshKey={refreshKey}
                             onChanged={changed} />);
}

it("shows one chip row per candidate, with what it was read from", async () => {
  renderPanel();
  expect(await screen.findByText("Mara is named but not in the scene")).toBeInTheDocument();
  expect(screen.getByText("Seraphine seems to have left")).toBeInTheDocument();
  expect(screen.getByText("“Seraphine slips out.”")).toBeInTheDocument();
  expect(screen.getByText("Winifred is new to this campaign")).toBeInTheDocument();
});

it("scans nothing until the scene has a post", () => {
  renderPanel(0);
  expect(castChanges).not.toHaveBeenCalled();
});

it("re-scans when the parent re-reads the scene, not when the window grows", async () => {
  /* The transcript fetch is windowed (#94): in a long scene `posts` is the page
     size and does not move when a turn lands, so the scan hangs off the parent's
     scene-read counter instead. */
  const { rerender } = renderPanel(2, 1);
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(1));
  rerender(<CastChanges cid="run" sid="s1" posts={3} refreshKey={1} onChanged={changed} />);
  expect(castChanges).toHaveBeenCalledTimes(1);
  rerender(<CastChanges cid="run" sid="s1" posts={3} refreshKey={2} onChanged={changed} />);
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(2));
});

it("renders nothing when the turn changed no one", async () => {
  castChanges.mockResolvedValue(NOTHING);
  const { container } = renderPanel();
  await waitFor(() => expect(castChanges).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

it("seats an entering character on confirm, then re-reads", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Add Mara" }));
  expect(addToCast).toHaveBeenCalledWith("run", "s1", { kind: "characters", id: "mara" });
  expect(changed).toHaveBeenCalled();
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(2));
});

it("removes a departing cast member on confirm", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Remove Seraphine" }));
  expect(removeFromCast).toHaveBeenCalledWith("run", "s1", "characters", "seraphine");
});

it("creates an unknown name as a campaign character", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Create Winifred" }));
  expect(createEmergentCast).toHaveBeenCalledWith("run", "s1", "Winifred");
});

it("dismisses an enter candidate by id and an unknown name by name", async () => {
  renderPanel();
  const rows = await screen.findAllByRole("button", { name: "Dismiss" });
  await userEvent.click(rows[0]);
  expect(dismissSuggestion).toHaveBeenCalledWith("run", "s1", "mara");
  await userEvent.click(rows[1]);
  expect(dismissSuggestion).toHaveBeenCalledWith("run", "s1", "Winifred");
});

it("hides a rejected departure locally, without storing a dismissal", async () => {
  /* A stored dismissal is keyed by character id and would also silence that
     character's future *enter* suggestions -- not what "no, they didn't leave"
     means. */
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Not yet" }));
  expect(dismissSuggestion).not.toHaveBeenCalled();
  expect(screen.queryByText("Seraphine seems to have left")).not.toBeInTheDocument();
});

it("reports a failed confirm instead of silently dropping it", async () => {
  addToCast.mockRejectedValue({ detail: "seraphine is locked to version corrupted" });
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Add Mara" }));
  expect(await screen.findByText("seraphine is locked to version corrupted")).toBeInTheDocument();
});
