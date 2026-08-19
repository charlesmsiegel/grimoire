import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SuggestedCast } from "./SuggestedCast";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getSuggestions: vi.fn(), dismissSuggestion: vi.fn(), addToCast: vi.fn(),
    },
  };
});
import { api } from "../api/client";

const SUGGESTIONS = [
  { character: "mara", name: "Mara", mentioned_by: ["seraphine"] },
  { character: "winifred", name: "Winifred", mentioned_by: ["seraphine", "mara"] },
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getSuggestions as any).mockResolvedValue(SUGGESTIONS);
  (api.dismissSuggestion as any).mockResolvedValue({ ok: true });
  (api.addToCast as any).mockResolvedValue({ ok: true });
});

const NAMES: Record<string, string> = { seraphine: "Seraphine", mara: "Mara" };

function renderStrip(props: Partial<{ refreshKey: number; onCast: () => void }> = {}) {
  render(<SuggestedCast cid="c" sid="s" nameOf={(id) => NAMES[id] ?? id}
                        refreshKey={props.refreshKey} onCast={props.onCast} />);
}

test("renders one chip per suggestion, with who mentioned them", async () => {
  renderStrip();
  await screen.findByText("Mara");
  expect(screen.getByText("mentioned by Seraphine")).toBeInTheDocument();
  expect(screen.getByText("mentioned by Seraphine, Mara")).toBeInTheDocument();
});

test("renders nothing when there are no suggestions", async () => {
  (api.getSuggestions as any).mockResolvedValue([]);
  const { container } = render(<SuggestedCast cid="c" sid="s" />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("a failing fetch stays silent rather than blocking the panel", async () => {
  (api.getSuggestions as any).mockRejectedValue({ detail: "nope" });
  const { container } = render(<SuggestedCast cid="c" sid="s" />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("Add casts the character as an npc and refetches", async () => {
  const onCast = vi.fn();
  renderStrip({ onCast });
  fireEvent.click(await screen.findByRole("button", { name: "Add Mara to the scene" }));
  await waitFor(() =>
    expect(api.addToCast).toHaveBeenCalledWith("c", "s", { kind: "characters", id: "mara", role: "npc" }));
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
  expect(onCast).toHaveBeenCalled();
});

test("Dismiss posts the character id and refetches", async () => {
  renderStrip();
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss Mara" }));
  await waitFor(() => expect(api.dismissSuggestion).toHaveBeenCalledWith("c", "s", "mara"));
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
});

test("a failed Add surfaces the reason and leaves the chip in place", async () => {
  (api.addToCast as any).mockRejectedValue({ detail: "already cast" });
  renderStrip();
  fireEvent.click(await screen.findByRole("button", { name: "Add Mara to the scene" }));
  expect(await screen.findByText("already cast")).toBeInTheDocument();
  expect(screen.getByText("Mara")).toBeInTheDocument();
});

test("refreshKey refetches: the scan depends on who is already in the scene", async () => {
  const { rerender } = render(<SuggestedCast cid="c" sid="s" refreshKey={1} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(1));
  rerender(<SuggestedCast cid="c" sid="s" refreshKey={2} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
});

test("a suggestion answering the previous scene never lands on this one", async () => {
  // The reader can switch scenes mid-flight; the stale answer must not paint,
  // or scene A's suggestions offer to cast people into scene B.
  let release: (v: any) => void = () => {};
  (api.getSuggestions as any)
    .mockImplementationOnce(() => new Promise((r) => { release = r; }))
    .mockResolvedValueOnce([]);
  const { rerender, container } = render(<SuggestedCast cid="c" sid="s" />);
  rerender(<SuggestedCast cid="c" sid="s2" />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
  release(SUGGESTIONS);
  await new Promise((r) => setTimeout(r, 0));
  expect(container).toBeEmptyDOMElement();
});
