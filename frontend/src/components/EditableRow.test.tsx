import { render, screen, fireEvent } from "@testing-library/react";
import { EditableRow } from "./EditableRow";

test("clicking the label selects", () => {
  const onSelect = vi.fn();
  render(<EditableRow label="Run" onSelect={onSelect} onRename={() => {}} onDelete={() => {}} />);
  fireEvent.click(screen.getByText("Run"));
  expect(onSelect).toHaveBeenCalled();
});

test("rename flow calls onRename with the new value on Enter", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Old" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onRename).toHaveBeenCalledWith("New");
});

test("renaming to the same value does not call onRename", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Same" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Same");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onRename).not.toHaveBeenCalled();
});

test("Escape cancels the rename without calling onRename", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Old" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect(onRename).not.toHaveBeenCalled();
  expect(screen.getByText("Old")).toBeInTheDocument();
});

test("locked disables rename and delete but still selects", () => {
  const onRename = vi.fn();
  const onDelete = vi.fn();
  const onSelect = vi.fn();
  render(<EditableRow label="Busy" locked lockedReason="Not while this scene is generating"
                      onSelect={onSelect} onRename={onRename} onDelete={onDelete} />);
  const rename = screen.getByRole("button", { name: /rename/i });
  expect(rename).toBeDisabled();
  expect(rename).toHaveAttribute("title", "Not while this scene is generating");
  expect(screen.getByRole("button", { name: /delete/i })).toBeDisabled();
  fireEvent.click(rename);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(onRename).not.toHaveBeenCalled();
  expect(onDelete).not.toHaveBeenCalled();
  // reading a row is never what makes a write unsafe
  fireEvent.click(screen.getByText("Busy"));
  expect(onSelect).toHaveBeenCalled();
});

test("delete calls onDelete", () => {
  const onDelete = vi.fn();
  render(<EditableRow label="Doomed" onRename={() => {}} onDelete={onDelete} />);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(onDelete).toHaveBeenCalled();
});
