import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignWizard from "./CampaignWizard";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(),
    listTags: vi.fn(),
    listPCs: vi.fn(),
    createCampaign: vi.fn(),
    createCampaignPC: vi.fn(),
    createScene: vi.fn(),
    addToCast: vi.fn(),
    createEntity: vi.fn(),
    availableGreetings: vi.fn(),
    startFromGreeting: vi.fn(),
    opener: vi.fn(),
    getCalendarProviders: vi.fn(),
    getCalendarConfig: vi.fn(),
    listClimates: vi.fn(() => Promise.resolve({ climates: [
      { id: "temperate-interior", name: "Temperate Interior", builtin: true, custom: false },
      { id: "high-desert", name: "High Desert", builtin: true, custom: false },
    ] })),
    listModules: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([{ id: "w1", name: "Realm", created: "", updated: "", counts: {} }]);
  (api.listTags as any).mockResolvedValue({ t1: "rebel", t2: "scholar" });
  (api.listPCs as any).mockResolvedValue([]);
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
    { id: "my-custom-calendar", name: "My Custom Calendar" },
  ] });
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30 });
  (api.createCampaign as any).mockResolvedValue({ id: "run" });
  (api.createCampaignPC as any).mockResolvedValue({ pc: "mara", version: "default" });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.createEntity as any).mockResolvedValue({ id: "tavern" });
  (api.availableGreetings as any).mockResolvedValue([]);
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "", version: "1", source: "builtin", valid: true },
  ]);
});

function renderWizard(ready = false) {
  render(
    <MemoryRouter>
      <CampaignWizard ready={ready} />
    </MemoryRouter>,
  );
}

async function fillBackdropAndPC() {
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  // step 2: PC
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
}

test("Next is gated until campaign name is entered", async () => {
  renderWizard();
  await screen.findByText("Realm");
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
});

test("Create campaign commits the full sequence in order", async () => {
  renderWizard();
  await fillBackdropAndPC();
  // step 3: add a location, then create
  fireEvent.change(screen.getByLabelText(/location name/i), { target: { value: "The Tavern" } });
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));

  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1", "US", "gregorian", undefined, "temperate-interior"));
  expect(api.createCampaignPC).toHaveBeenCalledWith("run", expect.objectContaining({
    name: "Mara", tags: [], persona: expect.objectContaining({ name: "Mara" }),
  }));
  expect(api.createScene).toHaveBeenCalledWith("run");
  expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "mara", version: "default" });
  expect(api.createEntity).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "locations", expect.objectContaining({ name: "The Tavern" }));
  // advanced to the opener step
  await screen.findByRole("heading", { name: /opening/i });
});

test("module picker defaults to inherit and passes the chosen module", async () => {
  renderWizard();
  await screen.findByText("Realm");
  const select = await screen.findByLabelText("Mechanics module");
  expect((select as HTMLSelectElement).value).toBe("");
  fireEvent.change(select, { target: { value: "pool-basic" } });
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith(
    "Run One", "w1", "US", "gregorian", "pool-basic", "temperate-interior"));
});

test("step 1 shows the calendar select alongside holidays", async () => {
  renderWizard();
  await screen.findByText("Realm");
  const calendar = screen.getByLabelText(/^calendar$/i) as HTMLSelectElement;
  expect(calendar.value).toBe("gregorian");
  expect(screen.getByText(/regional holiday set/i)).toBeInTheDocument();
});

test("selecting a holidays region passes it to createCampaign", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.change(screen.getByLabelText("Holidays region"), { target: { value: "GB" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1", "GB", "gregorian", undefined, "temperate-interior"));
});

test("choosing a custom (user-authored) calendar hides the Holidays select and creates with no region", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "FR" } });
  fireEvent.change(screen.getByLabelText(/^calendar$/i), { target: { value: "my-custom-calendar" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  expect(screen.queryByLabelText("Observance")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("FR", "w1", undefined, "my-custom-calendar", undefined, "temperate-interior"));
});

test("choosing Hebrew and Israel passes the observance region to createCampaign", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "H" } });
  fireEvent.change(screen.getByLabelText(/^calendar$/i), { target: { value: "hebrew" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  fireEvent.change(screen.getByLabelText("Observance"), { target: { value: "IL" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("H", "w1", "IL", "hebrew", undefined, "temperate-interior"));
});

test("Finish on the opener step navigates to the campaign", async () => {
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  fireEvent.click(screen.getByRole("button", { name: /finish/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/run");
});

test("a world with PCs offers them in step 2; picking one seats it and skips PC creation", async () => {
  (api.listPCs as any).mockResolvedValue([
    { id: "mara", name: "Mara", tags: ["rebel"], default_version: "default", versions: [] },
    { id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] },
  ]);
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));

  await screen.findByText(/play an existing character/i);
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /mara/i }));
  expect(screen.queryByLabelText(/character name/i)).toBeNull(); // form collapses
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));

  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "mara" }));
  expect(api.createCampaignPC).not.toHaveBeenCalled();
  await screen.findByRole("heading", { name: /opening/i });
});

test("deselecting the picked PC restores the new-character form", async () => {
  (api.listPCs as any).mockResolvedValue([
    { id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] },
  ]);
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  const card = await screen.findByRole("button", { name: /mara/i });
  fireEvent.click(card);
  expect(screen.queryByLabelText(/character name/i)).toBeNull();
  fireEvent.click(card); // toggle off
  expect(screen.getByLabelText(/character name/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
});

test("a world without PCs shows no existing-character section", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(screen.getByLabelText(/character name/i)).toBeInTheDocument();
  expect(screen.queryByText(/play an existing character/i)).toBeNull();
});

test("the chosen climate is passed to createCampaign", async () => {
  // Without this the API's climate parameter has no caller, and every campaign
  // created through the product silently gets the fallback.
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.change(screen.getByLabelText("Climate"), { target: { value: "high-desert" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith(
    "Run One", "w1", "US", "gregorian", undefined, "high-desert"));
});

test("a first-run opener the model could not be reached for offers the recovery", async () => {
  // The likeliest place to meet an unreachable model is the wizard that just
  // asked you to configure one (#210) -- and it dropped the kind out of the
  // stream frame, so it read as a bare socket error.
  (api.opener as any).mockImplementation(
    async (_c: string, _s: string, _p: string, on: any) => {
      on({ error: { detail: "connection refused", kind: "network" } });
    });
  renderWizard(true);
  await fillBackdropAndPC();
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  fireEvent.change(screen.getByLabelText(/opener prompt/i), { target: { value: "A foggy harbor" } });
  fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
});

// ---- the world's calendar is the default this wizard opens on (#223) ----

test("the calendar picker opens on the world's own calendar, not on Gregorian", async () => {
  // The wizard ALWAYS sends `calendar`, and `create_campaign` treats any value
  // it is given as an explicit choice that overwrites the world's. Seeded from
  // the world, the reader can still pick something else; unseeded, a world set
  // to Hebrew quietly produced Gregorian campaigns forever.
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30 });
  renderWizard();
  await screen.findByText("Realm");
  await waitFor(() => expect(screen.getByLabelText("Calendar")).toHaveValue("hebrew"));
  expect(api.getCalendarConfig).toHaveBeenCalledWith({ kind: "world", id: "w1" });
});

test("a world with an unreadable calendar leaves the picker on its own default", async () => {
  (api.getCalendarConfig as any).mockRejectedValue(new Error("nope"));
  renderWizard();
  await screen.findByText("Realm");
  expect(screen.getByLabelText("Calendar")).toHaveValue("gregorian");
});

test("the world picker lists A-Z but still defaults to the most recent world", async () => {
  // Two different questions with two different answers. The options are a list
  // you scan for a name, so they are alphabetical; the default is the world
  // you are most likely starting a campaign in, which is the one you touched
  // last -- and `listWorlds` answers newest-first, so that is its first entry.
  // Picking whichever world happens to sort first would be a choice made by
  // spelling.
  (api.listWorlds as any).mockResolvedValue([
    { id: "w2", name: "Tidewrack", created: "", updated: "", counts: {} },
    { id: "w1", name: "Ashfall", created: "", updated: "", counts: {} },
  ]);
  renderWizard();
  await screen.findByText("Ashfall");
  const picker = screen.getByLabelText("World");
  expect(Array.from(picker.querySelectorAll("option")).map((o) => o.textContent))
    .toEqual(["Ashfall", "Tidewrack"]);
  expect(picker).toHaveValue("w2");
});

test("switching worlds mid-flight keeps the world you landed on", async () => {
  // The seed is a controlled input the reader will commit. A slower answer for
  // the world they left must not overwrite the one they are looking at.
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
    { id: "w2", name: "Saltmarch", created: "", updated: "", counts: {} },
  ]);
  let releaseFirst: (v: unknown) => void = () => {};
  const slow = new Promise((res) => { releaseFirst = res; });
  (api.getCalendarConfig as any).mockImplementation((scope: { id: string }) =>
    scope.id === "w1" ? slow : Promise.resolve({
      primary: { provider: "gregorian", region: "GB", custom_holidays: [], anchor: null },
      secondary: null, confirmed: true, stale_after_days: 30 }));
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText("World"), { target: { value: "w2" } });
  await waitFor(() => expect(screen.getByLabelText("Holidays region")).toHaveValue("GB"));
  releaseFirst({ primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
                 secondary: null, confirmed: true, stale_after_days: 30 });
  await waitFor(() => expect(screen.getByLabelText("Calendar")).toHaveValue("gregorian"));
  expect(screen.getByLabelText("Holidays region")).toHaveValue("GB");
});
