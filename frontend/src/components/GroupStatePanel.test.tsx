import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GroupStatePanel } from "./GroupStatePanel";

vi.mock("../api/client", () => ({
  api: { getGroupState: vi.fn(), putGroupState: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getGroupState).mockResolvedValue({
    goals: "Expand.", resources: "", focus: "", public_perception: "", secrets: "The abbot.", updated: "t" });
  vi.mocked(api.putGroupState).mockResolvedValue({ ok: true });
});

test("shows non-empty sections read-only; edit + save round-trips", async () => {
  render(<GroupStatePanel cid="c" gid="salt-circle" />);
  expect(await screen.findByText("Expand.")).toBeInTheDocument();
  expect(screen.getByText("The abbot.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Goals")).toBeNull();            // read-only by default
  fireEvent.click(screen.getByRole("button", { name: /edit state/i }));
  fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Consolidate." } });
  fireEvent.click(screen.getByRole("button", { name: /save state/i }));
  await waitFor(() => expect(api.putGroupState).toHaveBeenCalledWith("c", "salt-circle", {
    goals: "Consolidate.", resources: "", focus: "", public_perception: "", secrets: "The abbot.",
  }));
});

test("empty state shows a hint instead of sections", async () => {
  vi.mocked(api.getGroupState).mockResolvedValue({
    goals: "", resources: "", focus: "", public_perception: "", secrets: "", updated: "" });
  render(<GroupStatePanel cid="c" gid="salt-circle" />);
  expect(await screen.findByText(/no campaign state yet/i)).toBeInTheDocument();
});
