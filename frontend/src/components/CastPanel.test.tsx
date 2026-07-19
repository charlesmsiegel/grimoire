import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CastPanel } from "./CastPanel";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
      listCampaignPCs: vi.fn(),
      listEntities: vi.fn(), getSceneLocation: vi.fn(), setSceneLocation: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      addToCast: vi.fn(),
      opener: vi.fn(), firstPost: vi.fn(), createGreeting: vi.fn(), listAppearances: vi.fn(),
      campaignImageUrl: (c: string, ch: string, v: string, n: string) => `/cimg/${c}/${ch}/${v}/${n}`,
    },
  };
});
import { api } from "../api/client";

const GREG_MONTHS = [
  { key: "01", name: "January", days: 31 },
  { key: "02", name: "February", days: 28 },
  { key: "03", name: "March", days: 31 },
  { key: "04", name: "April", days: 30 },
  { key: "05", name: "May", days: 31 },
  { key: "06", name: "June", days: 30 },
  { key: "07", name: "July", days: 31 },
  { key: "08", name: "August", days: 31 },
  { key: "09", name: "September", days: 30 },
  { key: "10", name: "October", days: 31 },
  { key: "11", name: "November", days: 30 },
  { key: "12", name: "December", days: 31 },
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCast as any).mockResolvedValue([{ kind: "pcs", id: "elara", role: "player" }]);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c", world: "w" }, body: "" });
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  (api.listCampaignPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: true, friendly: "1 January 2027", id: "s" });
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
  (api.addToCast as any).mockResolvedValue({ ok: true });
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

test("initialPrompt seeds the opener prompt", async () => {
  renderPanel({ initialPrompt: "A debt-collector arrives." });
  await waitFor(() => expect(
    (screen.getByLabelText("Opener prompt") as HTMLInputElement).value,
  ).toBe("A debt-collector arrives."));
});

test("switching scenes clears a previously seeded prompt", async () => {
  const { rerender } = render(
    <CastPanel cid="c" sid="s" ready onSeeded={() => {}} initialPrompt="A premise" />,
  );
  await waitFor(() => expect(
    (screen.getByLabelText("Opener prompt") as HTMLInputElement).value,
  ).toBe("A premise"));
  rerender(<CastPanel cid="c" sid="s2" ready onSeeded={() => {}} />);
  await waitFor(() => expect(
    (screen.getByLabelText("Opener prompt") as HTMLInputElement).value,
  ).toBe(""));
});

function renderPanel(props: Partial<{ ready: boolean; onSeeded: () => void;
                                      onSceneRenamed: (id: string) => void; initialPrompt: string }> = {}) {
  render(
    <CastPanel cid="c" sid="s" ready={props.ready ?? true}
               onSeeded={props.onSeeded ?? (() => {})} onSceneRenamed={props.onSceneRenamed}
               initialPrompt={props.initialPrompt} />,
  );
}

test("renders the current cast", async () => {
  renderPanel();
  await screen.findByText("elara");
  expect(screen.getByText(/PC · player/)).toBeInTheDocument();
});

test("PC dropdown lists the campaign's PCs (copied world PCs + local overlays)", async () => {
  (api.listCampaignPCs as any).mockResolvedValue([
    { id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] },
    { id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] },
  ]);
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
    expect(api.createGreeting).toHaveBeenCalledWith({ kind: "campaign", id: "c" }, expect.objectContaining({
      character: "seraphine", version: "default", body: "Mist rolls in.",
    })),
  );
});

test("Use adopts the generated opener as the scene's first post", async () => {
  (api.opener as any).mockImplementation(async (_c: string, _s: string, _p: string, onEvent: any) => {
    onEvent({ delta: "Mist rolls in." });
  });
  (api.firstPost as any).mockResolvedValue({ ok: true });
  const onSeeded = vi.fn();
  renderPanel({ onSeeded });
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "A foggy harbor" } });
  fireEvent.click(screen.getByRole("button", { name: /generate/i }));
  await screen.findByText("Mist rolls in.");
  fireEvent.click(screen.getByRole("button", { name: /^Use$/ }));
  await waitFor(() => expect(api.firstPost).toHaveBeenCalledWith("c", "s", "Mist rolls in."));
  expect(onSeeded).toHaveBeenCalled();
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
  fireEvent.change(screen.getByLabelText("Scene date year"), { target: { value: "2027" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "01" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: /advance to|set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2027-01-01"));
  await waitFor(() => expect(onSeeded).toHaveBeenCalled());
});

test("a dateless scene with a suggestion pre-fills the date input", async () => {
  (api.getSceneDatetime as any).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  renderPanel();
  await screen.findByLabelText("Scene date year");
  // the picker's visible fields show the prefill once it arrives...
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date year")).toHaveValue(2026));
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("07"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("6");
  // ...and "Set date" is immediately enabled and submits the suggestion
  const button = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-06"));
});

test("first date set renames the scene: adopts the new id via onSceneRenamed", async () => {
  (api.setSceneDatetime as any).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  const onSceneRenamed = vi.fn();
  const onSeeded = vi.fn();
  renderPanel({ onSeeded, onSceneRenamed });
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: /advance to|set date/i }));
  await waitFor(() => expect(onSceneRenamed).toHaveBeenCalledWith("001--2026-07-04--s"));
  expect(onSeeded).not.toHaveBeenCalled();  // the parent re-selects via the new id instead
});

test("offscreen scene hides PC and player seating", async () => {
  render(<CastPanel cid="c" sid="s" ready onSeeded={() => {}} pcless />);
  await screen.findByText(/add to scene/i);
  expect(screen.queryByLabelText("Actor kind")).toBeNull();
  expect(screen.queryByLabelText("Role")).toBeNull();
});
