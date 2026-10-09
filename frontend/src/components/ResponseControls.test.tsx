import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResponseControls } from "./ResponseControls";
import { api } from "../api/client";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: { getResponse: vi.fn(), getCampaignInference: vi.fn(), readConnectionCapabilities: vi.fn() },
}));

/** The campaign's inference view, as the route picker reads it. */
const INFERENCE = {
  format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
  roles: {} as never, routes: [], preset_clear: "\u2063none",
  providers: [{ id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
                usable: true, problem: null }],
  presets: [{ id: "warm", name: "Warm" }], retirement_notes: [],
} as Awaited<ReturnType<typeof api.getCampaignInference>>;

function show(extra = {}, { open = true } = {}) {
  const actions = { onDelete: vi.fn(), onReroll: vi.fn(), onActivate: vi.fn(), onReplay: vi.fn() };
  render(<ResponseControls cid="realm" sid="scene" responseId="response-a" canReroll={true}
    {...actions} {...extra} />);
  // The actions live behind the disclosure, and are built only once it opens.
  if (open) fireEvent.click(screen.getByText("Response actions"));
  return actions;
}

describe("individual response controls", () => {
  it("deletes only the selected response and rerolls with its own steer", () => {
    const actions = show();
    fireEvent.click(screen.getByRole("button", { name: "Delete response" }));
    expect(actions.onDelete).toHaveBeenCalledWith("response-a");
    fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "More restrained" } });
    fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
    expect(actions.onReroll).toHaveBeenCalledWith("response-a", "More restrained",
      { provider: "", model: "", preset: "" });
  });
  it("shows retained incomplete and changed-context states, disables all writes while busy", () => {
    show({ disabled: true, status: "incomplete", contextChanged: true });
    expect(screen.getByText("Incomplete response")).toBeInTheDocument();
    expect(screen.getByText("Earlier context changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay from here" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete response" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reroll response" })).toBeDisabled();
  });
  it("keeps writing with the shared steer and route, and only when offered", () => {
    const onExtend = vi.fn();
    const actions = show({ onExtend });
    fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "Colder" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep writing ▸" }));
    expect(onExtend).toHaveBeenCalledWith("response-a", "Colder", { provider: "", model: "", preset: "" });
    expect(actions.onReroll).not.toHaveBeenCalled();
  });
  it("offers no Keep writing without a handler", () => {
    show();
    expect(screen.queryByRole("button", { name: "Keep writing ▸" })).not.toBeInTheDocument();
  });
  it("disables Keep writing on its own flag while Reroll response stays usable", () => {
    show({ onExtend: vi.fn(), extendDisabled: true });
    expect(screen.getByRole("button", { name: "Keep writing ▸" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reroll response" })).not.toBeDisabled();
  });
  it("disables Keep writing with every other write while busy", () => {
    show({ onExtend: vi.fn(), disabled: true });
    expect(screen.getByRole("button", { name: "Keep writing ▸" })).toBeDisabled();
  });
  it("hides Keep writing where there is no frozen prompt to continue from", () => {
    show({ onExtend: vi.fn(), canReroll: false });
    expect(screen.queryByRole("button", { name: "Keep writing ▸" })).not.toBeInTheDocument();
  });
  it("offers explicit replay when a historical snapshot is unavailable", () => {
    const actions = show({ canReroll: false });
    expect(screen.queryByRole("button", { name: "Reroll response" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replay from here" }));
    expect(actions.onReplay).toHaveBeenCalledOnce();
  });
  it("builds the actions only once the disclosure is opened, and keeps a steer across closing it", () => {
    // One of these sits under every response in a scene, nearly all of them
    // shut for good, so a shut one carries no body at all.
    show({ status: "incomplete" }, { open: false });
    expect(screen.getByText("Incomplete response")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete response" })).toBeNull();
    const summary = screen.getByText("Response actions");
    fireEvent.click(summary);
    fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "Quieter" } });
    fireEvent.click(summary);
    expect(screen.queryByLabelText("Response steer")).toBeNull();
    fireEvent.click(summary);
    expect(screen.getByLabelText("Response steer")).toHaveValue("Quieter");
  });
  it("shuts the route disclosure with the body, so a reopen reads nothing it does not show", async () => {
    // The route picker reads the campaign's inference view as it mounts. The
    // body is rebuilt on every reopen, and its route disclosure with it, shut:
    // a picker still mounted inside that shut disclosure would read it again
    // for a control nobody can see.
    vi.mocked(api.getCampaignInference).mockResolvedValue(INFERENCE);
    show();
    fireEvent.click(screen.getByText("Model for this reroll"));
    await waitFor(() => expect(api.getCampaignInference).toHaveBeenCalledWith("realm"));
    const summary = screen.getByText("Response actions");
    fireEvent.click(summary);
    fireEvent.click(summary);
    await screen.findByText("Model for this reroll");
    expect(screen.getByText("Model for this reroll").closest("details")).not.toHaveAttribute("open");
    expect(api.getCampaignInference).toHaveBeenCalledTimes(1);
  });
  it("rerolls on the provider, model and preset chosen for it", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(INFERENCE);
    vi.mocked(api.readConnectionCapabilities).mockResolvedValue({
      provider_preset: {} as never, need: "generate", reason: null, hidden: [],
      groups: { unverified: [], fits: [{ id: "qwen3", name: "Qwen 3", context: null, prompt: null,
        completion: null, reason: "", capabilities: {} as never }] } });
    const actions = show();
    fireEvent.click(screen.getByText("Model for this reroll"));
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "saltmarch" } });
    fireEvent.click(await screen.findByRole("radio", { name: "Qwen 3" }));
    fireEvent.change(screen.getByLabelText("Reroll preset"), { target: { value: "warm" } });
    fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
    expect(actions.onReroll).toHaveBeenCalledWith("response-a", "",
      { provider: "saltmarch", model: "qwen3", preset: "warm" });
  });
  it("never rerolls past a typed model id it has not taken", async () => {
    // A provider with no models takes a typed id, which is the route's only
    // once "Use this id" takes it. Rerolling before then sent the provider
    // alone -- the standing (or its own) model, not the one in the box.
    vi.mocked(api.getCampaignInference).mockResolvedValue(INFERENCE);
    vi.mocked(api.readConnectionCapabilities).mockResolvedValue({
      provider_preset: {} as never, need: "generate", reason: null, hidden: [],
      groups: { unverified: [], fits: [] } });
    const onExtend = vi.fn();
    const actions = show({ onExtend });
    fireEvent.click(screen.getByText("Model for this reroll"));
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "saltmarch" } });
    fireEvent.change(await screen.findByLabelText("Model id"), { target: { value: "vendor/typed" } });

    expect(screen.getByRole("button", { name: "Reroll response" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Keep writing ▸" })).toBeDisabled();
    expect(screen.getByText(/Use this id to reroll on vendor\/typed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
    expect(actions.onReroll).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Use this id" }));
    fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
    expect(actions.onReroll).toHaveBeenCalledWith("response-a", "",
      { provider: "saltmarch", model: "vendor/typed", preset: "" });
  });
  it("lets go of a typed id when the route disclosure shuts", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(INFERENCE);
    vi.mocked(api.readConnectionCapabilities).mockResolvedValue({
      provider_preset: {} as never, need: "generate", reason: null, hidden: [],
      groups: { unverified: [], fits: [] } });
    show();
    const disclosure = screen.getByText("Model for this reroll");
    fireEvent.click(disclosure);
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "saltmarch" } });
    fireEvent.change(await screen.findByLabelText("Model id"), { target: { value: "vendor/typed" } });
    expect(screen.getByRole("button", { name: "Reroll response" })).toBeDisabled();
    const summary = screen.getByText("Response actions");
    fireEvent.click(summary);
    fireEvent.click(summary);
    expect(await screen.findByRole("button", { name: "Reroll response" })).toBeEnabled();
  });
  it("activates the stable variant id from the selected response", async () => {
    vi.mocked(api.getResponse).mockResolvedValue({ id: "response-a", active_variant: "v1",
      variants: [{ id: "v1", content: "First", status: "complete" },
        { id: "v2", content: "Second", status: "complete" }, { id: "v3", content: "Partial", status: "incomplete", issue: "invalid_handoff" }] } as Awaited<ReturnType<typeof api.getResponse>>);
    const actions = show();
    fireEvent.click(screen.getByRole("button", { name: "Response variants" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Use variant 2" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Use variant 3 (incomplete)" })).toBeDisabled();
    expect(screen.getByText("Response issue: invalid_handoff")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use variant 2" }));
    expect(actions.onActivate).toHaveBeenCalledWith("response-a", "v2");
  });
  it("shows what made each variant under its text, and nothing for one with no record", async () => {
    vi.mocked(api.getResponse).mockResolvedValue({ id: "response-a", active_variant: "v1",
      settings: { words: 120, paragraphs: 3, style_id: "", phase: "play" },
      resume_settings: { words: 60, paragraphs: 1, style_id: "noir", phase: "play" },
      variants: [{ id: "v1", content: "First", status: "complete" },
        { id: "v2", content: "Second", status: "complete", made_by: { model: "vendor/m", guidance: "Colder." } },
        { id: "v3", content: "Third", status: "complete", made_by: { model: "vendor/r", composed: "resume" } }] } as Awaited<ReturnType<typeof api.getResponse>>);
    show();
    fireEvent.click(screen.getByRole("button", { name: "Response variants" }));
    await screen.findByRole("button", { name: "Use variant 2" });
    expect(screen.getAllByText(/^Model: /)).toHaveLength(2);
    expect(screen.getByText("Model: vendor/m")).toBeInTheDocument();
    expect(screen.getByText("Guided: Colder.")).toBeInTheDocument();
    expect(screen.getAllByText("Guided: Colder.")).toHaveLength(1);
    // Each variant reads the settings it was composed under.
    expect(screen.getByText("Length: ~120 words, 3 paragraphs")).toBeInTheDocument();
    expect(screen.getByText("Length: ~60 words, 1 paragraph")).toBeInTheDocument();
    expect(screen.getByText("Style: noir")).toBeInTheDocument();
    // The variant with no `made_by` renders no provenance beside its text.
    const first = screen.getByText("First").parentElement as HTMLElement;
    expect(first.querySelectorAll("p.subtle")).toHaveLength(0);
  });
});
