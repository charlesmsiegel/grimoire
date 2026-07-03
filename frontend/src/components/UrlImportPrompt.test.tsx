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
