import { fixture } from "../testkit/fixture";
import type { Actor } from "../api/types";
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
import type { Suggestion } from "../api/types";

const SUGGESTIONS = [
  { character: "mara", name: "Mara", mentioned_by: ["seraphine"] },
  { character: "winifred", name: "Winifred", mentioned_by: ["seraphine", "mara"] },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSuggestions).mockResolvedValue(SUGGESTIONS);
  vi.mocked(api.dismissSuggestion).mockResolvedValue({ ok: true });
  vi.mocked(api.addToCast).mockResolvedValue({ ok: true });
});

const NAMES: Record<string, string> = { seraphine: "Seraphine", mara: "Mara" };
const CAST = fixture<Actor[]>([{ kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" }]);

function renderStrip(props: Partial<{ cast: Actor[]; onCast: () => void }> = {}) {
  render(<SuggestedCast cid="c" sid="s" cast={props.cast ?? CAST}
                        nameOf={(id) => NAMES[id] ?? id} onCast={props.onCast} />);
}

test("renders one chip per suggestion, with who mentioned them", async () => {
  renderStrip();
  await screen.findByText("Mara");
  expect(screen.getByText("mentioned by Seraphine")).toBeInTheDocument();
  expect(screen.getByText("mentioned by Seraphine, Mara")).toBeInTheDocument();
});

test("renders nothing when there are no suggestions", async () => {
  vi.mocked(api.getSuggestions).mockResolvedValue([]);
  const { container } = render(<SuggestedCast cid="c" sid="s" cast={CAST} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("a failing fetch stays silent rather than blocking the panel", async () => {
  vi.mocked(api.getSuggestions).mockRejectedValue({ detail: "nope" });
  const { container } = render(<SuggestedCast cid="c" sid="s" cast={CAST} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("an empty cast is never scanned: it can name nobody", async () => {
  const { container } = render(<SuggestedCast cid="c" sid="s" cast={[]} />);
  await new Promise((r) => setTimeout(r, 0));
  expect(api.getSuggestions).not.toHaveBeenCalled();
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
  vi.mocked(api.addToCast).mockRejectedValue({ detail: "already cast" });
  renderStrip();
  fireEvent.click(await screen.findByRole("button", { name: "Add Mara to the scene" }));
  expect(await screen.findByText("already cast")).toBeInTheDocument();
  expect(screen.getByText("Mara")).toBeInTheDocument();
});

test("a changed cast rescans; the same cast re-rendered does not", async () => {
  // The scan reads the cards of who is cast, so the cast is the only thing
  // worth re-running for — and re-running for anything else is a request per
  // turn spent to be told the same thing.
  const grown = fixture<Actor[]>([...CAST, { kind: "characters", id: "mara", role: "npc", name: "Mara" }]);
  const { rerender } = render(<SuggestedCast cid="c" sid="s" cast={CAST} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(1));
  rerender(<SuggestedCast cid="c" sid="s" cast={[...CAST]} nameOf={(id) => id} />);
  await new Promise((r) => setTimeout(r, 0));
  expect(api.getSuggestions).toHaveBeenCalledTimes(1);   // same people, no rescan
  rerender(<SuggestedCast cid="c" sid="s" cast={grown} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
});

test("a swap keeps the cast's size but still rescans", async () => {
  // The bug a length-keyed reload would have: one out, one in, no refetch.
  const swapped = fixture<Actor[]>([{ kind: "characters", id: "winifred", role: "npc", name: "Winifred" }]);
  const { rerender } = render(<SuggestedCast cid="c" sid="s" cast={CAST} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(1));
  rerender(<SuggestedCast cid="c" sid="s" cast={swapped} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
});

test("the row being acted on is the one that says so", async () => {
  let release: (v: Awaited<ReturnType<typeof api.addToCast>>) => void = () => {};
  vi.mocked(api.addToCast).mockImplementation(() => new Promise((r) => { release = r; }));
  vi.mocked(api.getSuggestions).mockResolvedValue([
    ...SUGGESTIONS.slice(0, 1),
    { character: "winifred", name: "Winifred", mentioned_by: ["mara"] },
  ]);
  renderStrip();
  fireEvent.click(await screen.findByRole("button", { name: "Add Mara to the scene" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Add Mara to the scene" })).toHaveTextContent("…"));
  expect(screen.getByRole("button", { name: "Add Winifred to the scene" })).toHaveTextContent("Add");
  expect(screen.getByRole("button", { name: "Dismiss Winifred" })).toBeDisabled();

  // settle it inside the test: an unresolved write outliving the test lands
  // its reload in whatever runs next
  release({ ok: true });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Add Mara to the scene" })).toHaveTextContent("Add"));
});

test("a double click seats the character once", async () => {
  // React flushes a click before the next one is dispatched, so `disabled`
  // is what actually stops the second write — this fails the moment it goes.
  renderStrip();
  const add = await screen.findByRole("button", { name: "Add Mara to the scene" });
  fireEvent.click(add);
  fireEvent.click(add);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
  expect(api.addToCast).toHaveBeenCalledTimes(1);
});

test("a suggestion answering the previous scene never lands on this one", async () => {
  // The reader can switch scenes mid-flight; the stale answer must not paint,
  // or scene A's suggestions offer to cast people into scene B.
  let release: (v: Suggestion[]) => void = () => {};
  vi.mocked(api.getSuggestions)
    .mockImplementationOnce(() => new Promise((r) => { release = r; }))
    .mockResolvedValueOnce([]);
  const { rerender, container } = render(<SuggestedCast cid="c" sid="s" cast={CAST} />);
  rerender(<SuggestedCast cid="c" sid="s2" cast={CAST} />);
  await waitFor(() => expect(api.getSuggestions).toHaveBeenCalledTimes(2));
  release(SUGGESTIONS);
  await new Promise((r) => setTimeout(r, 0));
  expect(container).toBeEmptyDOMElement();
});
