import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignWizard from "./CampaignWizard";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
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
    previewGreetings: vi.fn(),
    startFromGreeting: vi.fn(),
    opener: vi.fn(),
    getCalendarProviders: vi.fn(),
    getCalendarConfig: vi.fn(),
    listClimates: vi.fn(() => Promise.resolve({ climates: [
      { id: "temperate-interior", name: "Temperate Interior", builtin: true, custom: false },
      { id: "high-desert", name: "High Desert", builtin: true, custom: false },
    ] })),
    listModules: vi.fn(),
    checkCampaignPCName: vi.fn(),
    firstPost: vi.fn(),
  },
}));
import { api, type CalendarConfig } from "../api/client";
import { fixture } from "../testkit/fixture";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listWorlds).mockResolvedValue([{ id: "w1", name: "Realm", created: "", updated: "", counts: {} }]);
  vi.mocked(api.listTags).mockResolvedValue({ t1: "rebel", t2: "scholar" });
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.getCalendarProviders).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
    { id: "my-custom-calendar", name: "My Custom Calendar" },
  ] });
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30 }));
  vi.mocked(api.createCampaign).mockResolvedValue({ id: "run" });
  vi.mocked(api.createCampaignPC).mockResolvedValue({ pc: "mara", version: "default" });
  vi.mocked(api.createScene).mockResolvedValue({ id: "s1" });
  vi.mocked(api.addToCast).mockResolvedValue({ ok: true });
  vi.mocked(api.createEntity).mockResolvedValue({ id: "tavern" });
  vi.mocked(api.availableGreetings).mockResolvedValue(fixture([]));
  vi.mocked(api.previewGreetings).mockResolvedValue([]);
  vi.mocked(api.startFromGreeting).mockResolvedValue(fixture({ ok: true }));
  vi.mocked(api.checkCampaignPCName).mockResolvedValue({ refusal: null });
  vi.mocked(api.listModules).mockResolvedValue([
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
  // Next asks the server whether the name is free before it leaves the step.
  await screen.findByRole("heading", { name: /starting locations|create your character/i });
}

test.each(["Back", "edit"])("a pending name check cannot advance after %s", async (action) => {
  let settle: (value: { refusal: string | null }) => void = () => { throw new Error("check not started"); };
  vi.mocked(api.checkCampaignPCName).mockReturnValueOnce(new Promise((resolve) => { settle = resolve; }));
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(api.checkCampaignPCName).toHaveBeenCalledWith("w1", "Mara");
  if (action === "Back") fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  else fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Winifred" } });
  await act(async () => { settle({ refusal: null }); });
  expect(screen.queryByRole("heading", { name: /starting locations/i })).not.toBeInTheDocument();
  if (action === "Back") expect(screen.getByLabelText(/campaign name/i)).toBeInTheDocument();
  else {
    expect(screen.getByLabelText(/character name/i)).toHaveValue("Winifred");
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByRole("heading", { name: /starting locations/i });
    expect(api.checkCampaignPCName).toHaveBeenLastCalledWith("w1", "Winifred");
  }
});

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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));

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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith(
    "Run One", "w1", "US", "gregorian", "pool-basic", "temperate-interior"));
});

test("step 1 shows the calendar select alongside holidays", async () => {
  renderWizard();
  await screen.findByText("Realm");
  const calendar = screen.getByLabelText<HTMLSelectElement>(/^calendar$/i);
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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("H", "w1", "IL", "hebrew", undefined, "temperate-interior"));
});

test("Finish on the opener step navigates to the campaign", async () => {
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  fireEvent.click(screen.getByRole("button", { name: /finish/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/run");
});

test("a failed wizard opener retries only unfinished speakers", async () => {
  const cast = [
    { actor_ref: "grimoire", speaker: "Grimoire", version: "" },
    { actor_ref: "characters:mara", speaker: "Mara", version: "default" },
  ];
  const narrator = { ...cast[0], content: "Rain falls." };
  vi.mocked(api.opener).mockImplementationOnce(async (_cid, _sid,
                                                _prompt, on) => {
    on({ snapshot: cast });
    on({ speaker_done: narrator });
    on({ speaker_start: cast[1] });
    on({ delta: "unfinished" });
    on({ error: { kind: "rate_limit", detail: "Wait" } });
  }).mockImplementationOnce(async (_cid, _sid,
                             _prompt, on) => {
    on({ snapshot: cast });
    on({ speaker_done: { ...cast[1], content: "I wait." } });
    on({ done: true });
  });
  renderWizard(true);
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "Arrive" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  const retry = await screen.findByRole("button", { name: "Retry remaining speakers" });
  // Rendered: the speaker's label is bold, not wrapped in asterisks.
  expect(screen.getByText("Grimoire:").tagName).toBe("STRONG");
  expect(screen.getByText(/Rain falls\./)).toBeInTheDocument();
  expect(screen.queryByText(/unfinished/)).toBeNull();
  // An unfinished opener cannot begin the scene, and says so.
  expect(screen.getByText(/Not saved/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^finish/i })).toBeInTheDocument();
  fireEvent.click(retry);
  await waitFor(() => expect(api.opener).toHaveBeenCalledTimes(2));
  expect(api.opener).toHaveBeenLastCalledWith("run", "s1", "Arrive", expect.any(Function),
                                            undefined, [narrator], cast);
  expect(await screen.findByText(/I wait\./)).toBeInTheDocument();
});

test("a world with PCs offers them in step 2; picking one seats it and skips PC creation", async () => {
  vi.mocked(api.listPCs).mockResolvedValue([
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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));

  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "mara" }));
  expect(api.createCampaignPC).not.toHaveBeenCalled();
  await screen.findByRole("heading", { name: /opening/i });
});

test("deselecting the picked PC restores the new-character form", async () => {
  vi.mocked(api.listPCs).mockResolvedValue([
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
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith(
    "Run One", "w1", "US", "gregorian", undefined, "high-desert"));
});

test("a first-run opener the model could not be reached for offers the recovery", async () => {
  // The likeliest place to meet an unreachable model is the wizard that just
  // asked you to configure one (#210) -- and it dropped the kind out of the
  // stream frame, so it read as a bare socket error.
  vi.mocked(api.opener).mockImplementation(
    async (_c, _s, _p, on) => {
      on({ error: { detail: "connection refused", kind: "network" } });
    });
  renderWizard(true);
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  fireEvent.change(screen.getByLabelText(/opener prompt/i), { target: { value: "A foggy harbor" } });
  fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Providers/ })).toHaveAttribute("href", "/providers");
});

// ---- the world's calendar is the default this wizard opens on (#223) ----

test("the calendar picker opens on the world's own calendar, not on Gregorian", async () => {
  // The wizard ALWAYS sends `calendar`, and `create_campaign` treats any value
  // it is given as an explicit choice that overwrites the world's. Seeded from
  // the world, the reader can still pick something else; unseeded, a world set
  // to Hebrew quietly produced Gregorian campaigns forever.
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30 }));
  renderWizard();
  await screen.findByText("Realm");
  await waitFor(() => expect(screen.getByLabelText("Calendar")).toHaveValue("hebrew"));
  expect(api.getCalendarConfig).toHaveBeenCalledWith({ kind: "world", id: "w1" });
});

test("a world with an unreadable calendar leaves the picker on its own default", async () => {
  vi.mocked(api.getCalendarConfig).mockRejectedValue(new Error("nope"));
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
  vi.mocked(api.listWorlds).mockResolvedValue([
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
  vi.mocked(api.listWorlds).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
    { id: "w2", name: "Saltmarch", created: "", updated: "", counts: {} },
  ]);
  let releaseFirst: (v: CalendarConfig) => void = () => {};
  const slow = new Promise<CalendarConfig>((res) => { releaseFirst = res; });
  vi.mocked(api.getCalendarConfig).mockImplementation((scope) =>
    scope.id === "w1" ? slow : Promise.resolve(fixture({
      primary: { provider: "gregorian", region: "GB", custom_holidays: [], anchor: null },
      secondary: null, confirmed: true, stale_after_days: 30 })));
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText("World"), { target: { value: "w2" } });
  await waitFor(() => expect(screen.getByLabelText("Holidays region")).toHaveValue("GB"));
  releaseFirst(fixture({ primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
                         secondary: null, confirmed: true, stale_after_days: 30 }));
  await waitFor(() => expect(screen.getByLabelText("Calendar")).toHaveValue("gregorian"));
  expect(screen.getByLabelText("Holidays region")).toHaveValue("GB");
});

test("a world tag is suggested by label and committed as its id", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });

  // the datalist offers the readable label, never the id a greeting matches on
  const opts = await screen.findAllByRole("option", { hidden: true });
  expect(opts.map((o) => (o as HTMLOptionElement).value)).toEqual(["rebel", "scholar"]);

  fireEvent.change(screen.getByLabelText("Add tag"), { target: { value: "rebel" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  // the chip reads as the label, so picking one is not a lie about what was stored
  await screen.findByRole("button", { name: /rebel ✕/ });

  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaignPC).toHaveBeenCalledWith("run", expect.objectContaining({
    tags: ["t1"],
  })));
});

test("a tag the world does not declare is committed verbatim", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.change(await screen.findByLabelText("Add tag"), { target: { value: "stowaway" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await screen.findByRole("button", { name: /stowaway ✕/ });

  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaignPC).toHaveBeenCalledWith("run", expect.objectContaining({
    tags: ["stowaway"],
  })));
});

test("step 2 previews draft tag IDs without saving any records", async () => {
  vi.mocked(api.previewGreetings).mockImplementation(async (_world, tags) => [{
    id: "open", name: "Open", available: tags.includes("t1"),
    reasons: tags.includes("t1") ? [] : ["missing required tags"], pcless: false, location: "",
  }]);
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  await screen.findByText(/missing required tags/);
  fireEvent.change(screen.getByLabelText("Add tag"), { target: { value: "rebel" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t1"]));
  await screen.findByText("Open");
  expect(screen.queryByText(/missing required tags/)).toBeNull();
  expect(api.createCampaign).not.toHaveBeenCalled();
  expect(api.createCampaignPC).not.toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
  expect(api.addToCast).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /rebel ✕/ }));
  await screen.findByText(/missing required tags/);
  expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", []);
});

test("picking an existing PC previews its tags and deselecting restores the draft", async () => {
  vi.mocked(api.listPCs).mockResolvedValue([
    { id: "mara", name: "Mara", tags: ["t1"], default_version: "default", versions: [] },
  ]);
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText("Add tag"), { target: { value: "scholar" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t2"]));
  fireEvent.click(screen.getByRole("button", { name: /Mara/ }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t1"]));
  fireEvent.click(screen.getByRole("button", { name: /Mara/ }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t2"]));
  expect(screen.getByRole("button", { name: /scholar ✕/ })).toBeInTheDocument();
  expect(api.createCampaign).not.toHaveBeenCalled();
});

test.each(["success", "failure"])("ignores the old world's late PC and tag %s", async (outcome) => {
  vi.mocked(api.listWorlds).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
    { id: "w2", name: "Saltmarch", created: "", updated: "", counts: {} },
  ]);
  let resolvePCs: (rows: Awaited<ReturnType<typeof api.listPCs>>) => void = () => {};
  let rejectPCs: (err: Error) => void = () => {};
  let resolveTags: (rows: Record<string, string>) => void = () => {};
  let rejectTags: (err: Error) => void = () => {};
  const oldPCs = new Promise<Awaited<ReturnType<typeof api.listPCs>>>((resolve, reject) => {
    resolvePCs = resolve; rejectPCs = reject;
  });
  const oldTags = new Promise<Record<string, string>>((resolve, reject) => {
    resolveTags = resolve; rejectTags = reject;
  });
  vi.mocked(api.listPCs).mockImplementation((scope) => scope.id === "w1" ? oldPCs : Promise.resolve([
    { id: "winifred", name: "Winifred", tags: ["t2"], default_version: "default", versions: [] },
  ]));
  vi.mocked(api.listTags).mockImplementation((wid) => wid === "w1" ? oldTags : Promise.resolve({ t2: "scholar" }));
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText("World"), { target: { value: "w2" } });
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  await screen.findByRole("button", { name: /Winifred/ });
  await act(async () => {
    if (outcome === "success") {
      resolvePCs([{ id: "mara", name: "Mara", tags: ["t1"], default_version: "default", versions: [] }]);
      resolveTags({ t1: "rebel" });
    } else {
      rejectPCs(new Error("old PCs failed")); rejectTags(new Error("old tags failed"));
    }
    await Promise.allSettled([oldPCs, oldTags]);
  });
  expect(screen.queryByRole("button", { name: /Mara/ })).toBeNull();
  expect(screen.getAllByRole("option", { hidden: true }).map((o) => (o as HTMLOptionElement).value))
    .toEqual(["scholar"]);
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w2", ["t2"]));
});

test("switching worlds drops the draft's tags, which name the old world's vocabulary", async () => {
  vi.mocked(api.listWorlds).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
    { id: "w2", name: "Saltmarch", created: "", updated: "", counts: {} },
  ]);
  vi.mocked(api.listTags).mockImplementation(async (wid) =>
    wid === "w1" ? { t1: "rebel" } : { t1: "sailor" });
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.change(screen.getByLabelText("Add tag"), { target: { value: "rebel" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await screen.findByRole("button", { name: /rebel ✕/ });
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t1"]));
  fireEvent.change(screen.getByLabelText("Add tag"), { target: { value: "stowaway" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w1", ["t1", "stowaway"]));
  fireEvent.click(screen.getByRole("button", { name: /back/i }));
  fireEvent.change(screen.getByLabelText("World"), { target: { value: "w2" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  // The free-form tag was never world vocabulary, so it carries across; the ID does not.
  await waitFor(() => expect(api.previewGreetings).toHaveBeenLastCalledWith("w2", ["stowaway"]));
  expect(screen.getByRole("button", { name: /stowaway ✕/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /sailor ✕/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /rebel ✕/ })).toBeNull();
});

// ---- a commit is five writes, and a failure part-way must not orphan the first ----

test("a commit that fails after the campaign exists resumes it rather than making another", async () => {
  // Create used to start every attempt with `createCampaign`, so a failure at
  // any later step left that campaign behind -- and the retry made the next one
  // beside it, `-2`, `-3`, one per click.
  vi.mocked(api.createScene).mockRejectedValueOnce(new Error("the scene could not be written"));
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.change(screen.getByLabelText(/location name/i), { target: { value: "The Tavern" } });
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByText(/the scene could not be written/);

  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  expect(api.createCampaign).toHaveBeenCalledTimes(1);
  expect(api.createCampaignPC).toHaveBeenCalledTimes(1);
  expect(api.createScene).toHaveBeenCalledTimes(2);
  expect(api.addToCast).toHaveBeenCalledTimes(1);
  expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "mara", version: "default" });
  expect(api.createEntity).toHaveBeenCalledTimes(1);
});

test("a location written before the failure is not written twice", async () => {
  vi.mocked(api.createEntity).mockResolvedValueOnce({ id: "tavern" })
    .mockRejectedValueOnce(new Error("the second location could not be written"));
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.change(screen.getByLabelText(/location name/i), { target: { value: "The Tavern" } });
  fireEvent.click(screen.getByRole("button", { name: /add another location/i }));
  fireEvent.change(screen.getAllByLabelText(/location name/i)[1], { target: { value: "The Quay" } });
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByText(/second location could not be written/);
  // The written one says so and can no longer be edited into something the
  // retry would not write.
  expect(screen.getAllByLabelText(/location name/i)[0]).toBeDisabled();
  expect(screen.getAllByLabelText(/location name/i)[1]).toBeEnabled();

  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  expect(api.createCampaign).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.createEntity).mock.calls.map((c) => c[2].name))
    .toEqual(["The Tavern", "The Quay", "The Quay"]);
});

test.each(["campaign", "location"])("location drafts cannot change while the %s write is pending and unlock after refusal", async (phase) => {
  let refuse: (error: Error) => void = () => { throw new Error("write was not requested"); };
  if (phase === "campaign") vi.mocked(api.createCampaign).mockReturnValueOnce(new Promise((_resolve, reject) => { refuse = reject; }));
  else vi.mocked(api.createEntity).mockReturnValueOnce(new Promise((_resolve, reject) => { refuse = reject; }));
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.change(screen.getByLabelText(/location name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /add another location/i }));
  fireEvent.change(screen.getAllByLabelText(/location name/i)[1], { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(phase === "campaign" ? api.createCampaign : api.createEntity).toHaveBeenCalled());
  for (const field of screen.getAllByRole("textbox")) expect(field).toBeDisabled();
  const add = screen.getByRole("button", { name: /add another location/i });
  expect(add).toBeDisabled();
  fireEvent.click(add);
  for (const remove of screen.getAllByRole("button", { name: /^remove$/i })) {
    expect(remove).toBeDisabled();
    fireEvent.click(remove);
  }
  expect(screen.getAllByLabelText(/location name/i)).toHaveLength(2);
  await act(async () => { refuse(new Error("write refused")); });
  expect(await screen.findByText(/write refused/)).toBeInTheDocument();
  for (const field of screen.getAllByRole("textbox")) expect(field).toBeEnabled();
  expect(add).toBeEnabled();
  expect(screen.getAllByLabelText(/location name/i)[0]).toHaveValue("Saltmarch");
  expect(screen.getAllByLabelText(/location name/i)[1]).toHaveValue("Realm");
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  expect(vi.mocked(api.createEntity).mock.calls.map((call) => call[2].name))
    .toEqual(phase === "campaign" ? ["Saltmarch", "Realm"] : ["Saltmarch", "Saltmarch", "Realm"]);
});

test("a refusal from the previous world does not survive checking the same name in another world", async () => {
  vi.mocked(api.listWorlds).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
    { id: "w2", name: "Saltmarch", created: "", updated: "", counts: {} },
  ]);
  vi.mocked(api.checkCampaignPCName).mockResolvedValueOnce({ refusal: "Mara is already in Realm." });
  let accept: (value: { refusal: string | null }) => void = () => { throw new Error("check was not requested"); };
  vi.mocked(api.checkCampaignPCName).mockReturnValueOnce(new Promise((resolve) => { accept = resolve; }));
  renderWizard();
  await fillBackdropAndPC();
  expect(screen.getByRole("alert")).toHaveTextContent("Mara is already in Realm.");
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  fireEvent.change(screen.getByLabelText(/^world$/i), { target: { value: "w2" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(screen.getByLabelText(/character name/i)).toHaveValue("Mara");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(api.checkCampaignPCName).toHaveBeenLastCalledWith("w2", "Mara");
  await act(async () => { accept({ refusal: null }); });
  await screen.findByRole("heading", { name: /starting locations/i });
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(screen.getByLabelText(/character name/i)).toHaveValue("Mara");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.createCampaign).not.toHaveBeenCalled();
});

test("a PC name the world already holds is refused on step 2, before anything is written", async () => {
  vi.mocked(api.checkCampaignPCName).mockResolvedValue({
    refusal: "There is already a character called \"Mara\" in this world. A transcript "
      + "could not tell the two apart, so this one needs another name.",
  });
  renderWizard();
  await fillBackdropAndPC();
  expect(await screen.findByRole("alert")).toHaveTextContent(/already a character called "Mara"/);
  // Still on step 2, with the field to fix.
  expect(screen.getByLabelText(/character name/i)).toBeInTheDocument();
  expect(api.checkCampaignPCName).toHaveBeenCalledWith("w1", "Mara");
  expect(api.createCampaign).not.toHaveBeenCalled();

  vi.mocked(api.checkCampaignPCName).mockResolvedValue({ refusal: null });
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Winifred" } });
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  await screen.findByRole("button", { name: /create campaign/i });
});

test("a check that cannot be answered does not hold the wizard on step 2", async () => {
  // The check is advice: the PC route's own refusal still binds, and a commit
  // it refuses resumes rather than orphaning.
  vi.mocked(api.checkCampaignPCName).mockRejectedValue(new Error("offline"));
  renderWizard();
  await fillBackdropAndPC();
  await screen.findByRole("button", { name: /create campaign/i });
});

test("a picked world PC is not name-checked: it is not a new name", async () => {
  vi.mocked(api.listPCs).mockResolvedValue([
    { id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] },
  ]);
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /mara/i }));
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  await screen.findByRole("button", { name: /create campaign/i });
  expect(api.checkCampaignPCName).not.toHaveBeenCalled();
});

test("a PC refused at commit is renamed on step 2 and the retry reuses the campaign", async () => {
  // The race the step-2 check cannot close: the name was free when asked and
  // taken by the time the PC was written. The campaign exists by then, so the
  // backdrop it was made from is no longer offered for editing -- Back from
  // step 2 is gone -- while the character is, because nothing was written for
  // it.
  vi.mocked(api.createCampaignPC)
    .mockRejectedValueOnce({ status: 409, detail: "There is already a character called \"Mara\" in this campaign." })
    .mockResolvedValueOnce({ pc: "winifred", version: "default" });
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByText(/already a character called "Mara" in this campaign/);

  fireEvent.click(screen.getByRole("button", { name: /back/i }));
  expect(screen.queryByRole("button", { name: /back/i })).toBeNull();
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
  expect(api.createCampaign).toHaveBeenCalledTimes(1);
  expect(api.createCampaignPC).toHaveBeenLastCalledWith("run", expect.objectContaining({ name: "Winifred" }));
  expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "winifred", version: "default" });
});

test("once the character is written, step 3 offers no way back to edit it", async () => {
  vi.mocked(api.createScene).mockRejectedValueOnce(new Error("the scene could not be written"));
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByText(/the scene could not be written/);
  expect(screen.queryByRole("button", { name: /back/i })).toBeNull();
});

// ---- the way out, and the way into the scene ----

test("step 1 offers a way out, since a wizard has no rail beside it", async () => {
  renderWizard();
  await screen.findByText("Realm");
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(navigate).toHaveBeenCalledWith("/");
  expect(api.createCampaign).not.toHaveBeenCalled();
});

async function toOpenerStep() {
  renderWizard(true);
  await fillBackdropAndPC();
  fireEvent.click(await screen.findByRole("button", { name: /create campaign/i }));
  await screen.findByRole("heading", { name: /opening/i });
}

test("a greeting that cannot open the campaign is named with its reason, not hidden in a tooltip", async () => {
  vi.mocked(api.availableGreetings).mockResolvedValue(fixture([
    { id: "quay", name: "On the quay", available: true, reasons: [], pcless: false, location: "" },
    { id: "keep", name: "At the keep", available: false, reasons: ["needs the scholar tag"],
      pcless: false, location: "" },
  ]));
  await toOpenerStep();
  expect(await screen.findByRole("button", { name: "On the quay" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "At the keep" })).toBeNull();
  expect(screen.getByText(/needs the scholar tag/)).toBeInTheDocument();
});

const CAST = [
  { actor_ref: "pcs:mara", speaker: "Mara", version: "default", role: "player" as const },
  { actor_ref: "grimoire", speaker: "Grimoire", version: "" },
];
const PART = { ...CAST[1], content: "Rain falls on the quay." };

function aWholeOpener() {
  vi.mocked(api.opener).mockImplementation(async (_c, _s, _p, on) => {
    on({ snapshot: CAST });
    on({ speaker_done: PART });
    on({ done: true });
  });
}

test("Finish with a whole opener posts it as the scene's first post and opens the scene", async () => {
  // It used to be thrown away: the reader paid for the preview and landed on
  // an empty scene.
  aWholeOpener();
  vi.mocked(api.firstPost).mockResolvedValue({ ok: true });
  await toOpenerStep();
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "Arrive" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  fireEvent.click(await screen.findByRole("button", { name: /begin with this opener/i }));
  await waitFor(() => expect(api.firstPost).toHaveBeenCalledWith(
    "run", "s1", "**Grimoire:** Rain falls on the quay.", [PART], CAST));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/campaigns/run/scenes/s1"));
});

test("a whole opener can still be left behind, deliberately", async () => {
  aWholeOpener();
  await toOpenerStep();
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "Arrive" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  fireEvent.click(await screen.findByRole("button", { name: /finish without it/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/run");
  expect(api.firstPost).not.toHaveBeenCalled();
});

test("an opener the scene refuses stays on screen with the reason", async () => {
  aWholeOpener();
  vi.mocked(api.firstPost).mockRejectedValue({ status: 409, detail: "the scene already has posts" });
  await toOpenerStep();
  fireEvent.change(screen.getByLabelText("Opener prompt"), { target: { value: "Arrive" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  fireEvent.click(await screen.findByRole("button", { name: /begin with this opener/i }));
  expect(await screen.findByText(/the scene already has posts/)).toBeInTheDocument();
  expect(navigate).not.toHaveBeenCalled();
  expect(screen.getByText(/Rain falls on the quay/)).toBeInTheDocument();
});
