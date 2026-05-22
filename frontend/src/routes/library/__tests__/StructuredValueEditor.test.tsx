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
