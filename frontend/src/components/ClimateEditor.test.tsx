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
    climateReferrers: vi.fn(),
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
  // Call history is not auto-cleared here, and one test asserts "not called".
  vi.clearAllMocks();
  vi.mocked(api.listClimates).mockResolvedValue({ climates: [
    { id: "temperate-interior", name: "Temperate Interior", builtin: true, custom: false },
    { id: "saltmarch-fens", name: "Fens", builtin: false, custom: true },
  ] });
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: CLIMATE, builtin: true, custom: false });
  vi.mocked(api.saveClimate).mockResolvedValue({ climate: CLIMATE });
  vi.mocked(api.deleteClimate).mockResolvedValue({ ok: true, reverted_to_preset: true });
  vi.mocked(api.climateReferrers).mockResolvedValue({ campaigns: [], locations: [] });
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
  expect(await screen.findByText(/snow ×2 · 50% \(freezing only\)/)).toBeInTheDocument();
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

test("clearing a temperature requirement omits the key rather than sending []", async () => {
  // The validator rejects an empty requires_temp and wants the key omitted, so
  // sending [] makes the constraint impossible to remove — Save always fails.
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Conditions requires 2"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.saveClimate).toHaveBeenCalled());
  const calls = vi.mocked(api.saveClimate).mock.calls;
  const sent = calls[calls.length - 1][1];
  expect("requires_temp" in sent.seasons[0].conditions[1]).toBe(false);
});

test("renaming a temperature rewrites the conditions that require it", async () => {
  // Otherwise the next save is rejected as a dangling requirement and the
  // author has to find every dependent condition by hand.
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Temperature name 1"), { target: { value: "bitter" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.saveClimate).toHaveBeenCalled());
  const calls = vi.mocked(api.saveClimate).mock.calls;
  const sent = calls[calls.length - 1][1];
  expect(sent.seasons[0].conditions[1].requires_temp).toEqual(["bitter"]);
});

test("a new climate cannot silently overwrite an existing custom id", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New climate" }));
  fireEvent.change(await screen.findByLabelText("Id"), { target: { value: "saltmarch-fens" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  expect(api.saveClimate).not.toHaveBeenCalled();
});

test("deleting a standalone climate discloses the campaigns defaulting to it", async () => {
  // A campaign default is the widest effect and the one a locations-only
  // warning never mentions: every untagged location there falls back.
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: { ...CLIMATE, id: "saltmarch-fens", name: "Fens" }, builtin: false, custom: true });
  vi.mocked(api.climateReferrers).mockResolvedValue({
    campaigns: [{ id: "saltmarch-chronicle", name: "Saltmarch Chronicle" }],
    locations: [{ campaign: "saltmarch-chronicle", id: "docks", name: "Saltmarch Docks" }] });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Fens"));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  const message = confirm.mock.calls[0][0] as string;
  expect(message).toContain("Saltmarch Chronicle");
  expect(message).toContain("Saltmarch Docks");
  expect(api.deleteClimate).not.toHaveBeenCalled();  // declined
  confirm.mockRestore();
});

test("a failed referrer lookup blocks deletion rather than claiming no impact", async () => {
  // Treating a failed lookup as an empty one would say "Nothing is using it"
  // and delete anyway — an unknown impact presented as no impact.
  vi.mocked(api.readClimate).mockResolvedValue(
    { climate: { ...CLIMATE, id: "saltmarch-fens", name: "Fens" }, builtin: false, custom: true });
  vi.mocked(api.climateReferrers).mockRejectedValue(new Error("network"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Fens"));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/Could not check what is using this climate/)).toBeInTheDocument();
  expect(confirm).not.toHaveBeenCalled();
  expect(api.deleteClimate).not.toHaveBeenCalled();
  confirm.mockRestore();
});

test("removing a temperature drops it from the conditions that required it", async () => {
  // Left behind, the reference is a dangling requirement the backend rejects,
  // so remove could never complete without hand-editing every dependant.
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  // The first Temperature row is `freezing`, which `snow` requires.
  fireEvent.click(screen.getAllByRole("button", { name: "✕" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.saveClimate).toHaveBeenCalled());
  const calls = vi.mocked(api.saveClimate).mock.calls;
  const sent = calls[calls.length - 1][1];
  expect(sent.seasons[0].temperature.map((x) => x.name)).toEqual(["cold"]);
  // `snow` required only `freezing`, so it becomes unconstrained rather than
  // carrying an empty array the validator rejects.
  expect("requires_temp" in sent.seasons[0].conditions[1]).toBe(false);
});

test("the read-only view shows what the weights actually mean", async () => {
  // ×2 and ×6 alone do not tell an author they configured 25% and 75%.
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  expect(await screen.findByText(/freezing ×2 · 25%/)).toBeInTheDocument();
  expect(screen.getByText(/cold ×6 · 75%/)).toBeInTheDocument();
});

test("per-band odds are shown when a constraint changes the eligible total", async () => {
  render(<ClimateEditor />);
  fireEvent.click(await screen.findByText("Temperate Interior"));
  // `snow` requires freezing, so cold can only draw `clear`.
  expect(await screen.findByText(/cold — clear 100%/)).toBeInTheDocument();
  expect(screen.getByText(/freezing — clear 50%, snow 50%/)).toBeInTheDocument();
});
