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

  expect(await screen.findByText(/stay "not reported" in every cost view/))
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

test("a row nobody has named cannot be sent at all", async () => {
  // Guarded twice on purpose: Save is blocked here, and `toTable` would still
  // refuse to file it under the catch-all key if it ever got through.
  render(<PricingEditor />);
  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.change(screen.getByLabelText(/Input rate for a new model/),
                   { target: { value: "9" } });
  fireEvent.change(screen.getByLabelText(/Output rate for a new model/),
                   { target: { value: "9" } });

  expect(screen.getByText("Save rates")).toBeDisabled();
  fireEvent.click(screen.getByText("Save rates"));
  expect(api.setPricing).not.toHaveBeenCalled();
});

test("removing a row and saving drops it", async () => {
  vi.mocked(api.setPricing).mockResolvedValue({ rates: {} });
  render(<PricingEditor />);
  fireEvent.click(await screen.findByLabelText("Remove local/glm"));
  fireEvent.click(screen.getByText("Save rates"));

  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({}));
  expect(await screen.findByText(/stay "not reported" in every cost view/))
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

test("an unparseable file refuses the form too, not just a failed request", async () => {
  // A 200 carrying `unreadable` is the same danger through a different door:
  // the file is there, and an empty form saved over it replaces real rates.
  vi.mocked(api.getPricing).mockResolvedValue(
    { ...TABLE, rates: {}, unreadable: true, detail: "line 1" });
  render(<PricingEditor />);

  expect(await screen.findByText(/Could not read the rate table/)).toBeInTheDocument();
  expect(screen.queryByText("Save rates")).toBeNull();
});

test("two rows claiming one model block the save that would drop one", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.click(screen.getByText("+ Add a model"));
  fireEvent.change(screen.getByLabelText(/Model id for row 1/),
                   { target: { value: "local/x" } });
  // Trailing space: `toTable` trims, so these collapse to one key on save.
  fireEvent.change(screen.getByLabelText(/Model id for row 2/),
                   { target: { value: "local/x " } });

  expect(screen.getAllByText(/Two rows claim this model/)).toHaveLength(2);
  expect(screen.getByText("Save rates")).toBeDisabled();
});

test("two unnamed rows are not duplicates of each other", async () => {
  // Neither is saved at all, so neither can displace anything.
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.click(screen.getByText("+ Add a model"));

  expect(screen.queryByText(/Two rows claim this model/)).toBeNull();
});

test("a failed read refuses to offer a form that could wipe the table", async () => {
  // The failure mode this replaced: a transient GET degraded to an editable
  // empty table, and one click of Save then sent `{}` and deleted every rate
  // the user had — rates they never loaded, saw or removed.
  vi.mocked(api.getPricing).mockRejectedValue(new Error("no"));
  render(<PricingEditor />);

  expect(await screen.findByText(/Could not read the rate table/)).toBeInTheDocument();
  expect(screen.queryByText("Save rates")).toBeNull();
});

test("a failed read can be retried", async () => {
  vi.mocked(api.getPricing).mockRejectedValueOnce(new Error("no"))
    .mockResolvedValue(TABLE);
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("Try again"));

  expect(await screen.findByDisplayValue("local/glm")).toBeInTheDocument();
});

test("a filled-in row with no model id blocks the save that would discard it", async () => {
  // `toTable` drops it (an unnamed row must not become the catch-all), and it
  // did so under a "Rates saved" that threw away everything typed.
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.change(screen.getByLabelText(/Input rate for a new model/),
                   { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText(/Output rate for a new model/),
                   { target: { value: "2" } });

  expect(screen.getByText(/This row needs a model id/)).toBeInTheDocument();
  expect(screen.getByText("Save rates")).toBeDisabled();
});

test("an untouched new row is not nagged about its missing id", async () => {
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));

  expect(screen.queryByText(/This row needs a model id/)).toBeNull();
});

test("a row missing a base rate says it will not be saved, and is not", async () => {
  // Half an entry prices half a call and values the other half at nothing.
  vi.mocked(api.getPricing).mockResolvedValue({ ...TABLE, rates: {} });
  vi.mocked(api.setPricing).mockResolvedValue({ rates: {} });
  render(<PricingEditor />);

  fireEvent.click(await screen.findByText("+ Add a model"));
  fireEvent.change(screen.getByLabelText(/Model id for row 1/),
                   { target: { value: "local/x" } });
  fireEvent.change(screen.getByLabelText(/Input rate for local\/x/),
                   { target: { value: "1" } });

  expect(screen.getByText(/This row will not be saved/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Save rates"));
  await waitFor(() => expect(api.setPricing).toHaveBeenCalledWith({}));
});
