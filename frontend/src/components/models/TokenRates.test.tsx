import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "../../api/client";
import { TokenRates } from "./TokenRates";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { getPricing: vi.fn(), setPricing: vi.fn() } };
});

const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };
const TABLE = { rates: { "vendor/m": BOTH, "": BOTH } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPricing).mockResolvedValue(TABLE as any);
  vi.mocked(api.setPricing).mockImplementation(async (rates) => ({ rates }));
});

const block = () => within(screen.getByRole("region", { name: "Token rates" }));

test("the table is read-only until Edit", async () => {
  render(<TokenRates onSaved={() => {}} />);
  expect(await block().findByRole("cell", { name: "vendor/m" })).toBeInTheDocument();
  expect(block().getByRole("cell", { name: "Every other model" })).toBeInTheDocument();
  expect(block().queryByRole("spinbutton")).toBeNull();
  fireEvent.click(block().getByRole("button", { name: "Edit rates" }));
  expect(await block().findByRole("button", { name: "Save rates" })).toBeInTheDocument();
});

test("the moved explanations come with it", async () => {
  render(<TokenRates onSaved={() => {}} />);
  await block().findByRole("cell", { name: "vendor/m" });
  expect(block().getByText(/is not the same as setting them\s+to zero/)).toBeInTheDocument();
  expect(block().getByText(/never added to what a provider actually charged/)).toBeInTheDocument();
});

test("Set rate opens the editor on a new row for that model, caret in Input", async () => {
  render(<TokenRates addModel="vendor/new" onSaved={() => {}} />);
  const id = await block().findByDisplayValue("vendor/new");
  expect(id).toBeInTheDocument();
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/new" }))
    .toHaveFocus());
});

test("Set rate on a model already in the table edits that entry instead", async () => {
  render(<TokenRates addModel="vendor/m" onSaved={() => {}} />);
  await block().findByRole("button", { name: "Save rates" });
  expect(block().getAllByDisplayValue("vendor/m")).toHaveLength(1);
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/m" }))
    .toHaveFocus());
});

test("a second Set rate while the editor is open adds that model too", async () => {
  const { rerender } = render(<TokenRates addModel="vendor/a" onSaved={() => {}} />);
  await block().findByDisplayValue("vendor/a");
  rerender(<TokenRates addModel="vendor/b" onSaved={() => {}} />);
  expect(await block().findByDisplayValue("vendor/b")).toBeInTheDocument();
  await waitFor(() => expect(block().getByRole("spinbutton", { name: "Input rate for vendor/b" }))
    .toHaveFocus());
});

test("a saved table goes back to reading, and says so to the page", async () => {
  const onSaved = vi.fn();
  render(<TokenRates onSaved={onSaved} />);
  fireEvent.click(await block().findByRole("button", { name: "Edit rates" }));
  fireEvent.click(await block().findByRole("button", { name: "Save rates" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(await block().findByRole("button", { name: "Edit rates" })).toBeInTheDocument();
});

test("a table that could not be read offers nothing to edit", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ rates: {}, unreadable: true } as any);
  render(<TokenRates onSaved={() => {}} />);
  expect(await block().findByText(/Could not read the rate table/)).toBeInTheDocument();
  expect(block().queryByRole("button", { name: "Edit rates" })).toBeNull();
});
