import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(),
    setWorldModule: vi.fn(),
    listModules: vi.fn(),
    getWorldSheets: vi.fn(),
  },
}));
import { api } from "../api/client";
import WorldMechanics from "./WorldMechanics";
import { fixture } from "../testkit/fixture";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listModules).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "", version: "0.1", source: "builtin", valid: true },
  ]);
  vi.mocked(api.getWorld).mockResolvedValue(fixture({ meta: { id: "w", name: "Saltmarch" }, body: "", counts: {} }));
  vi.mocked(api.setWorldModule).mockResolvedValue({ ok: true });
  vi.mocked(api.getWorldSheets).mockResolvedValue({ coverage: {}, refs: [] });
});

test("shows two-state select defaulting to None and saves the choice", async () => {
  render(<WorldMechanics wid="w" />);
  const select = (await screen.findByLabelText<HTMLSelectElement>("Mechanics"));
  expect(select.value).toBe("");
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.setWorldModule).toHaveBeenCalledWith("w", "pool-basic"));
});

test("shows the default-module hint when the world has a module set", async () => {
  vi.mocked(api.getWorld).mockResolvedValue(
    fixture({ meta: { id: "w", name: "Saltmarch", module: "pool-basic" }, body: "", counts: {} }));
  render(<WorldMechanics wid="w" />);
  expect(await screen.findByText("Campaigns on this world default to Basic Pool.")).toBeInTheDocument();
});

test("the starting-sheets picker calls onPickMid on selection", async () => {
  const onPickMid = vi.fn();
  render(<WorldMechanics wid="w" worldMid="" onPickMid={onPickMid} />);
  const picker = (await screen.findByLabelText<HTMLSelectElement>("Starting sheets module"));
  fireEvent.change(picker, { target: { value: "pool-basic" } });
  expect(onPickMid).toHaveBeenCalledWith("pool-basic");
});

test("shows sheet coverage for the picked starting-sheets module", async () => {
  vi.mocked(api.getWorldSheets).mockResolvedValue({
    coverage: { characters: { total: 3, sheeted: 1, invalid: 0 } }, refs: [],
  });
  render(<WorldMechanics wid="w" worldMid="pool-basic" onPickMid={() => {}} />);
  expect(await screen.findByText("Characters 1/3")).toBeInTheDocument();
  await waitFor(() => expect(api.getWorldSheets).toHaveBeenCalledWith("w", "pool-basic"));
});

test("does not fetch or render coverage when no starting-sheets module is picked", async () => {
  render(<WorldMechanics wid="w" worldMid="" onPickMid={() => {}} />);
  await screen.findByLabelText("Starting sheets module");
  expect(api.getWorldSheets).not.toHaveBeenCalled();
});
