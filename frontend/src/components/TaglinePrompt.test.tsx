import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, test, expect, beforeEach } from "vitest";
import { TaglinePrompt } from "./TaglinePrompt";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { api, ApiError } from "../api/client";

// `ApiError` is the real class: `isOffline` reads `kind` off the rejection,
// so a hand-rolled stand-in would prove nothing about what `request` throws.
vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: { setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn() },
}));

beforeEach(() => vi.clearAllMocks());

test("typing then Save calls PUT and not generate", async () => {
  (api.setCharacterTagline as any).mockResolvedValue({ ok: true });
  const onClose = vi.fn();
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={onClose} />);
  fireEvent.change(screen.getByLabelText("Tagline"), { target: { value: "A snowleopardgirl." } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.setCharacterTagline).toHaveBeenCalledWith("w", "aese", "A snowleopardgirl."));
  expect(api.generateCharacterTagline).not.toHaveBeenCalled();
  await waitFor(() => expect(onClose).toHaveBeenCalled());
});

test("Generate fills the field from the endpoint", async () => {
  (api.generateCharacterTagline as any).mockResolvedValue({ tagline: "A generated line." });
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={vi.fn()} />);
  fireEvent.click(screen.getByText("Generate"));
  await screen.findByDisplayValue("A generated line.");
});

test("Skip closes without saving", async () => {
  const onClose = vi.fn();
  render(<TaglinePrompt wid="w" cid="aese" name="Aese" onClose={onClose} />);
  fireEvent.click(screen.getByText("Skip"));
  expect(onClose).toHaveBeenCalled();
  expect(api.setCharacterTagline).not.toHaveBeenCalled();
});

test("a Generate that cannot reach the provider offers the local-model recovery", async () => {
  // The generate endpoint 502s with `{detail, kind}`; the kind used to be
  // dropped on the floor here and the modal showed the socket error alone.
  (api.generateCharacterTagline as any).mockRejectedValue(
    new ApiError(502, "connection refused", "network"));
  render(<MemoryRouter><TaglinePrompt wid="w" cid="aese" name="Aese" onClose={vi.fn()} /></MemoryRouter>);
  fireEvent.click(screen.getByText("Generate"));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
});

test("any other Generate failure still shows the endpoint's own message", async () => {
  (api.generateCharacterTagline as any).mockRejectedValue(
    new ApiError(409, "No LLM connection selected", "missing_key"));
  render(<MemoryRouter><TaglinePrompt wid="w" cid="aese" name="Aese" onClose={vi.fn()} /></MemoryRouter>);
  fireEvent.click(screen.getByText("Generate"));
  await screen.findByText("No LLM connection selected");
  expect(screen.queryByRole("link")).toBeNull();
});

// Escape mirrors Skip, and Skip is not disabled mid-generation -- only
// Generate is. An Escape refused there would leave keyboard users the only
// ones who could not leave a prompt the mouse can still dismiss (PR #400
// review, correcting my own reading of which button `busy` disables).
test("Escape skips mid-generation, exactly as Skip does", async () => {
  const onClose = vi.fn();
  // A generate that never settles, so the dialog stays `busy`.
  (api.generateCharacterTagline as any).mockImplementation(() => new Promise(() => {}));
  render(<TaglinePrompt wid="w" cid="mara" name="Mara" onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: /generate/i }));
  await screen.findByRole("button", { name: /generating/i });
  expect(screen.getByRole("button", { name: /^skip$/i })).not.toBeDisabled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("Escape skips while idle", () => {
  const onClose = vi.fn();
  render(<TaglinePrompt wid="w" cid="mara" name="Mara" onClose={onClose} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

// The case the parent could not cover: a sheet takeover keeps its open state
// inside `SheetPanel`, so `CharacterEditor` cannot know to hold this back. The
// prompt asks the registry instead, which does know (PR #400 review).
test("it waits for an overlay it cannot see, and takes its turn when that closes", () => {
  function Overlay({ up }: { up: boolean }) {
    useHotkeys([{ keys: "escape", enabled: up, run: () => {} }], { modal: up });
    return null;
  }
  const prompt = <TaglinePrompt wid="w" cid="mara" name="Mara" onClose={() => {}} />;
  const { rerender } = render(<><Overlay up />{prompt}</>);
  expect(screen.queryByRole("dialog", { name: /set tagline/i })).toBeNull();

  rerender(<><Overlay up={false} />{prompt}</>);
  expect(screen.getByRole("dialog", { name: /set tagline/i })).toBeInTheDocument();
});

// ...and while it waits it must not hold the keyboard either: a dialog that
// renders nothing and still answers Escape is the same bug wearing a disguise.
test("while waiting it does not answer Escape", () => {
  const onClose = vi.fn();
  const below = vi.fn();
  function Overlay() {
    useHotkeys([{ keys: "escape", run: below }], { modal: true });
    return null;
  }
  render(<><Overlay /><TaglinePrompt wid="w" cid="mara" name="Mara" onClose={onClose} /></>);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).not.toHaveBeenCalled();
  expect(below).toHaveBeenCalledTimes(1);
});
