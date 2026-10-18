import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TagEditor } from "./TagEditor";

vi.mock("../api/client", () => ({
  api: { listTags: vi.fn(), addTag: vi.fn(), renameTag: vi.fn(), deleteTag: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listTags).mockResolvedValue({});
  vi.mocked(api.addTag).mockResolvedValue({ id: "student" });
  vi.mocked(api.renameTag).mockResolvedValue({ id: "student", name: "Pupil" });
  vi.mocked(api.deleteTag).mockResolvedValue({ ok: true });
});

test("adds a tag", async () => {
  render(<TagEditor wid="w" />);
  await waitFor(() => expect(api.listTags).toHaveBeenCalledWith("w"));
  fireEvent.change(screen.getByPlaceholderText(/tag name/i), { target: { value: "Student" } });
  fireEvent.click(screen.getByRole("button", { name: /add tag/i }));
  await waitFor(() => expect(api.addTag).toHaveBeenCalledWith("w", "Student"));
});

test("renames and deletes a tag", async () => {
  vi.mocked(api.listTags).mockResolvedValue({ student: "Student" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<TagEditor wid="w" />);
  await screen.findByText("Student");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Student");
  fireEvent.change(input, { target: { value: "Pupil" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameTag).toHaveBeenCalledWith("w", "student", "Pupil"));
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteTag).toHaveBeenCalledWith("w", "student"));
});
