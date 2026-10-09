import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api, type CapabilityNeed, type InferenceProvider, type ProviderHealth } from "../../api/client";
import { forgetModelTests } from "../inference/TestCallDialog";
import { ModelSelect } from "./ModelSelect";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: {
    readConnectionCapabilities: vi.fn(), previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});

const PRESET = {
  id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "", url_locked: true,
  billing: "metered", reports_price: true, always: [], possible: [], never: [],
};
type Value = "yes" | "no" | "unknown";
let CAPS: Record<string, Record<string, Value>>;

function answer(need: CapabilityNeed, model?: string) {
  const out = { provider_preset: PRESET, need, reason: null as string | null,
                groups: { fits: [] as unknown[], unverified: [] as unknown[] },
                hidden: [] as { id: string; reason: string }[] };
  for (const id of model ? [model] : Object.keys(CAPS)) {
    const v: Value = CAPS[id]?.[need] ?? "unknown";
    const row = { id, name: id, context: null, prompt: null, completion: null,
                  reason: v === "yes" ? "listed" : "not known yet",
                  capabilities: Object.fromEntries(Object.entries(CAPS[id] ?? {})
                    .map(([k, x]) => [k, { value: x, source: "catalog" }])) };
    if (v === "yes") out.groups.fits.push(row);
    else if (v === "unknown") out.groups.unverified.push(row);
    else out.hidden.push({ id, reason: `${id} cannot ${need}` });
  }
  return out;
}

const PROVIDERS: InferenceProvider[] = [
  { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
    usable: true, problem: null },
  { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom",
    usable: false, problem: "Realm Local has no key set" },
];
const health = (state: ProviderHealth["state"]): ProviderHealth =>
  ({ state, kind: "", detail: "", at: "" });
const HEALTH = new Map([["saltmarch", health("ok")], ["realm", health("error")]]);

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  CAPS = {
    "vendor/m": { generate: "yes", vision: "yes" },
    "vendor/eye": { generate: "yes", vision: "unknown" },
    "vendor/embed": { generate: "no", vision: "no" },
  };
  (api.readConnectionCapabilities as any).mockImplementation(
    (_p: string, need: CapabilityNeed, model?: string) => Promise.resolve(answer(need, model)));
});

/** A holder that keeps the value, as the form does, and reports each change. */
function Holder({ start, needs = ["generate"] as CapabilityNeed[], seen }:
  { start: { provider: string; model: string }; needs?: CapabilityNeed[];
    seen: { provider: string; model: string }[] }) {
  const [v, setV] = useState(start);
  return <ModelSelect label="Primary" needs={needs} value={v} providers={PROVIDERS}
                      health={HEALTH} emptyLabel="Not set"
                      onChange={(next) => { seen.push(next); setV(next); }} />;
}

const provider = () => screen.getByRole("combobox", { name: "Primary provider" });
const model = () => screen.getByRole("combobox", { name: "Primary model" });

test("each provider says its health, and one that cannot send stays choosable with why", async () => {
  render(<Holder start={{ provider: "", model: "" }} seen={[]} />);
  const options = within(provider()).getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(["Not set", "Saltmarch Router (working)",
                           "Realm Local (failing) — cannot send: Realm Local has no key set"]);
  expect(within(provider()).getByRole("option", { name: /Realm Local/ })).not.toBeDisabled();
});

test("choosing an unusable provider warns beside the row", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "", model: "" }} seen={seen} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  expect(seen[seen.length - 1]).toEqual({ provider: "realm", model: "" });
  expect(await screen.findByText("Realm Local cannot send: Realm Local has no key set"))
    .toBeInTheDocument();
});

test("a provider the list no longer holds is kept, never blanked", async () => {
  render(<Holder start={{ provider: "gone", model: "vendor/m" }} seen={[]} />);
  expect(provider()).toHaveValue("gone");
  expect(within(provider()).getByRole("option", { name: "gone (missing provider)" }))
    .toBeInTheDocument();
});

test("models come grouped: fits, then unverified; a known no is not offered", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={[]} />);
  const fits = await screen.findByRole("group", { name: "Fits this role" });
  expect(within(fits).getAllByRole("option").map((o) => o.textContent)).toEqual(["vendor/eye", "vendor/m"]);   // `combine` sorts by id
  expect(within(model()).queryByRole("option", { name: "vendor/embed" })).toBeNull();
});

test("two needs combine: a model unverified for one lands under Unverified", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "" }} needs={["generate", "vision"]} seen={[]} />);
  const unverified = await screen.findByRole("group", { name: "Unverified" });
  expect(within(unverified).getByRole("option", { name: "vendor/eye" })).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "Fits this role" }))
    .getByRole("option", { name: "vendor/m" })).toBeInTheDocument();
});

test("a stored model the list does not hold is kept and says so", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "vendor/ghost" }} seen={[]} />);
  expect(await screen.findByRole("option", { name: "vendor/ghost (not in this provider's list)" }))
    .toBeInTheDocument();
  expect(model()).toHaveValue("vendor/ghost");
});

test("Other model id… takes a typed id, and that id is judged on its own", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={seen} />);
  await screen.findByRole("group", { name: "Fits this role" });
  fireEvent.change(model(), { target: { value: "\u0000other" } });
  const box = screen.getByRole("textbox", { name: "Primary model id" });
  fireEvent.change(box, { target: { value: " vendor/typed " } });
  fireEvent.click(screen.getByRole("button", { name: "Use this id" }));
  expect(seen[seen.length - 1]).toEqual({ provider: "saltmarch", model: "vendor/typed" });
  // Not listed, so it is asked about alone: the unknown answer is Unverified.
  expect(await screen.findByText(/Unverified: not known yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Test vendor/typed" })).toBeInTheDocument();
});

test("changing the provider clears the model", async () => {
  const seen: { provider: string; model: string }[] = [];
  render(<Holder start={{ provider: "saltmarch", model: "vendor/m" }} seen={seen} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  expect(seen[seen.length - 1]).toEqual({ provider: "realm", model: "" });
});

test("an unverified choice offers Test…, and a fitting one does not", async () => {
  render(<Holder start={{ provider: "saltmarch", model: "vendor/eye" }} needs={["generate", "vision"]} seen={[]} />);
  expect(await screen.findByRole("button", { name: "Test vendor/eye" })).toBeInTheDocument();
  fireEvent.change(model(), { target: { value: "vendor/m" } });
  expect(screen.queryByRole("button", { name: /^Test / })).toBeNull();
});

test("closing the test dialog after a test landed puts focus back on the model select", async () => {
  // As the picker's own test does (ProviderModelPicker.test.tsx:301): the run
  // lands "works", the re-asked list moves vendor/eye into Fits, and the
  // Test… the dialog came from is gone by the time it closes.
  (api.previewModelTest as any).mockResolvedValue({
    provider: "Saltmarch Router", provider_id: "saltmarch", model: "vendor/eye",
    sends: [], estimated_cost_usd: null });
  (api.runModelTest as any).mockImplementation(async () => {
    CAPS["vendor/eye"].vision = "yes";
    return { provider: "saltmarch", model: "vendor/eye", rev: "r2",
             results: { vision: { ok: true } }, recorded: true };
  });
  render(<Holder start={{ provider: "saltmarch", model: "vendor/eye" }} needs={["generate", "vision"]} seen={[]} />);
  const opener = await screen.findByRole("button", { name: "Test vendor/eye" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Run test" }));
  await within(dialog).findByText("vision: works");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Test vendor/eye" })).toBeNull());
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(model()).toHaveFocus());
});

test("a slow answer for the provider just left never fills the new one's list", async () => {
  let release!: () => void;
  (api.readConnectionCapabilities as any).mockImplementation((p: string, need: CapabilityNeed) =>
    p === "saltmarch"
      ? new Promise((resolve) => { release = () => resolve(answer(need)); })
      : Promise.resolve({ ...answer(need), groups: { fits: [], unverified: [] }, hidden: [] }));
  render(<Holder start={{ provider: "saltmarch", model: "" }} seen={[]} />);
  fireEvent.change(provider(), { target: { value: "realm" } });
  await act(async () => { release(); });
  expect(screen.queryByRole("option", { name: "vendor/m" })).toBeNull();
});
