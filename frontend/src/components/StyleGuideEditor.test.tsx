import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { StyleGuideEditor } from "./StyleGuideEditor";

vi.mock("../api/client", () => ({
  api: {
    listStyles: vi.fn(), readStyle: vi.fn(), createStyle: vi.fn(),
    updateStyle: vi.fn(), deleteStyle: vi.fn(), duplicateStyle: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
    { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
  ]);
  (api.readStyle as any).mockImplementation((sid: string) => Promise.resolve(
    sid === "gothic-horror"
      ? { meta: { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
          body: "Atmosphere first." }
      : { meta: { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
          body: "Keep it warm." }));
  (api.createStyle as any).mockResolvedValue({ id: "new-style" });
  (api.updateStyle as any).mockResolvedValue({ ok: true });
  (api.deleteStyle as any).mockResolvedValue({ ok: true });
  (api.duplicateStyle as any).mockResolvedValue({ id: "gothic-horror-copy" });
});

test("clicking a style shows a read-only view; built-in shows Duplicate not Edit", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror"));
  expect(screen.getByText("Atmosphere first.")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();
  expect(screen.getByRole("button", { name: /duplicate to customize/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
});

test("a custom style shows Edit, which reveals the form", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("cozy-mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("+ New opens the form directly and creates a style", async () => {
  render(<StyleGuideEditor />);
  await screen.findByRole("button", { name: /new style guide/i });
  fireEvent.click(screen.getByRole("button", { name: /new style guide/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Space Opera" } });
  fireEvent.change(screen.getByLabelText("Guide text"), { target: { value: "Big, bold, cosmic." } });
  fireEvent.click(screen.getByRole("button", { name: /create style guide/i }));
  await waitFor(() => expect(api.createStyle).toHaveBeenCalledWith(
    expect.objectContaining({ name: "Space Opera", body: "Big, bold, cosmic." })));
});

test("duplicating a built-in style opens the new copy for editing", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  (api.readStyle as any).mockResolvedValueOnce({
    meta: { id: "gothic-horror-copy", name: "Gothic Horror (copy)", description: "Dread.", tags: ["horror"], built_in: false },
    body: "Atmosphere first.",
  });
  fireEvent.click(screen.getByRole("button", { name: /duplicate to customize/i }));
  await waitFor(() => expect(api.duplicateStyle).toHaveBeenCalledWith("gothic-horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror-copy"));
});

test("deleting a custom style removes it", async () => {
  const original = window.confirm;
  window.confirm = () => true;
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteStyle).toHaveBeenCalledWith("cozy-mystery"));
  window.confirm = original;
});
