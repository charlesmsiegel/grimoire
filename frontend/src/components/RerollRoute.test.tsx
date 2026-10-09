import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: {
    getCampaignInference: vi.fn(), readConnectionCapabilities: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(),
  },
}));

import { api, PRESET_CLEAR, type CapabilityNeed } from "../api/client";
import RerollRoutePicker, { NO_REROLL_ROUTE, rerollOverrides, type RerollRoute } from "./RerollRoute";
import { forgetModelTests } from "./inference/TestCallDialog";
import { bodiesOf, declares, stylesheet } from "../testkit/stylesheet";

const PROVIDERS = [
  { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter", usable: true },
  { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom", usable: true },
  { id: "winifred", name: "Winifred Anthropic", kind: "anthropic", preset: "anthropic", usable: true },
  { id: "mara", name: "Mara Claude", kind: "claude", preset: "claude", usable: true },
];

/** The scene route as the campaign's view resolves it: what Default runs. */
const SCENE_ROUTE = {
  key: "scene", label: "Scene turns", hint: "", operation: "generate", default_role: "primary",
  tasks: ["chat", "retry", "regenerate", "extend"], requires: [], campaign_scoped: true,
  use: "", pin: { provider: "", model: "", preset: "" }, preset: "", problem: null, role: "primary",
  resolves: { provider: "saltmarch", provider_name: "Saltmarch Router", model: "vendor/campaign",
              preset: "warm", preset_name: "Warm", via: "role", scope: "global" },
  inherits: null,
};

function view(over: Record<string, unknown> = {}) {
  return {
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {}, routes: [SCENE_ROUTE], providers: PROVIDERS,
    presets: [{ id: "warm", name: "Warm" }, { id: "cold", name: "Cold" }],
    preset_clear: PRESET_CLEAR, ...over,
  };
}

const PRESET = {
  id: "custom", label: "Custom", kind: "openai_compatible", base_url: "", url_locked: false,
  billing: "metered", reports_price: false, always: [], possible: [], never: [],
};

function answer(need: CapabilityNeed, fits: { id: string; name: string }[]) {
  return {
    provider_preset: PRESET, need, reason: null, hidden: [],
    groups: {
      fits: fits.map((m) => ({ ...m, context: null, prompt: null, completion: null,
                               reason: "the catalog says so", capabilities: {} })),
      unverified: [],
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  (api.getCampaignInference as any).mockResolvedValue(view());
  (api.readConnectionCapabilities as any).mockResolvedValue(answer("generate", []));
});

/** The picker, controlled the way the reroll popover holds it. */
function Harness({ start = NO_REROLL_ROUTE, spy = vi.fn() }:
  { start?: RerollRoute; spy?: (route: RerollRoute) => void }) {
  const [route, setRoute] = useState(start);
  return <RerollRoutePicker cid="saltmarch-campaign" value={route}
                            onChange={(next) => { spy(next); setRoute(next); }} />;
}

test("the reroll picker sends provider, model and preset", async () => {
  (api.readConnectionCapabilities as any).mockImplementation((provider: string) =>
    Promise.resolve(answer("generate", provider === "realm" ? [{ id: "qwen3", name: "Qwen 3" }] : [])));
  const spy = vi.fn();
  render(<Harness spy={spy} />);

  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "realm" } });
  // The model list is the provider's own, narrowed to what a reroll needs.
  fireEvent.click(await screen.findByRole("radio", { name: "Qwen 3" }));
  fireEvent.change(screen.getByLabelText("Reroll preset"), { target: { value: "cold" } });

  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("realm", "generate");
  const last = spy.mock.lastCall![0] as RerollRoute;
  expect(last).toEqual({ provider: "realm", model: "qwen3", preset: "cold" });
  expect(rerollOverrides(last)).toEqual({ provider: "realm", model: "qwen3", preset: "cold" });
});

test("a preset alone keeps the route and changes only how it samples", async () => {
  const spy = vi.fn();
  render(<Harness spy={spy} />);

  fireEvent.change(await screen.findByLabelText("Reroll preset"), { target: { value: PRESET_CLEAR } });

  expect(rerollOverrides(spy.mock.lastCall![0])).toEqual({ preset: PRESET_CLEAR });
});

test("Default sends no override", async () => {
  const spy = vi.fn();
  render(<Harness start={{ provider: "realm", model: "qwen3", preset: "cold" }} spy={spy} />);
  // What Default runs is said beside it: the campaign's scene route, resolved.
  const standing = await screen.findByText(/Saltmarch Router · vendor\/campaign · Warm/);
  expect(standing).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Default" }));

  expect(spy).toHaveBeenLastCalledWith(NO_REROLL_ROUTE);
  expect(rerollOverrides(NO_REROLL_ROUTE)).toEqual({});
  expect(screen.getByLabelText<HTMLSelectElement>("Provider").value).toBe("");
  expect(screen.getByLabelText<HTMLSelectElement>("Reroll preset").value).toBe("");
  expect(screen.getByRole("button", { name: "Default" })).toHaveAttribute("aria-pressed", "true");
});

test("an anthropic provider offers its models", async () => {
  (api.readConnectionCapabilities as any).mockImplementation((provider: string) =>
    Promise.resolve(answer("generate", provider === "winifred"
      ? [{ id: "claude-opus-4-8", name: "Claude Opus 4.8" },
         { id: "claude-haiku-4-5", name: "Claude Haiku 4.5" }]
      : [])));
  const spy = vi.fn();
  render(<Harness spy={spy} />);

  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "winifred" } });
  const fits = await screen.findByRole("group", { name: "Fits" });
  expect(within(fits).getAllByRole("radio").map((r) => r.getAttribute("value")))
    .toEqual(["claude-haiku-4-5", "claude-opus-4-8"]);
  fireEvent.click(screen.getByRole("radio", { name: "Claude Opus 4.8" }));

  expect(spy).toHaveBeenLastCalledWith({ provider: "winifred", model: "claude-opus-4-8", preset: "" });
});

test("a claude provider with no catalog takes a typed model id", async () => {
  // The subscription lists nothing to choose from, so the id is typed --
  // through the same picker, not a roster kept by hand on this side.
  const spy = vi.fn();
  render(<Harness spy={spy} />);
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "mara" } });
  // Not pointed at a Refresh it does not have.
  await screen.findByText("This provider lists no models: type an id.");

  fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Use this id" }));

  expect(spy).toHaveBeenLastCalledWith({ provider: "mara", model: "opus", preset: "" });
  // and it is judged like any typed id, never silently trusted
  expect(await screen.findByRole("group", { name: "Typed id" })).toBeInTheDocument();
});

test("the providers, presets and Default are this campaign's, read fresh on open", async () => {
  render(<Harness />);

  await screen.findByText(/Saltmarch Router · vendor\/campaign/);
  expect(api.getCampaignInference).toHaveBeenCalledWith("saltmarch-campaign");
  // Each provider is offered, and the presets are the library's.
  for (const p of PROVIDERS) expect(screen.getByRole("option", { name: p.name })).toBeInTheDocument();
  const preset = screen.getByLabelText("Reroll preset");
  expect(within(preset).getByRole("option", { name: "Warm" })).toBeInTheDocument();
  expect(within(preset).getByRole("option", { name: "Cold" })).toBeInTheDocument();
});

test("a store still on the legacy layout keeps the per-reroll preset", async () => {
  // `override_inference` applies a reroll's preset in either layout, so a
  // format-1 store is offered it too.
  (api.getCampaignInference as any).mockResolvedValue(view({ format: "1" }));
  const spy = vi.fn();
  render(<Harness spy={spy} />);

  await screen.findByText(/Saltmarch Router · vendor\/campaign/);
  fireEvent.change(screen.getByLabelText("Reroll preset"), { target: { value: "cold" } });
  expect(rerollOverrides(spy.mock.lastCall![0])).toEqual({ preset: "cold" });
});

test("a view that cannot be read still offers Default, and says why nothing else is", async () => {
  (api.getCampaignInference as any).mockRejectedValue(new Error("offline"));
  render(<Harness />);

  expect(await screen.findByText(/Couldn't read the providers/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Default" })).toHaveAttribute("aria-pressed", "true");
});

test("Enter on the picker's own controls does not reach the popover; Escape does", async () => {
  // The popover commits the reroll on Enter. On a select, a button or the
  // typed-id box Enter already means something, and letting it bubble sent the
  // reroll through the route the reader was in the middle of choosing.
  (api.readConnectionCapabilities as any).mockResolvedValue(
    answer("generate", [{ id: "qwen3", name: "Qwen 3" }]));
  const onOuterEnter = vi.fn();
  const onOuterEscape = vi.fn();
  render(
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div onKeyDown={(e) => {
      if (e.key === "Enter") onOuterEnter();
      if (e.key === "Escape") onOuterEscape();
    }}>
      <Harness />
    </div>);
  const provider = await screen.findByLabelText("Provider");
  fireEvent.change(provider, { target: { value: "realm" } });
  const radio = await screen.findByRole("radio", { name: "Qwen 3" });

  for (const control of [provider, screen.getByLabelText("Reroll preset"),
                         screen.getByLabelText("Model id"),
                         screen.getByRole("button", { name: "Default" })]) {
    fireEvent.keyDown(control, { key: "Enter" });
  }
  expect(onOuterEnter).not.toHaveBeenCalled();

  // A chosen model row has no Enter of its own, so there it commits.
  fireEvent.keyDown(radio, { key: "Enter" });
  expect(onOuterEnter).toHaveBeenCalledTimes(1);
  // Escape backs out of the popover from any control.
  fireEvent.keyDown(provider, { key: "Escape" });
  expect(onOuterEscape).toHaveBeenCalledTimes(1);
});

test("a provider chosen without a model says which model it keeps", async () => {
  // `{provider}` alone runs the scene route's standing model on that provider
  // (spec 5.6) -- so the box says which one, rather than leaving the reader to
  // find out from the reply.
  (api.readConnectionCapabilities as any).mockImplementation((provider: string) =>
    Promise.resolve(answer("generate", provider === "realm" ? [{ id: "qwen3", name: "Qwen 3" }] : [])));
  render(<Harness />);
  await screen.findByText(/Saltmarch Router · vendor\/campaign/);
  expect(screen.queryByText(/^Keeps /)).toBeNull();

  fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "realm" } });

  expect(await screen.findByText("Keeps vendor/campaign")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("radio", { name: "Qwen 3" }));
  expect(screen.queryByText(/^Keeps /)).toBeNull();
});

test("a provider alone, where the route names no model to keep, says to choose one", async () => {
  (api.getCampaignInference as any).mockResolvedValue(view({
    routes: [{ ...SCENE_ROUTE, resolves: null }] }));
  render(<Harness start={{ provider: "realm", model: "", preset: "" }} />);

  expect(await screen.findByText(/names no model to keep/)).toBeInTheDocument();
});

test("at format 1 a provider alone keeps the standing model, as the server runs it", async () => {
  // A store whose upgrade is pending or failed is read by the server as the
  // new layout too (inference slice I), so a provider-only reroll keeps the
  // scene route's standing model there as well -- whatever model the
  // provider's legacy record names. The caption says so, and never offers
  // the provider's own model.
  (api.getCampaignInference as any).mockResolvedValue(view({
    format: "1", migration: { state: "pending", reason: "", skipped: [] },
    providers: PROVIDERS.map((p) => (p.id === "realm" ? { ...p, own_model: "qwen3-max" }
                                                     : { ...p, own_model: "" })) }));
  render(<Harness start={{ provider: "realm", model: "", preset: "" }} />);

  expect(await screen.findByText("Keeps vendor/campaign")).toBeInTheDocument();
  expect(screen.queryByText(/own model/)).toBeNull();
});

test("at format 1 a provider alone, where the route names no model to keep, says to choose one", async () => {
  (api.getCampaignInference as any).mockResolvedValue(view({
    format: "1", providers: PROVIDERS, routes: [{ ...SCENE_ROUTE, resolves: null }] }));
  render(<Harness start={{ provider: "realm", model: "", preset: "" }} />);

  expect(await screen.findByText(/names no model to keep/)).toBeInTheDocument();
  expect(screen.queryByText(/own model/)).toBeNull();
});

test("the popover fits a phone: capped to the viewport, and anchored inside it", () => {
  const { css, atWidth } = stylesheet();
  expect(bodiesOf(css, ".reroll-pop").map((b) => declares(b, "max-width")))
    .toContain("calc(100vw - 32px)");
  // The route row's fixed width yields to that cap rather than pushing past it.
  expect(bodiesOf(css, ".reroll-route").map((b) => declares(b, "max-width"))).toContain("100%");
  // Beside the gutter there is no room on a phone, so there it opens over the
  // post, from the gutter's own left edge.
  const phone = bodiesOf(atWidth(720), ".reroll-pop");
  expect(phone.map((b) => declares(b, "left"))).toContain("0");
  expect(phone.map((b) => declares(b, "margin-left"))).toContain("0");
});
