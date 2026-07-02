import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, test, expect, beforeEach } from "vitest";
import { TaglinePrompt } from "./TaglinePrompt";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
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
