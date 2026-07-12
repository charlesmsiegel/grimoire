import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    getCampaignModule: vi.fn(),
    setCampaignModule: vi.fn(),
    listModules: vi.fn(),
  },
}));
import { api } from "../api/client";
import MechanicsConfig from "./MechanicsConfig";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "", version: "0.1", source: "builtin", valid: true },
  ]);
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  (api.setCampaignModule as any).mockResolvedValue({ ok: true });
});

test("shows tri-state select and saves the choice", async () => {
  render(<MechanicsConfig cid="run" />);
  const select = (await screen.findByLabelText("Mechanics")) as HTMLSelectElement;
  expect(select.value).toBe("");                        // inherit
  expect(screen.getByText(/No mechanics/)).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.setCampaignModule).toHaveBeenCalledWith("run", "pool-basic"));
});

test("shows the resolved module and its source", async () => {
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "", resolved: "pool-basic", source: "world" });
  render(<MechanicsConfig cid="run" />);
  // "Basic Pool" also appears as a <select> option, so match the hint text
  // in full rather than a bare substring to avoid an ambiguous query.
  expect(await screen.findByText("Playing with Basic Pool (world default)")).toBeInTheDocument();
});

test("shows a stale-binding warning when the bound module is missing or invalid", async () => {
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "ghost", resolved: null, source: null });
  render(<MechanicsConfig cid="run" />);
  expect(await screen.findByText(
    'Bound module "ghost" is missing or invalid — resolving to no mechanics.'
  )).toBeInTheDocument();
});

test("shows an error hint and does not throw when save fails", async () => {
  (api.setCampaignModule as any).mockRejectedValue(new Error("boom"));
  render(<MechanicsConfig cid="run" />);
  const select = (await screen.findByLabelText("Mechanics")) as HTMLSelectElement;
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  expect(await screen.findByText(/boom/)).toBeInTheDocument();
});
