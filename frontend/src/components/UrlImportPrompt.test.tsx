import { render, screen, fireEvent } from "@testing-library/react";
import { UrlImportPrompt } from "./UrlImportPrompt";

test("Add submits trimmed non-empty lines and closes", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.change(screen.getByLabelText("Card URLs"),
    { target: { value: " creator/one \n\n   \ncreator/two\n" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(onSubmit).toHaveBeenCalledWith(["creator/one", "creator/two"]);
  expect(onClose).toHaveBeenCalled();
});

test("Add with no URLs does nothing", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("Cancel closes without submitting", () => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={onSubmit} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
  expect(onClose).toHaveBeenCalled();
  expect(onSubmit).not.toHaveBeenCalled();
});

// Every dialog in the app answers Escape now (#193); this one had no key
// handling at all, so a reader who opened it by mistake had to find Cancel.
test("Escape cancels, even with a URL half typed", () => {
  const onClose = vi.fn();
  render(<UrlImportPrompt onSubmit={vi.fn()} onClose={onClose} />);
  const box = screen.getByLabelText("Card URLs");
  fireEvent.change(box, { target: { value: "creator/one" } });
  box.focus();
  fireEvent.keyDown(box, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});
