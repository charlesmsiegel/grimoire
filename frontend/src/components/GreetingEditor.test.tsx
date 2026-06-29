import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { GreetingEditor } from "./GreetingEditor";

vi.mock("../api/client", () => ({
  api: {
    listGreetings: vi.fn(), listCharacters: vi.fn(), listTags: vi.fn(), readGreeting: vi.fn(),
    createGreeting: vi.fn(), updateGreeting: vi.fn(), deleteGreeting: vi.fn(),
    setEdges: vi.fn(), importGreetings: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listGreetings as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  (api.listTags as any).mockResolvedValue({ vip: "VIP" });
  (api.createGreeting as any).mockResolvedValue({ id: "open" });
  (api.setEdges as any).mockResolvedValue({ ok: true });
  (api.importGreetings as any).mockResolvedValue({ greetings: ["g1"] });
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] },
  });
});

test("creating a greeting posts the draft then sets edges", async () => {
  render(<GreetingEditor wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Open" } });
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "default" } });
  fireEvent.change(screen.getByLabelText(/predecessor join/i), { target: { value: "any" } });
  fireEvent.click(screen.getByRole("button", { name: "VIP" }));
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));
  await waitFor(() =>
    expect(api.createGreeting).toHaveBeenCalledWith("w", expect.objectContaining({
      name: "Open", character: "seraphine", version: "default",
      predecessor_join: "any", requires_tags: ["vip"],
    })),
  );
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith("w", "open", { leads_to: [], excludes: [] }));
});

test("version options follow the selected character", async () => {
  render(<GreetingEditor wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  // the version select now offers 'default'
  const versionSelect = screen.getByLabelText("Version") as HTMLSelectElement;
  expect([...versionSelect.options].map((o) => o.value)).toContain("default");
});

test("import-from-character posts the selected character + version", async () => {
  render(<GreetingEditor wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Character"), { target: { value: "seraphine" } });
  fireEvent.change(screen.getByLabelText("Version"), { target: { value: "default" } });
  fireEvent.click(screen.getByRole("button", { name: /import greetings from this/i }));
  await waitFor(() =>
    expect(api.importGreetings).toHaveBeenCalledWith("w", { character: "seraphine", version: "default" }),
  );
});

test("editing a greeting toggles present characters and saves them", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
    { id: "rowan", name: "Rowan", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] },
  });
  (api.updateGreeting as any).mockResolvedValue({ ok: true });
  const { container } = render(<GreetingEditor wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith("w", "open"));
  const present = screen.getByText("Present characters").closest(".field") as HTMLElement;
  fireEvent.click(within(present).getByRole("button", { name: "Rowan" }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.updateGreeting).toHaveBeenCalledWith("w", "open",
      expect.objectContaining({ present: ["seraphine", "rowan"] })),
  );
});

test("editing a greeting sets leads_to edges", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", requires_tags: [], predecessor_join: "all" },
    { id: "reckoning", name: "Reckoning", character: "seraphine", version: "default", requires_tags: [], predecessor_join: "all" },
  ]);
  const { container } = render(<GreetingEditor wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Open"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith("w", "open"));
  const leadsTo = screen.getByText("Leads to").closest(".field") as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Reckoning" }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() =>
    expect(api.setEdges).toHaveBeenCalledWith("w", "open", { leads_to: ["reckoning"], excludes: [] }),
  );
});
