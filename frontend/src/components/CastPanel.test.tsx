import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CastPanel } from "./CastPanel";

vi.mock("../api/client", () => ({
  api: {
    getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
    listCampaignPCs: vi.fn(),
    listEntities: vi.fn(), getSceneLocation: vi.fn(), setSceneLocation: vi.fn(),
    getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(),
    availableGreetings: vi.fn(), addToCast: vi.fn(), startFromGreeting: vi.fn(),
    opener: vi.fn(), createGreeting: vi.fn(), listAppearances: vi.fn(),
    sceneSuggestions: vi.fn(),
    campaignImageUrl: (c: string, ch: string, v: string, n: string) => `/cimg/${c}/${ch}/${v}/${n}`,
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCast as any).mockResolvedValue([{ kind: "pcs", id: "elara", role: "player" }]);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c", world: "w" }, body: "" });
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: true, friendly: "1 January 2027" });
  (api.availableGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", available: true, reasons: [] },
    { id: "locked", name: "Locked", available: false, reasons: ["missing required tags"] },
  ]);
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
  (api.createGreeting as any).mockResolvedValue({ id: "g" });
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "sera", version: "default", role: "npc", scenes: ["s"] },
  ]);
});

test("character cast row shows the locked-version avatar", async () => {
  (api.getCast as any).mockResolvedValue([{ kind: "characters", id: "sera", role: "npc" }]);
  renderPanel();
  const img = await screen.findByAltText("sera avatar");
  expect(img.getAttribute("src")).toContain("/cimg/c/sera/default/avatar");
});

test("Suggest scenes fetches, renders, and a pick auto-seeds + prefills the prompt", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({ suggestions: [
    { title: "The creditor", premise: "A debt-collector arrives.",
      cast: [{ kind: "characters", id: "doran", name: "Doran" }],
      location: { id: "keep", name: "The Keep" } }] });
  renderPanel();
  fireEvent.click(await screen.findByRole("button", { name: /Suggest scenes/ }));
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByRole("button", { name: /Use this scene/ }));
  await waitFor(() => {
    expect(api.addToCast).toHaveBeenCalledWith("c", "s", { kind: "characters", id: "doran" });
    expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "keep");
  });
  expect((screen.getByLabelText("Opener prompt") as HTMLInputElement).value).toBe("A debt-collector arrives.");
});

function renderPanel(props: Partial<{ sceneEmpty: boolean; keySet: boolean; onSeeded: () => void }> = {}) {
  render(
    <CastPanel cid="c" sid="s" sceneEmpty={props.sceneEmpty ?? true} keySet={props.keySet ?? true}
               onSeeded={props.onSeeded ?? (() => {})} />,
  );
}

test("renders the current cast", async () => {
  renderPanel();
  await screen.findByText("elara");
  expect(screen.getByText(/PC · player/)).toBeInTheDocument();
});

test("PC dropdown includes campaign-local PCs", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listCampaignPCs as any).mockResolvedValue([{ id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] }]);
  renderPanel();
  fireEvent.change(await screen.findByLabelText(/actor kind/i), { target: { value: "pcs" } });
  await screen.findByRole("option", { name: "Elara" });
  await screen.findByRole("option", { name: "Mara" });
});

test("adding an actor posts kind + id (+ role for characters)", async () => {
  renderPanel();
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Actor"), { target: { value: "seraphine" } });
  fireEvent.change(screen.getByLabelText("Role"), { target: { value: "player" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() =>
    expect(api.addToCast).toHaveBeenCalledWith("c", "s", { kind: "characters", id: "seraphine", role: "player" }),
  );
});

test("starting from an available greeting seeds the scene; unavailable is disabled", async () => {
  const onSeeded = vi.fn();
  renderPanel({ onSeeded });
  const open = await screen.findByRole("button", { name: "Open" });
  expect(screen.getByRole("button", { name: "Locked" })).toBeDisabled();
  fireEvent.click(open);
  await waitFor(() => expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s", "open"));
  expect(onSeeded).toHaveBeenCalled();
});

test("start-from-greeting is disabled when the scene is not empty", async () => {
  renderPanel({ sceneEmpty: false });
  expect(await screen.findByRole("button", { name: "Open" })).toBeDisabled();
});

test("generating an opener streams into the preview and can be saved as a greeting", async () => {
  (api.opener as any).mockImplementation(async (_c: string, _s: string, _p: string, onEvent: any) => {
    onEvent({ delta: "Mist " });
    onEvent({ delta: "rolls in." });
  });
  vi.spyOn(window, "prompt").mockReturnValue("Opener");
  renderPanel();
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "A foggy harbor" } });
  fireEvent.click(screen.getByRole("button", { name: /generate/i }));
  await screen.findByText("Mist rolls in.");
  // pick a character so the opener can be saved as that character's greeting
  fireEvent.change(screen.getByLabelText("Actor"), { target: { value: "seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: /save as greeting/i }));
  await waitFor(() =>
    expect(api.createGreeting).toHaveBeenCalledWith("w", expect.objectContaining({
      character: "seraphine", version: "default", body: "Mist rolls in.",
    })),
  );
});

test("shows the current setting and lists campaign locations", async () => {
  (api.getSceneLocation as any).mockResolvedValue({ current: { id: "crypt", name: "The Crypt" }, visited: [] });
  (api.listEntities as any).mockResolvedValue([{ id: "crypt", name: "The Crypt" }, { id: "market", name: "Market" }]);
  renderPanel();
  await screen.findByText("The Crypt", { selector: "div.field-hint" });
  await screen.findByRole("option", { name: "Market" });
});

test("changing the setting calls setSceneLocation and refreshes the stream", async () => {
  const onSeeded = vi.fn();
  (api.listEntities as any).mockResolvedValue([{ id: "market", name: "Market" }]);
  renderPanel({ onSeeded });
  fireEvent.change(await screen.findByLabelText("Location"), { target: { value: "market" } });
  fireEvent.click(screen.getByRole("button", { name: /set location/i }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "market"));
  await waitFor(() => expect(onSeeded).toHaveBeenCalled());
});

test("When section shows the current date and advances", async () => {
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-12-25", friendly: "25 December 2026", weekday: "Friday",
               secondary_friendly: null, holidays_today: ["Christmas Day"], upcoming: null, cast: [] },
    history: ["2026-12-25"],
  });
  const onSeeded = vi.fn();
  renderPanel({ onSeeded });
  expect(await screen.findByText(/25 December 2026/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Scene date"), { target: { value: "2027-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: /advance to|set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2027-01-01"));
  await waitFor(() => expect(onSeeded).toHaveBeenCalled());
});
