import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SceneFocusEditor } from "./SceneFocusEditor";
import { api } from "../api/client";
import type { PromptField } from "../api/types";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: { getSceneFocus: vi.fn(), setSceneFocus: vi.fn(), getPromptLayout: vi.fn() },
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSceneFocus).mockResolvedValue({ text: "Let Mara choose.", status: "ok" });
  vi.mocked(api.setSceneFocus).mockResolvedValue({ text: "Let Mara choose. Gently.", status: "ok" });
  vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: false, sections: [] });
});

test("focus is a paragraph, preserving typed spaces and normalizing boundaries at submission", async () => {
  const onSaved = vi.fn();
  render(<MemoryRouter><SceneFocusEditor cid="saltmarch" sid="mara" onSaved={onSaved} /></MemoryRouter>);
  await screen.findByText("Let Mara choose.");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("textbox", { name: /scene focus/i });
  fireEvent.change(input, { target: { value: " Let Mara " } });
  expect(input).toHaveValue(" Let Mara ");
  fireEvent.change(input, { target: { value: " Let Mara choose.\u2028Gently. " } });
  expect(input).toHaveValue(" Let Mara choose. Gently. ");
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await screen.findByText("Let Mara choose. Gently.");
  expect(api.setSceneFocus).toHaveBeenCalledWith("saltmarch", "mara", "Let Mara choose. Gently.");
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test("clear shows an unset hint; cancel never publishes", async () => {
  vi.mocked(api.setSceneFocus).mockResolvedValue({ text: "", status: "empty" });
  render(<MemoryRouter><SceneFocusEditor cid="saltmarch" sid="mara" /></MemoryRouter>);
  await screen.findByText("Let Mara choose.");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(api.setSceneFocus).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await screen.findByText(/what should this scene explore/i);
  expect(api.setSceneFocus).toHaveBeenCalledWith("saltmarch", "mara", "");
});

test("closed-branch save keeps the draft, and unreadable loads never permit a blank save", async () => {
  vi.mocked(api.setSceneFocus).mockRejectedValue(new Error("closed branch"));
  render(<MemoryRouter><SceneFocusEditor cid="saltmarch" sid="mara" /></MemoryRouter>);
  await screen.findByText("Let Mara choose.");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Seraphine waits." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await screen.findByText(/closed branch/i);
  expect(screen.getByRole("textbox")).toHaveValue("Seraphine waits.");
  vi.mocked(api.getSceneFocus).mockResolvedValue({ text: "", status: "unreadable" });
  fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await screen.findByText(/could not be read/i);
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
});

test("late scene save cannot display or refresh another scene", async () => {
  let finish!: (value: PromptField) => void;
  const saved = new Promise<PromptField>((resolve) => { finish = resolve; });
  vi.mocked(api.setSceneFocus).mockReturnValue(saved);
  const onSaved = vi.fn();
  const { rerender } = render(<MemoryRouter><SceneFocusEditor cid="saltmarch" sid="mara" onSaved={onSaved} /></MemoryRouter>);
  await screen.findByText("Let Mara choose.");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  vi.mocked(api.getSceneFocus).mockResolvedValue({ text: "Seraphine waits.", status: "ok" });
  rerender(<MemoryRouter><SceneFocusEditor cid="saltmarch" sid="seraphine" onSaved={onSaved} /></MemoryRouter>);
  await screen.findByText("Seraphine waits.");
  await act(async () => finish({ text: "Mara waits.", status: "ok" }));
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText("Mara waits.")).toBeNull();
  await waitFor(() => expect(screen.getByRole("button", { name: /edit/i })).toBeEnabled());
});
