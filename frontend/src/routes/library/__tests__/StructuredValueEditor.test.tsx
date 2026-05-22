import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { StructuredValueEditor } from "../StructuredValueEditor";

describe("StructuredValueEditor — scalars", () => {
  it("string renders as text input; typing fires onChange with the new string", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value="hello" onChange={onChange} />);
    const input = screen.getByDisplayValue("hello");
    fireEvent.change(input, { target: { value: "hi" } });
    expect(onChange).toHaveBeenLastCalledWith("hi");
  });

  it("number renders as number input; typing fires onChange with a number", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={42} onChange={onChange} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "7" } });
    expect(onChange).toHaveBeenLastCalledWith(7);
  });

  it("boolean renders as a checkbox", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={true} onChange={onChange} />);
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it("null shows (empty) placeholder and a type picker that initializes a default", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={null} onChange={onChange} />);
    expect(screen.getByText(/\(empty\)/)).toBeInTheDocument();
    const picker = screen.getByLabelText(/Type/);
    fireEvent.change(picker, { target: { value: "text" } });
    expect(onChange).toHaveBeenLastCalledWith("");

    onChange.mockClear();
    fireEvent.change(picker, { target: { value: "list" } });
    expect(onChange).toHaveBeenLastCalledWith([]);

    onChange.mockClear();
    fireEvent.change(picker, { target: { value: "object" } });
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("readOnly disables scalar inputs", () => {
    render(<StructuredValueEditor value="x" onChange={vi.fn()} readOnly />);
    expect(screen.getByDisplayValue("x")).toHaveAttribute("readonly");
  });
});

describe("StructuredValueEditor — arrays", () => {
  it("list renders numbered rows and a single + add item button", () => {
    render(<StructuredValueEditor value={["a", "b"]} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("a")).toBeInTheDocument();
    expect(screen.getByDisplayValue("b")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add item/i })).toBeInTheDocument();
  });

  it("clicking + add item appends a null row", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add item/i }));
    expect(onChange).toHaveBeenLastCalledWith(["a", null]);
  });

  it("per-row delete button removes that item", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a", "b", "c"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove item 2$/ }));
    expect(onChange).toHaveBeenLastCalledWith(["a", "c"]);
  });

  it("editing an item bubbles the new array up", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={["a", "b"]} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("b"), { target: { value: "B" } });
    expect(onChange).toHaveBeenLastCalledWith(["a", "B"]);
  });

  it("nested object inside a list propagates edits", () => {
    const onChange = vi.fn();
    render(
      <StructuredValueEditor
        value={[{ name: "January", days: 31 }]}
        onChange={onChange}
      />,
    );
    const daysInput = screen.getByDisplayValue("31");
    fireEvent.change(daysInput, { target: { value: "30" } });
    expect(onChange).toHaveBeenLastCalledWith([{ name: "January", days: 30 }]);
  });
});

describe("StructuredValueEditor — objects", () => {
  it("object renders one row per key with value editors", () => {
    render(<StructuredValueEditor value={{ hair: "brown", eyes: "green" }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("brown")).toBeInTheDocument();
    expect(screen.getByDisplayValue("green")).toBeInTheDocument();
    expect(screen.getByDisplayValue("hair")).toBeInTheDocument();
    expect(screen.getByDisplayValue("eyes")).toBeInTheDocument();
  });

  it("typing then clicking + add field appends a null-valued key", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1" }} onChange={onChange} />);
    const pending = screen.getByPlaceholderText(/add field/i);
    fireEvent.change(pending, { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: /add field/i }));
    expect(onChange).toHaveBeenLastCalledWith({ a: "1", b: null });
  });

  it("typing a new key fires onChange with the renamed key", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("a"), { target: { value: "alpha" } });
    expect(onChange).toHaveBeenLastCalledWith({ alpha: "1" });
  });

  it("editing a value bubbles the new object up", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ hair: "brown" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("brown"), { target: { value: "red" } });
    expect(onChange).toHaveBeenLastCalledWith({ hair: "red" });
  });

  it("per-row delete removes that key", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1", b: "2" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove field a$/ }));
    expect(onChange).toHaveBeenLastCalledWith({ b: "2" });
  });

  it("renaming a key to a duplicate shows an error hint and does not fire onChange", () => {
    const onChange = vi.fn();
    render(<StructuredValueEditor value={{ a: "1", b: "2" }} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("b"), { target: { value: "a" } });
    expect(screen.getByText(/already exists/i)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
