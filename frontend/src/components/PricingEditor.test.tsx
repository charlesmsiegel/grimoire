import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PricingEditor from "./PricingEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { getPricing: vi.fn(), setPricing: vi.fn() } };
});
import { ApiError, api } from "../api/client";

const TABLE = {
  rates: { "local/glm": { prompt_usd_per_1k: 0.5, completion_usd_per_1k: 1.5 } },
  fields: ["prompt_usd_per_1k", "completion_usd_per_1k",
           "cache_read_usd_per_1k", "cache_write_usd_per_1k"],
  default_key: "", max_entries: 500,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPricing).mockResolvedValue(TABLE);
  vi.mocked(api.setPricing).mockImplementation(
    async (rates) => ({ rates }));
});

test("an existing rate is loaded into the form", async () => {
  render(<PricingEditor />);

  expect(await screen.findByDisplayValue("local/glm")).toBeInTheDocument();
  expect(screen.getByLabelText(/Input rate for local\/glm/)).toHaveValue(0.5);
  expect(screen.getByLabelText(/Output rate for local\/glm/)).toHaveValue(1.5);
});

test("each box shows the same rate the way a price sheet quotes it", async () => {
  // The line that stops a rate being typed a thousandfold off.
  render(<PricingEditor />);

  expect(await screen.findByText("$500/M")).toBeInTheDocument();
  expect(screen.getByText("$1,500/M")).toBeInTheDocument();
});

test("an empty table says what that costs rather than showing nothing", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  expect(await screen.findByText(/stay "unpriced" in every cost view/))
    .toBeInTheDocument();
});

test("a new row is a named model, not a second catch-all", async () => {
  // Both start with an empty id; inferring the catch-all from emptiness would
  // make every added row a rate claiming to price the whole library.
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));
  expect(screen.getByLabelText(/Model id for row 1/)).toBeInTheDocument();
  expect(screen.queryByText("Every other model")).toBeNull();
});

test("a catch-all can be added once, and only once", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a catch-all rate"));
  expect(screen.getByText("Every other model")).toBeInTheDocument();
  expect(screen.queryByText("+ Add a catch-all rate")).toBeNull();
});

test("saving sends the whole table, as typed", async () => {
  render(<PricingEditor />);
  fireEvent.change(await screen.findByLabelText(/Input rate for local\/glm/),
                   { target: { value: "0.25" } });
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({
    "local/glm": { prompt_usd_per_1k: 0.25, completion_usd_per_1k: 1.5 },
  }));
  expect(await screen.findByText("Rates saved.")).toBeInTheDocument();
});

test("an empty box is left out rather than sent as a zero", async () => {
  // A cache rate of zero would price cached tokens at nothing; an absent one
  // prices them at the input rate, which is what an untouched box means.
  render(<PricingEditor />);
  await screen.findByDisplayValue("local/glm");
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({
    "local/glm": { prompt_usd_per_1k: 0.5, completion_usd_per_1k: 1.5 },
  }));
});

test("a row nobody has named is not sent under the catch-all key", async () => {
  render(<PricingEditor />);
  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.change(screen.getByLabelText(/Input rate for a new model/),
                   { target: { value: "9" } });
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({
    "local/glm": { prompt_usd_per_1k: 0.5, completion_usd_per_1k: 1.5 },
  }));
});

test("removing a row and saving drops it", async () => {
  vi.mocked(api.setPricing).mockResolvedValue({ rates: {} });
  render(<PricingEditor />);
  fireEvent.click(await screen.findByLabelText("Remove local/glm"));
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({}));
  expect(await screen.findByText(/stay "unpriced" in every cost view/))
    .toBeInTheDocument();
});

test("the form is re-seeded from what the server kept, not from what was typed", async () => {
  // An entry the server dropped has to leave the form too, or the next save
  // sends it back and the two disagree forever.
  vi.mocked(api.setPricing).mockResolvedValue({ rates: {} });
  render(<PricingEditor />);
  await screen.findByDisplayValue("local/glm");
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(screen.queryByDisplayValue("local/glm")).toBeNull());
});

test("a failed save is reported without losing what was typed", async () => {
  // A real `ApiError`, which is what `request` throws: the panel reads the
  // server's own `detail` off one and falls back to the message otherwise.
  vi.mocked(api.setPricing).mockRejectedValue(new ApiError(500, "store is read-only"));
  render(<PricingEditor />);
  fireEvent.change(await screen.findByLabelText(/Input rate for local\/glm/),
                   { target: { value: "0.25" } });
  fireEvent.click(screen.getByText("Save rates"));

  expect(await screen.findByText("store is read-only")).toBeInTheDocument();
  expect(screen.getByLabelText(/Input rate for local\/glm/)).toHaveValue(0.25);
});

test("a failed read degrades to an empty table rather than a stuck spinner", async () => {
  vi.mocked(api.getPricing).mockRejectedValue(new Error("no"));
  render(<PricingEditor />);

  expect(await screen.findByText(/stay "unpriced" in every cost view/))
    .toBeInTheDocument();
});
