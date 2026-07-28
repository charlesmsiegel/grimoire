import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ClimateEditor } from "./ClimateEditor";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listClimates: vi.fn(),
    readClimate: vi.fn(),
    saveClimate: vi.fn(),
    deleteClimate: vi.fn(),
  } };
});

const CLIMATE = {
  id: "temperate-interior", name: "Temperate Interior", persistence: 0.55,
  seasons: [{
    name: "winter", from: 0.92, to: 0.21,
    temperature: [{ name: "freezing", weight: 2 }, { name: "cold", weight: 6 }],
    conditions: [{ name: "clear", weight: 2 },
                 { name: "snow", weight: 2, requires_temp: ["freezing"] }],
    wind: [{ name: "calm", weight: 1 }],
  }],
};

beforeEach(() => {
  vi.mocked(api.listClimates).mockResolvedValue({ climates: [
    { id: "temperate-interior", name: "Temperate Interior", builtin: true, custom: false },
    { id: "saltmarch-fens", name: "Fens", builtin: false, custom: true },
  ] });
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: CLIMATE, builtin: true, custom: false });
  vi.mocked(api.saveClimate).mockResolvedValue({ climate: CLIMATE });
  vi.mocked(api.deleteClimate).mockResolvedValue({ ok: true, reverted_to_preset: true });
});

test("lists every climate in the rail", async () => {
  render(<ClimateEditor />);
  expect(await screen.findByText("Temperate Interior")).toBeInTheDocument();
  expect(screen.getByText("Fens")).toBeInTheDocument();
});

test("clicking a row shows the read-only view, not the form", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByRole("heading", { name: "Temperate Interior" })).toBeInTheDocument();
  expect(screen.getByText(/freezing ×2/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
});

test("Edit reveals the form", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByLabelText("Name")).toHaveValue("Temperate Interior");
  expect(screen.getByLabelText("Persistence")).toHaveValue(0.55);
});

test("+ New climate opens the form directly", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New climate" }));
  expect(await screen.findByLabelText("Id")).toBeInTheDocument();
});

test("a shipped preset says editing makes a copy", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByText(/shipped preset/)).toBeInTheDocument();
  expect(screen.getByText(/never changed/)).toBeInTheDocument();
});

test("a custom copy of a preset offers Revert, not Delete", async () => {
  // Both flags means the undo is a revert; only the two-flag response can
  // distinguish this from a standalone custom climate.
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: CLIMATE, builtin: true, custom: true });
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByRole("button", { name: "Revert to preset" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
});

test("a standalone custom climate offers Delete, not Revert", async () => {
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: { ...CLIMATE, id: "saltmarch-fens", name: "Fens" },
      builtin: false, custom: true });
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Fens"));
  expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Revert to preset" })).not.toBeInTheDocument();
});

test("saving an invalid climate surfaces the server's reason", async () => {
  // The resolver is lenient so bad data cannot take a turn down, which makes
  // this the only place the author ever hears about a mistake.
  vi.mocked(api.saveClimate).mockRejectedValue({ detail: "season 'winter' wind: weight must be finite" });
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/weight must be finite/)).toBeInTheDocument();
});

test("a constrained condition shows what it is limited to", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByText(/snow ×2 \(freezing only\)/)).toBeInTheDocument();
});

test("a whole-year season is described as such rather than 0% to 0%", async () => {
  vi.mocked(api.readClimate).mockResolvedValue({
    climate: { ...CLIMATE, seasons: [{ ...CLIMATE.seasons[0], name: "all year", from: 0, to: 0 }] },
    builtin: true, custom: false });
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByText(/all year — the whole year/)).toBeInTheDocument();
});

test("saving sends the route id even for a renamed climate", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.saveClimate).toHaveBeenCalledWith(
    "temperate-interior", expect.objectContaining({ id: "temperate-interior", name: "Mine" })));
});
