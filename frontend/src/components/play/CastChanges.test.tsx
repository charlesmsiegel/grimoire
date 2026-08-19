import { act, render, screen, waitFor } from "@testing-library/react";
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

function renderPanel(hasPosts = true, refreshKey = 1) {
  return render(<CastChanges cid="run" sid="s1" hasPosts={hasPosts} refreshKey={refreshKey}
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
  renderPanel(false);
  expect(castChanges).not.toHaveBeenCalled();
});

it("re-scans on a scene re-read, with the post count standing still", async () => {
  /* The property that matters in a long scene: the transcript fetch is windowed
     (#94), so the post count is the page size and does not move when a turn
     lands. If the scan hung off it, the column would go quiet for the rest of
     the scene. */
  const { rerender } = renderPanel(true, 1);
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(1));
  rerender(<CastChanges cid="run" sid="s1" hasPosts refreshKey={2} onChanged={changed} />);
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

it("keeps a rejected departure hidden when another chip triggers a re-scan", async () => {
  /* Confirming any chip bumps the parent's scene-read counter, so clearing the
     local list on every scan would bring the waved-off departure straight back. */
  const { rerender } = renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Not yet" }));
  rerender(<CastChanges cid="run" sid="s1" hasPosts refreshKey={2} onChanged={changed} />);
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Seraphine seems to have left")).not.toBeInTheDocument();
});

it("offers the same actor again when a LATER turn walks them off", async () => {
  const { rerender } = renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Not yet" }));
  castChanges.mockResolvedValue({
    ...CHANGES,
    leave: [{ kind: "characters", id: "seraphine", name: "Seraphine",
              quote: "Seraphine rides off at dawn." }],
  });
  rerender(<CastChanges cid="run" sid="s1" hasPosts refreshKey={2} onChanged={changed} />);
  expect(await screen.findByText("“Seraphine rides off at dawn.”")).toBeInTheDocument();
});

it("ignores a scan that lands after a newer one", async () => {
  /* A confirm reloads while the parent's re-read starts a second scan; the
     older result must not install itself over the newer. */
  let settleFirst: (c: unknown) => void = () => {};
  castChanges.mockImplementationOnce(() => new Promise((r) => { settleFirst = r; }));
  const { rerender } = renderPanel();
  rerender(<CastChanges cid="run" sid="s1" hasPosts refreshKey={2} onChanged={changed} />);
  await waitFor(() => expect(castChanges).toHaveBeenCalledTimes(2));
  await screen.findByText("Mara is named but not in the scene");   // the newer scan
  settleFirst({ ...CHANGES, enter: [{ kind: "characters", id: "stale", name: "Stale",
                                      mentioned_by: ["Seraphine"] }] });
  // Waited out rather than queried straight away: `queryBy… not.toBeInTheDocument`
  // passes trivially before the update it is meant to catch has been applied.
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(screen.queryByText("Stale is named but not in the scene")).not.toBeInTheDocument();
  expect(screen.getByText("Mara is named but not in the scene")).toBeInTheDocument();
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
