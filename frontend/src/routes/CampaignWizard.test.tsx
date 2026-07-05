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
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([{ id: "w1", name: "Realm", created: "", updated: "", counts: {} }]);
  (api.listTags as any).mockResolvedValue({ t1: "rebel", t2: "scholar" });
  (api.listPCs as any).mockResolvedValue([]);
  (api.createCampaign as any).mockResolvedValue({ id: "run" });
  (api.createCampaignPC as any).mockResolvedValue({ pc: "mara", version: "default" });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.createEntity as any).mockResolvedValue({ id: "tavern" });
  (api.availableGreetings as any).mockResolvedValue([]);
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
});

function renderWizard() {
  render(
    <MemoryRouter>
      <CampaignWizard keySet={false} />
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

  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1", "US"));
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

test("step 1 shows the calendar select alongside holidays", async () => {
  renderWizard();
  await screen.findByText("Realm");
  const calendar = screen.getByLabelText(/^calendar$/i) as HTMLSelectElement;
  expect(calendar.value).toBe("gregorian");
  expect(screen.getByText(/more providers to come/i)).toBeInTheDocument();
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
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1", "GB"));
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
