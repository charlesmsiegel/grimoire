import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PromptLayoutEditor } from "./PromptLayoutEditor";

vi.mock("../api/client", () => ({
  api: { getPromptLayout: vi.fn(), putPromptLayout: vi.fn() },
}));
import { api } from "../api/client";

const layout = {
  enabled: false,
  sections: [
    { id: "global_system_prompt", label: "Global system prompt",
      default_label: "Global system prompt", tier: "lock-in", enabled: true },
    { id: "world_info", label: "World info", default_label: "World info",
      tier: "spotlight", enabled: true },
    { id: "weather", label: "Weather", default_label: "Weather",
      tier: "spotlight", enabled: true },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getPromptLayout as any).mockResolvedValue(structuredClone(layout));
  (api.putPromptLayout as any).mockImplementation((sections: any[]) =>
    Promise.resolve({
      enabled: false,
      sections: sections.map((s) => ({
        ...s, label: s.label || layout.sections.find((c) => c.id === s.id)!.default_label,
        default_label: layout.sections.find((c) => c.id === s.id)!.default_label,
        tier: layout.sections.find((c) => c.id === s.id)!.tier,
      })),
    }));
});

const rowNames = () =>
  screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id"));

test("lists the sections in server order", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  expect(rowNames()).toEqual(["global_system_prompt", "world_info", "weather"]);
});

test("moving a row down swaps it with the next and saves that order", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");

  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(rowNames()).toEqual(["world_info", "global_system_prompt", "weather"]);

  fireEvent.click(screen.getByRole("button", { name: /^save/i }));
  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalled());
  expect((api.putPromptLayout as any).mock.calls[0][0].map((s: any) => s.id))
    .toEqual(["world_info", "global_system_prompt", "weather"]);
});

test("moving a row up swaps it with the previous", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ up$/i })[2]);
  expect(rowNames()).toEqual(["global_system_prompt", "weather", "world_info"]);
});

test("the first row cannot move up and the last cannot move down", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  expect(screen.getAllByRole("button", { name: /^move .+ up$/i })[0]).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /^move .+ down$/i })[2]).toBeDisabled();
});

test("unchecking a section saves it disabled", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("Weather");

  fireEvent.click(screen.getByRole("checkbox", { name: /include weather/i }));
  fireEvent.click(screen.getByRole("button", { name: /^save/i }));

  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalled());
  const sent = (api.putPromptLayout as any).mock.calls[0][0];
  expect(sent.find((s: any) => s.id === "weather").enabled).toBe(false);
  expect(sent.find((s: any) => s.id === "world_info").enabled).toBe(true);
});

test("a typed label is sent, and a blank one falls back to the default", async () => {
  render(<PromptLayoutEditor />);
  const input = await screen.findByDisplayValue("World info");

  fireEvent.change(input, { target: { value: "Lore" } });
  fireEvent.change(screen.getByDisplayValue("Weather"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /^save/i }));

  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalled());
  const sent = (api.putPromptLayout as any).mock.calls[0][0];
  expect(sent.find((s: any) => s.id === "world_info").label).toBe("Lore");
  expect(sent.find((s: any) => s.id === "weather").label).toBe("");
});

test("reset sends an empty list", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  fireEvent.click(screen.getByRole("button", { name: /reset/i }));
  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalledWith([]));
});

test("save is inert until something changes", async () => {
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  expect(screen.getByRole("button", { name: /^save/i })).toBeDisabled();
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(screen.getByRole("button", { name: /^save/i })).toBeEnabled();
});

test("it says a label renames the inspector row and not the prompt", async () => {
  /** The honest half of the feature: each template emits its own heading, so a
   *  label edit never reaches the model. Asserted so a refactor cannot quietly
   *  drop the sentence and leave the control looking like it edits the prompt. */
  render(<PromptLayoutEditor />);
  await screen.findByDisplayValue("World info");
  expect(screen.getByText(/renames the row in the scene inspector/i)).toBeInTheDocument();
  expect(screen.getByText(/not the heading the model reads/i)).toBeInTheDocument();
});

test("a failed load leaves an explanation rather than an empty panel", async () => {
  (api.getPromptLayout as any).mockRejectedValue(new Error("nope"));
  render(<PromptLayoutEditor />);
  expect(await screen.findByText(/could not load the prompt layout/i)).toBeInTheDocument();
});
