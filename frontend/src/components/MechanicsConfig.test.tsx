import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    getCampaignModule: vi.fn(),
    setCampaignModule: vi.fn(),
    listModules: vi.fn(),
    getCampaignSheets: vi.fn(),
  },
}));
import { api } from "../api/client";
import MechanicsConfig from "./MechanicsConfig";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listModules).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "", version: "0.1", source: "builtin", valid: true },
  ]);
  vi.mocked(api.getCampaignModule).mockResolvedValue({ setting: "", resolved: null, source: null });
  vi.mocked(api.setCampaignModule).mockResolvedValue({ ok: true });
  vi.mocked(api.getCampaignSheets).mockResolvedValue({ coverage: {}, refs: [] });
});

test("shows tri-state select and saves the choice", async () => {
  render(<MechanicsConfig cid="run" />);
  const select = (await screen.findByLabelText<HTMLSelectElement>("Mechanics"));
  expect(select.value).toBe("");                        // inherit
  expect(screen.getByText(/No mechanics/)).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.setCampaignModule).toHaveBeenCalledWith("run", "pool-basic"));
});

test("shows the resolved module and its source", async () => {
  vi.mocked(api.getCampaignModule).mockResolvedValue(
    { setting: "", resolved: "pool-basic", source: "world" });
  render(<MechanicsConfig cid="run" />);
  // "Basic Pool" also appears as a <select> option, so match the hint text
  // in full rather than a bare substring to avoid an ambiguous query.
  expect(await screen.findByText("Playing with Basic Pool (world default)")).toBeInTheDocument();
});

test("shows a stale-binding warning when the bound module is missing or invalid", async () => {
  vi.mocked(api.getCampaignModule).mockResolvedValue(
    { setting: "ghost", resolved: null, source: null });
  render(<MechanicsConfig cid="run" />);
  expect(await screen.findByText(
    'Bound module "ghost" is missing or invalid — resolving to no mechanics.'
  )).toBeInTheDocument();
});

test("shows an error hint and does not throw when save fails", async () => {
  vi.mocked(api.setCampaignModule).mockRejectedValue(new Error("boom"));
  render(<MechanicsConfig cid="run" />);
  const select = (await screen.findByLabelText<HTMLSelectElement>("Mechanics"));
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  expect(await screen.findByText(/boom/)).toBeInTheDocument();
});

test("shows sheet coverage below the resolved hint when a module is resolved", async () => {
  vi.mocked(api.getCampaignModule).mockResolvedValue(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  vi.mocked(api.getCampaignSheets).mockResolvedValue({
    coverage: { characters: { total: 3, sheeted: 1, invalid: 0 } }, refs: [],
  });
  render(<MechanicsConfig cid="run" />);
  expect(await screen.findByText("Characters 1/3")).toBeInTheDocument();
});

test("the module is one labelled field, its state the field's hint", async () => {
  // A "Mechanics" heading over a "Mechanics" label that ran into the select.
  render(<MechanicsConfig cid="run" />);
  const select = await screen.findByLabelText("Mechanics");
  const field = select.closest(".field") as HTMLElement;
  expect(field).not.toBeNull();
  expect(field.querySelector(".field-hint")).toHaveTextContent(/No mechanics/);
  expect(screen.queryByRole("heading", { name: "Mechanics" })).toBeNull();
});
