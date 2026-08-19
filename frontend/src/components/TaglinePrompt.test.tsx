import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, test, expect, beforeEach } from "vitest";
import { TaglinePrompt } from "./TaglinePrompt";
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
