import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DefaultPromptEditor } from "./DefaultPromptEditor";
import { api } from "../api/client";
import type { PromptField } from "../api/types";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: { getWorldDefaultPrompt: vi.fn(), setWorldDefaultPrompt: vi.fn(),
    getCampaignDefaultPrompt: vi.fn(), setCampaignDefaultPrompt: vi.fn(), getPromptLayout: vi.fn() },
}));

function pendingField() {
  let resolve!: (value: PromptField) => void;
  const promise = new Promise<PromptField>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: false, sections: [] });
  vi.mocked(api.getWorldDefaultPrompt).mockResolvedValue({ text: "The river freezes.", status: "ok" });
  vi.mocked(api.setWorldDefaultPrompt).mockResolvedValue({ text: "The harbor stays open.", status: "ok" });
  vi.mocked(api.getCampaignDefaultPrompt).mockResolvedValue({ text: "The harbor stays open.", status: "ok",
    world: { id: "realm", text: "The river freezes.", status: "ok" } });
  vi.mocked(api.setCampaignDefaultPrompt).mockResolvedValue({ text: "Mara waits.", status: "ok",
    world: { id: "realm", text: "The river freezes.", status: "ok" } });
});

const world = { kind: "world", id: "realm" } as const;
const campaign = { kind: "campaign", id: "saltmarch" } as const;

test("defaults explicitly edit, cancel and save multiline text using the canonical response", async () => {
  render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText("The river freezes.");
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("textbox", { name: /world default prompt/i });
  fireEvent.change(input, { target: { value: "The harbor\nstays open." } });
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(api.setWorldDefaultPrompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "The harbor\nstays open." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await screen.findByText("The harbor stays open.");
  expect(api.setWorldDefaultPrompt).toHaveBeenCalledWith("realm", "The harbor\nstays open.");
});

test("campaign saves only its own field and shows live inherited preview with a world-overview link", async () => {
  render(<MemoryRouter><DefaultPromptEditor scope={campaign} /></MemoryRouter>);
  await screen.findByText("The harbor stays open.");
  expect(screen.getByText("The river freezes.")).toBeInTheDocument();
  expect(screen.getByText(/inherited live/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /world overview/i })).toHaveAttribute("href", "/worlds/realm");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCampaignDefaultPrompt).toHaveBeenCalledWith("saltmarch", "Mara waits."));
  expect(api.setWorldDefaultPrompt).not.toHaveBeenCalled();
});

test("failed reads are not editable empty fields; failed saves retain the draft", async () => {
  vi.mocked(api.getWorldDefaultPrompt).mockRejectedValueOnce(new Error("read refused"));
  render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText(/read refused/i);
  expect(screen.getByRole("button", { name: /edit/i })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /refresh|retry/i }));
  await screen.findByText("The river freezes.");
  vi.mocked(api.setWorldDefaultPrompt).mockRejectedValueOnce(new Error("save refused"));
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await screen.findByText(/save refused/i);
  expect(screen.getByRole("textbox")).toHaveValue("Mara waits.");
});

test("unreadable status disables editing rather than pretending the field is unset", async () => {
  vi.mocked(api.getWorldDefaultPrompt).mockResolvedValue({ text: "", status: "unreadable" });
  render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText(/could not be read/i);
  expect(screen.getByRole("button", { name: /edit/i })).toBeDisabled();
  expect(screen.queryByText(/no world default prompt/i)).toBeNull();
});

test("late read and save cannot cross world scope", async () => {
  const read = pendingField();
  vi.mocked(api.getWorldDefaultPrompt).mockReturnValueOnce(read.promise);
  vi.mocked(api.getWorldDefaultPrompt).mockResolvedValue({ text: "Seraphine waits.", status: "ok" });
  const { rerender } = render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  rerender(<MemoryRouter><DefaultPromptEditor scope={{ kind: "world", id: "realm-2" }} /></MemoryRouter>);
  await screen.findByText("Seraphine waits.");
  await act(async () => read.resolve({ text: "Mara waits.", status: "ok" }));
  expect(screen.queryByText("Mara waits.")).toBeNull();
  const save = pendingField();
  vi.mocked(api.setWorldDefaultPrompt).mockReturnValueOnce(save.promise);
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara chooses." } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  rerender(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText("Seraphine waits.");
  await act(async () => save.resolve({ text: "Mara chooses.", status: "ok" }));
  expect(screen.queryByText("Mara chooses.")).toBeNull();
  expect(api.setWorldDefaultPrompt).toHaveBeenCalledWith("realm-2", "Mara chooses.");
});

test("same-scope refresh cannot replace an active edit", async () => {
  render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText("The river freezes.");
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  vi.mocked(api.getWorldDefaultPrompt).mockResolvedValue({ text: "Seraphine waits.", status: "ok" });
  fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await waitFor(() => expect(api.getWorldDefaultPrompt).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("textbox")).toHaveValue("Mara waits.");
});

test.each(["disabled", "off", "older", "failed"])("layout state %s is honest about inclusion", async (state) => {
  if (state === "failed") vi.mocked(api.getPromptLayout).mockRejectedValue(new Error("layout refused"));
  else vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: state !== "off", sections: state === "older" ? [] : [{
    id: "world_default_prompt", label: "", default_label: "World default prompt", tier: "lock-in", enabled: false,
  }] });
  render(<MemoryRouter><DefaultPromptEditor scope={world} /></MemoryRouter>);
  await screen.findByText("The river freezes.");
  if (state === "disabled") expect(await screen.findByText(/disabled.*prompt layout/i)).toBeInTheDocument();
  if (state === "failed") expect(await screen.findByText(/inclusion.*unknown/i)).toBeInTheDocument();
  if (state === "off" || state === "older") expect(screen.queryByText(/disabled.*prompt layout/i)).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
});
