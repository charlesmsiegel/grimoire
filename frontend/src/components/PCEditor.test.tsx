import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", () => ({
  api: {
    listPCs: vi.fn(), listTags: vi.fn(), readPC: vi.fn(), createPC: vi.fn(),
    updatePC: vi.fn(), deletePC: vi.fn(), createPCVersion: vi.fn(), updatePCVersion: vi.fn(),
  },
}));
import { api } from "../api/client";

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
});

test("clicking a PC shows a read-only view; Edit reveals the form", async () => {
  const { container } = render(<PCEditor wid="w" />);
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
  const { container } = render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() => expect(container.querySelector("textarea")).toBeNull());
});

test("creating a PC prompts for a name and opens the form directly", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  const { container } = render(<PCEditor wid="w" />);
  await screen.findByText("Elara");
  fireEvent.click(screen.getByRole("button", { name: /new pc/i }));
  await waitFor(() => expect(api.createPC).toHaveBeenCalledWith("w", { name: "Rook" }));
  await waitFor(() => expect(container.querySelector("textarea")).not.toBeNull()); // straight to the form
});

test("editing persona saves the selected version", async () => {
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "a sage" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith("w", "elara", "default",
      expect.objectContaining({ description: "a sage" })),
  );
});

test("editing the birthdate saves it on the persona", async () => {
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Birthdate"), { target: { value: "1990-06-29" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith("w", "elara", "default",
      expect.objectContaining({ birthdate: "1990-06-29" })),
  );
});

test("toggling a tag chip in the form updates the PC tags", async () => {
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(await screen.findByRole("button", { name: "Student" }));
  await waitFor(() => expect(api.updatePC).toHaveBeenCalledWith("w", "elara", { tags: [] }));
});

test("adding a version prompts and posts the current persona", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Young");
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByLabelText("Description");
  fireEvent.click(screen.getByRole("button", { name: /\+ version/i }));
  await waitFor(() =>
    expect(api.createPCVersion).toHaveBeenCalledWith("w", "elara",
      expect.objectContaining({ name: "Young" })),
  );
});
