import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api, type CapabilityNeed, type InferenceProvider } from "../../api/client";
import { ProviderModelPicker } from "./ProviderModelPicker";
import { forgetModelTests } from "./TestCallDialog";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: {
    readConnectionCapabilities: vi.fn(), previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});

const PROVIDERS = [
  { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter" as const,
    preset: "openrouter", usable: true },
  { id: "realm", name: "Realm Local", kind: "openai_compatible" as const,
    preset: "custom", usable: true },
];

const PRESET = {
  id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "", url_locked: true,
  billing: "metered", reports_price: true, always: [], possible: [], never: [],
};

function row(id: string, reason = "the catalog says so") {
  return { id, name: id.toUpperCase(), context: null, prompt: null, completion: null,
           reason, capabilities: {} };
}

/** One need's answer: the rows in each group, and the hidden ids. */
function answer(need: CapabilityNeed, fits: string[], unverified: string[], hidden: string[],
                reason: string | null = null) {
  return {
    provider_preset: PRESET, need, reason,
    groups: { fits: fits.map((id) => row(id)),
              unverified: unverified.map((id) => row(id, `${id} is not known to ${need}`)) },
    hidden: hidden.map((id) => ({ id, reason: `${id} cannot ${need}` })),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  (api.previewModelTest as any).mockResolvedValue({
    provider: "Saltmarch Router", provider_id: "saltmarch", model: "b",
    sends: [], estimated_cost_usd: null });
});

/** The picker, controlled the way a role form holds it. */
function Harness({ needs, start = { provider: "saltmarch", model: "" }, spy = vi.fn() }:
  { needs: CapabilityNeed[]; start?: { provider: string; model: string };
    spy?: (v: { provider: string; model: string }) => void }) {
  const [value, setValue] = useState(start);
  return <ProviderModelPicker needs={needs} providers={PROVIDERS} value={value}
                              onChange={(v) => { spy(v); setValue(v); }} />;
}

test("ProviderModelPicker groups fits and unverified across several needs", async () => {
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed) => Promise.resolve(need === "generate"
      ? answer("generate", ["a", "b", "c"], ["d"], ["e"])
      : answer("vision", ["a"], ["b", "d"], ["c"])));

  render(<Harness needs={["generate", "vision"]} />);

  const fits = await screen.findByRole("group", { name: "Fits" });
  const unverified = screen.getByRole("group", { name: "Unverified" });
  // Once per need, and nothing else asked.
  expect(api.readConnectionCapabilities).toHaveBeenCalledTimes(2);
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "generate");
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "vision");
  // Fits only where every need fits; hidden where any need is hidden.
  expect(within(fits).getAllByRole("radio").map((r) => r.getAttribute("value")))
    .toEqual(["a"]);
  expect(within(unverified).getAllByRole("radio").map((r) => r.getAttribute("value")))
    .toEqual(["b", "d"]);
  expect(screen.queryByRole("radio", { name: "C" })).toBeNull();
  expect(screen.queryByRole("radio", { name: "E" })).toBeNull();
  // Each unverified row says why it is only unverified, and offers a test.
  expect(within(unverified).getByText("b is not known to vision")).toBeInTheDocument();
  expect(within(unverified).getAllByRole("button", { name: /^Test/ })).toHaveLength(2);
  expect(within(fits).queryByRole("button", { name: /^Test/ })).toBeNull();
});

test("picking a row reports the provider and the model", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", ["a"], [], []));
  const spy = vi.fn();
  render(<Harness needs={["generate"]} spy={spy} />);

  fireEvent.click(await screen.findByRole("radio", { name: "A" }));
  expect(spy).toHaveBeenLastCalledWith({ provider: "saltmarch", model: "a" });
  expect(screen.getByRole("radio", { name: "A" })).toBeChecked();
});

test("changing the provider clears the model and asks the new one", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", ["a"], [], []));
  const spy = vi.fn();
  render(<Harness needs={["generate"]} spy={spy}
                  start={{ provider: "saltmarch", model: "a" }} />);
  await screen.findByRole("radio", { name: "A" });

  fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "realm" } });
  expect(spy).toHaveBeenLastCalledWith({ provider: "realm", model: "" });
  await screen.findByRole("radio", { name: "A" });
  expect(api.readConnectionCapabilities).toHaveBeenLastCalledWith("realm", "generate");
});

test("shows the provider-wide reason", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("embed", [], [], [], "z.ai serves no embeddings"));

  render(<Harness needs={["embed"]} />);

  expect(await screen.findByText("z.ai serves no embeddings")).toBeInTheDocument();
  // The reason replaces the list: no groups, no rows.
  expect(screen.queryByRole("group", { name: "Fits" })).toBeNull();
  expect(screen.queryByRole("radio")).toBeNull();
});

test("accepts a typed model id", async () => {
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed, model?: string) => Promise.resolve(model
      ? answer(need, [], [model], [])
      : answer(need, ["a"], [], [])));
  const spy = vi.fn();
  render(<Harness needs={["generate"]} spy={spy} />);
  await screen.findByRole("radio", { name: "A" });

  fireEvent.change(screen.getByLabelText("Model id"), { target: { value: " vendor/new " } });
  fireEvent.click(screen.getByRole("button", { name: "Use this id" }));

  expect(spy).toHaveBeenLastCalledWith({ provider: "saltmarch", model: "vendor/new" });
  // An id the catalog does not list is judged on its own, and lands in a group.
  const typed = await screen.findByRole("group", { name: "Typed id" });
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith(
    "saltmarch", "generate", "vendor/new");
  expect(typed).toHaveTextContent("vendor/new");
  expect(typed).toHaveTextContent("Unverified");
  expect(within(typed).getByRole("button", { name: /^Test/ })).toBeInTheDocument();
});

test("Enter in the typed-id box uses the id, and goes no further", async () => {
  // The box's Enter is "Use this id", as the old combobox's was. It stops
  // there: a picker inside the reroll popover sits in an Enter-commits
  // container, and a typed id is not a reroll.
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed, model?: string) => Promise.resolve(model
      ? answer(need, [], [model], [])
      : answer(need, ["a"], [], [])));
  const spy = vi.fn();
  const outer = vi.fn();
  render(
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div onKeyDown={(e) => outer(e.key)}><Harness needs={["generate"]} spy={spy} /></div>);
  await screen.findByRole("radio", { name: "A" });
  const box = screen.getByLabelText("Model id");

  // Nothing typed: nothing to use, and still nothing past the box.
  fireEvent.keyDown(box, { key: "Enter" });
  expect(spy).not.toHaveBeenCalled();
  fireEvent.change(box, { target: { value: " vendor/new " } });
  const enter = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
  act(() => { box.dispatchEvent(enter); });

  expect(spy).toHaveBeenLastCalledWith({ provider: "saltmarch", model: "vendor/new" });
  expect(box).toHaveValue("");
  // Spoken for, so neither a form around it nor the shortcut registry acts too.
  expect(enter.defaultPrevented).toBe(true);
  expect(outer).not.toHaveBeenCalled();
  expect(await screen.findByRole("group", { name: "Typed id" })).toBeInTheDocument();
});

test("Test… opens the test dialog for that row", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", [], ["b"], []));
  render(<Harness needs={["generate"]} />);

  fireEvent.click(await screen.findByRole("button", { name: "Test B" }));

  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  // On the body, not inside the picker: a picker in a popover sits in that
  // popover's stacking context, which would paint the "modal" under the
  // page's sticky chrome.
  expect(dialog.parentElement).toBe(document.body);
  expect(api.previewModelTest).toHaveBeenCalledWith(
    "saltmarch", { model: "b", capabilities: ["generate"] });
  expect(api.runModelTest).not.toHaveBeenCalled();
});

test("a test dialog closed mid-run and opened again rejoins that run", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", [], ["b"], []));
  // A run that never lands: closing the dialog does not stop it on the server.
  (api.runModelTest as any).mockImplementation(() => new Promise(() => {}));
  render(<Harness needs={["generate"]} />);

  fireEvent.click(await screen.findByRole("button", { name: "Test B" }));
  let dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByRole("button", { name: /testing/i });
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Test B" }));
  dialog = await screen.findByRole("dialog", { name: "Test a model" });

  // The same run, still going -- not a second, paid one on offer.
  expect(within(dialog).getByRole("button", { name: /testing/i })).toBeDisabled();
  expect(within(dialog).queryByRole("button", { name: "Run test" })).toBeNull();
  expect(api.runModelTest).toHaveBeenCalledTimes(1);
});

/** A run of B that landed: it works. */
const LANDED_B = { provider: "saltmarch", model: "b", rev: "r2",
                   results: { generate: { ok: true } }, recorded: true };

test("closing a dialog whose test landed puts focus back on that model", async () => {
  // A landed test re-asks the provider, which redraws every row -- so the
  // Test… the dialog came from is gone by the time it closes, and focus fell
  // to the body, where no key inside the picker (or a popover around it)
  // could reach it.
  (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", [], ["b"], []));
  (api.runModelTest as any).mockImplementation(async () => {
    (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", ["b"], [], []));
    return LANDED_B;
  });
  render(<Harness needs={["generate"]} />);
  const opener = await screen.findByRole("button", { name: "Test B" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByText("generate: works");
  // B fits now: its Test… went with the redraw.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Test B" })).toBeNull());

  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("radio", { name: "B" })));
});

test("a test landing behind a closed dialog keeps focus on its Test…", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", [], ["b"], []));
  let land: (r: typeof LANDED_B) => void = () => {};
  (api.runModelTest as any).mockImplementation(() => new Promise((resolve) => { land = resolve; }));
  render(<Harness needs={["generate"]} />);
  const opener = await screen.findByRole("button", { name: "Test B" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByRole("button", { name: /testing/i });
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(document.activeElement).toBe(opener);

  await act(async () => land(LANDED_B));

  // Redrawn, still unverified: the new Test… for the same model has it.
  await waitFor(() => expect(document.activeElement)
    .toBe(screen.getByRole("button", { name: "Test B" })));
  expect(document.activeElement).not.toBe(opener);
});

test("another model's test dialog starts clean", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", [], ["b", "d"], []));
  (api.runModelTest as any).mockImplementation(() => new Promise(() => {}));
  render(<Harness needs={["generate"]} />);

  fireEvent.click(await screen.findByRole("button", { name: "Test B" }));
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByRole("button", { name: /testing/i });

  // Reached behind the backdrop (a keyboard can), while B's run is out.
  fireEvent.click(screen.getByRole("button", { name: "Test D" }));

  await screen.findByRole("dialog", { name: "Test a model" });
  expect(api.previewModelTest).toHaveBeenLastCalledWith(
    "saltmarch", { model: "d", capabilities: ["generate"] });
  // B's run is B's: D's dialog offers its own, untouched.
  expect(await screen.findByRole("button", { name: "Run test" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /testing/i })).toBeNull();
});

test("a provider that cannot send says so without guessing why", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", ["a"], [], []));
  // `kind: ""` is what the view sends for a record that names none.
  const providers: InferenceProvider[] = [
    { id: "saltmarch", name: "Saltmarch Router", kind: "", preset: "custom", usable: false },
  ];
  const { rerender } = render(
    <ProviderModelPicker needs={["generate"]} providers={providers}
                         value={{ provider: "saltmarch", model: "" }} onChange={vi.fn()} />);
  await screen.findByRole("radio", { name: "A" });

  // No base URL fails the same check a missing key does, so "no key" would be
  // a guess -- and sometimes a wrong one.
  expect(screen.getByRole("option", { name: "Saltmarch Router (cannot send)" }))
    .toBeInTheDocument();
  expect(screen.queryByText(/no key/)).toBeNull();

  rerender(
    <ProviderModelPicker needs={["generate"]}
                         providers={[{ ...providers[0], problem: "it has no address" }]}
                         value={{ provider: "saltmarch", model: "" }} onChange={vi.fn()} />);
  expect(await screen.findByRole("option",
    { name: "Saltmarch Router (cannot send: it has no address)" })).toBeInTheDocument();
});

test("no needs asks nothing and offers no models", async () => {
  render(<Harness needs={[]} />);

  expect(await screen.findByLabelText("Provider")).toHaveValue("saltmarch");
  expect(api.readConnectionCapabilities).not.toHaveBeenCalled();
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.queryByLabelText("Model id")).toBeNull();
});

test("a failed read keeps the chosen model on screen and still takes a typed id", async () => {
  (api.readConnectionCapabilities as any).mockRejectedValue(
    Object.assign(new Error("offline"), { detail: "the provider is unreachable" }));
  const spy = vi.fn();
  render(<Harness needs={["generate"]} spy={spy}
                  start={{ provider: "saltmarch", model: "vendor/kept" }} />);

  expect(await screen.findByText(/Couldn't list this provider's models/)).toBeInTheDocument();
  const typed = screen.getByRole("group", { name: "Typed id" });
  expect(within(typed).getByRole("radio", { name: "vendor/kept" })).toBeChecked();
  expect(typed).toHaveTextContent("Not checked");

  fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "vendor/new" } });
  fireEvent.click(screen.getByRole("button", { name: "Use this id" }));
  expect(spy).toHaveBeenLastCalledWith({ provider: "saltmarch", model: "vendor/new" });
});

test("a provider-wide reason still shows the model a selection names", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("embed", [], [], [], "z.ai serves no embeddings"));
  render(<Harness needs={["embed"]} start={{ provider: "saltmarch", model: "vendor/kept" }} />);

  const typed = await screen.findByRole("group", { name: "Typed id" });
  expect(within(typed).getByRole("radio", { name: "vendor/kept" })).toBeChecked();
  expect(typed).toHaveTextContent("Known not to fit: z.ai serves no embeddings");
});

test("an empty list points a listable provider at its refresh", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", [], [], []));
  render(<Harness needs={["generate"]} />);
  expect(await screen.findByText(
    "No listed model can do this. Refresh this provider's models on the Providers page, "
    + "or type an id.")).toBeInTheDocument();
});

test("an empty list on a provider with no catalog asks only for an id", async () => {
  (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", [], [], []));
  render(<ProviderModelPicker needs={["generate"]} value={{ provider: "realm-claude", model: "" }}
                              onChange={() => {}}
                              providers={[{ id: "realm-claude", name: "Realm Claude",
                                            kind: "claude", usable: true }]} />);
  expect(await screen.findByText("This provider lists no models: type an id."))
    .toBeInTheDocument();
  expect(screen.queryByText(/Refresh/)).toBeNull();
});
