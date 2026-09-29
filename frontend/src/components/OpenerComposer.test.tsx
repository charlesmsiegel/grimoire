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
    "c", "s1", "A debt-collector arrives.", expect.any(Function), undefined, [], []));
  await screen.findByText("Rain on the marsh road.");

  fireEvent.change(screen.getByLabelText("Opener prompt"),
                   { target: { value: "A stranger returns." } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  await waitFor(() => expect(api.opener).toHaveBeenCalledWith(
    "c", "s1", "A stranger returns.", expect.any(Function), undefined, [], []));
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

test("actor-scoped preview adopts complete labeled contributions", async () => {
  const cast = [
    { actor_ref: "grimoire", speaker: "Grimoire", version: "" },
    { actor_ref: "characters:mara", speaker: "Mara", version: "v1" },
  ];
  (api.opener as any).mockImplementation(async (_c: string, _s: string, _p: string,
                                              on: (e: any) => void) => {
    on({ snapshot: cast });
    for (const [speaker, content] of [[cast[0], "Rain falls."], [cast[1], "I wait."]] as const) {
      on({ speaker_start: speaker });
      on({ delta: content });
      on({ speaker_done: { ...speaker, content } });
    }
    on({ done: true });
  });
  (api.firstPost as any).mockResolvedValue({ ok: true });
  renderComposer({ initialPrompt: "A meeting.",
    characters: [{ ...CHARS[0], has_avatar: true, avatar_v: "v1-token" }] });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(await screen.findByText("I wait.")).toBeInTheDocument();
  expect(screen.getByAltText("Mara portrait")).toHaveAttribute("src",
    expect.stringContaining("/characters/mara/versions/v1/images/avatar"));
  fireEvent.click(screen.getByRole("button", { name: "Use" }));
  await waitFor(() => expect(api.firstPost).toHaveBeenCalledWith("c", "s1",
    "**Grimoire:** Rain falls.\n\n**Mara:** I wait.",
    [{ ...cast[0], content: "Rain falls." }, { ...cast[1], content: "I wait." }], cast));
});

test("a failed later contribution keeps Use disabled and retries only the missing speaker", async () => {
  const cast = [
    { actor_ref: "grimoire", speaker: "Grimoire", version: "" },
    { actor_ref: "characters:mara", speaker: "Mara", version: "v1" },
  ];
  (api.opener as any).mockImplementationOnce(async (_c: string, _s: string, _p: string,
                                                  on: (e: any) => void) => {
    on({ snapshot: cast });
    on({ speaker_start: cast[0] });
    on({ speaker_done: { ...cast[0], content: "Rain falls." } });
    on({ error: { kind: "rate_limit", detail: "Wait" } });
  }).mockImplementationOnce(async (_c: string, _s: string, _p: string,
                                  on: (e: any) => void) => {
    on({ snapshot: cast });
    on({ speaker_start: cast[1] });
    on({ speaker_done: { ...cast[1], content: "I wait." } });
    on({ done: true });
  });
  renderComposer({ initialPrompt: "A meeting." });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  const retry = await screen.findByRole("button", { name: "Retry remaining speakers" });
  expect(screen.getByRole("button", { name: "Use" })).toBeDisabled();
  fireEvent.click(retry);
  await waitFor(() => expect(api.opener).toHaveBeenLastCalledWith("c", "s1", "A meeting.",
    expect.any(Function), undefined, [{ ...cast[0], content: "Rain falls." }], cast));
  expect(screen.getByRole("button", { name: "Use" })).toBeEnabled();
});

test("a failed contribution leaves only completed speakers in the preview", async () => {
  const cast = [
    { actor_ref: "grimoire", speaker: "Grimoire", version: "" },
    { actor_ref: "characters:mara", speaker: "Mara", version: "v1" },
  ];
  (api.opener as any).mockImplementation(async (_c: string, _s: string, _p: string,
                                             on: (e: any) => void) => {
    on({ snapshot: cast });
    on({ speaker_start: cast[0] });
    on({ speaker_done: { ...cast[0], content: "Rain falls." } });
    on({ speaker_start: cast[1] });
    on({ delta: "An unfinished sentence" });
    on({ error: { kind: "network", detail: "Connection lost" } });
  });
  renderComposer({ initialPrompt: "A meeting." });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  await screen.findByRole("button", { name: "Retry remaining speakers" });
  expect(screen.getByText("Rain falls.")).toBeInTheDocument();
  expect(screen.queryByText(/An unfinished sentence/)).toBeNull();
  expect(screen.getByRole("button", { name: "Use" })).toBeDisabled();
});
