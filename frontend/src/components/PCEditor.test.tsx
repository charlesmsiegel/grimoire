import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(), createCampaignPC: vi.fn(),
      listPCs: vi.fn(), listTags: vi.fn(), readPC: vi.fn(), createPC: vi.fn(),
      updatePC: vi.fn(), deletePC: vi.fn(), createPCVersion: vi.fn(), updatePCVersion: vi.fn(),
      getCalendarMonths: vi.fn(),
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

const DETAIL = {
  meta: { id: "elara", name: "Elara", tags: ["student"], default_version: "default" },
  versions: [{ id: "default", name: "default", persona: { name: "Elara", pronouns: "she/her", summary: "scholar", birthdate: "", description: "a wanderer" } }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listTags as any).mockResolvedValue({ student: "Student" });
  (api.readPC as any).mockResolvedValue(DETAIL);
  (api.createPC as any).mockResolvedValue({ pc: "rook", version: "default" });
  (api.updatePC as any).mockResolvedValue({ ok: true });
  (api.updatePCVersion as any).mockResolvedValue({ ok: true });
  (api.createPCVersion as any).mockResolvedValue({ version: "young" });
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
});

test("clicking a PC shows a read-only view; Edit reveals the form", async () => {
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByText("a wanderer");                         // rendered description
  expect(container.querySelector("textarea")).toBeNull();        // read-only
  expect(screen.getByText("she/her")).toBeInTheDocument();       // sidebar metadata
  expect(screen.getByText("scholar")).toBeInTheDocument();
  expect(screen.getByText("Student")).toBeInTheDocument();       // tag chip

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(container.querySelector("textarea")).not.toBeNull();    // form revealed
});

test("saving the persona returns to the read-only view", async () => {
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() => expect(container.querySelector("textarea")).toBeNull());
});

test("creating a PC prompts for a name and opens the form directly", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  const { container } = render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  await screen.findByText("Elara");
  fireEvent.click(screen.getByRole("button", { name: /new pc/i }));
  await waitFor(() => expect(api.createPC).toHaveBeenCalledWith("w", { name: "Rook" }));
  await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull()); // straight to the form
});

test("editing persona saves the selected version", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "a sage" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", "default",
      expect.objectContaining({ description: "a sage" })),
  );
});

test("editing the birthdate saves it on the persona", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Birthdate year"), { target: { value: "1990" } });
  const monthSelect = await screen.findByLabelText("Birthdate month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "06" } });
  const daySelect = screen.getByLabelText("Birthdate day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "29" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", "default",
      expect.objectContaining({ birthdate: "1990-06-29" })),
  );
});

test("toggling a tag chip in the form updates the PC tags", async () => {
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(await screen.findByRole("button", { name: "Student" }));
  await waitFor(() => expect(api.updatePC).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara", { tags: [] }));
});

test("adding a version prompts and posts the current persona", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Young");
  render(<PCEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.click(screen.getByRole("button", { name: /\+ version/i }));
  await waitFor(() =>
    expect(api.createPCVersion).toHaveBeenCalledWith({ kind: "world", id: "w" }, "elara",
      expect.objectContaining({ name: "Young" })),
  );
});


const PC_DETAIL = {
  meta: { id: "elara", name: "Elara", tags: [], default_version: "young" },
  versions: [
    { id: "young", name: "Young", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "d" } },
    { id: "older", name: "Older", persona: { name: "Elara", pronouns: "", summary: "", birthdate: "", description: "d2" } },
  ],
};

test("campaign scope: picking a version confirms and calls pickVersion", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  (api.readPC as any).mockResolvedValue(PC_DETAIL);
  (api.listAppearances as any).mockResolvedValue([]);          // unlocked
  (api.pickVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "Elara" }));
  fireEvent.click(await screen.findByRole("button", { name: "Pick this version" }));
  await waitFor(() => expect(api.pickVersion).toHaveBeenCalledWith("run", "pcs", "elara", "young"));
});

test("campaign scope: a locked PC offers import from world", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [],
    default_version: "young", versions: [] }]);
  (api.readPC as any).mockImplementation(async (scope: any) =>
    scope.kind === "campaign" ? { ...PC_DETAIL, versions: [PC_DETAIL.versions[0]] } : PC_DETAIL);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "pcs", id: "elara", version: "young", role: "player", scenes: [] },
  ]);
  (api.importVersion as any).mockResolvedValue({ ok: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PCEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: "Elara" }));
  expect(await screen.findByText(/locked to/i)).toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText("Import version"), { target: { value: "older" } });
  fireEvent.click(screen.getByRole("button", { name: "Import from world" }));
  await waitFor(() => expect(api.importVersion).toHaveBeenCalledWith("run", "pcs", "elara", "older"));
});
