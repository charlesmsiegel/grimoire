import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OpenerComposer } from "./OpenerComposer";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { opener: vi.fn(), firstPost: vi.fn(), createGreeting: vi.fn() } };
});
import { api } from "../api/client";

const CHARS = [{ id: "mara", name: "Mara", versions: [{ id: "v1", name: "v1" }],
                 default_version: "v1" }] as any;

beforeEach(() => {
  vi.clearAllMocks();
  (api.opener as any).mockImplementation(
    async (_c: string, _s: string, _p: string, on: (e: any) => void) => {
      on({ delta: "Rain on the marsh road." });
    });
});

function renderComposer(props: Record<string, unknown> = {}) {
  return render(
    <OpenerComposer cid="c" sid="s1" ready characters={CHARS}
                    onSeeded={() => {}} onError={() => {}} {...props} />);
}

// What the scene chooser's handoff is for: the premise the reader approved is
// already in the box, so starting the opener is one press and no retyping.
test("a handed-over premise arrives in the box", async () => {
  renderComposer({ initialPrompt: "A debt-collector arrives." });
  expect(screen.getByLabelText("Opener prompt")).toHaveValue("A debt-collector arrives.");
});

// Deliberate: a premise in the box is a suggestion, not an instruction. Nothing
// here may spend a call the reader did not ask for by pressing Generate.
test("arriving with a premise generates nothing on its own", async () => {
  renderComposer({ initialPrompt: "A debt-collector arrives." });
  await waitFor(() => expect(screen.getByLabelText("Opener prompt"))
    .toHaveValue("A debt-collector arrives."));
  expect(api.opener).not.toHaveBeenCalled();
});

test("Generate sends what the box holds, premise or edit", async () => {
  renderComposer({ initialPrompt: "A debt-collector arrives." });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  await waitFor(() => expect(api.opener).toHaveBeenCalledWith(
    "c", "s1", "A debt-collector arrives.", expect.any(Function)));
  await screen.findByText("Rain on the marsh road.");

  fireEvent.change(screen.getByLabelText("Opener prompt"),
                   { target: { value: "A stranger returns." } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  await waitFor(() => expect(api.opener).toHaveBeenCalledWith(
    "c", "s1", "A stranger returns.", expect.any(Function)));
});

// The reset the seeding effect exists for: one scene's premise must not linger
// in the box of the next one.
test("switching scenes clears a premise that belonged to the last one", async () => {
  const { rerender } = renderComposer({ initialPrompt: "A debt-collector arrives." });
  expect(screen.getByLabelText("Opener prompt")).toHaveValue("A debt-collector arrives.");
  rerender(<OpenerComposer cid="c" sid="s2" ready characters={CHARS}
                           onSeeded={() => {}} onError={() => {}} />);
  expect(screen.getByLabelText("Opener prompt")).toHaveValue("");
});

test("without an LLM connection the box still seeds, and says why it cannot run", async () => {
  renderComposer({ initialPrompt: "A debt-collector arrives.", ready: false });
  await screen.findByText(/Set up an LLM connection/);
  expect(screen.getByLabelText("Opener prompt")).toHaveValue("A debt-collector arrives.");
  expect(api.opener).not.toHaveBeenCalled();
});
