import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { JsonField } from "../JsonField";

describe("JsonField", () => {
  it("does not revert intermediate invalid JSON and parses on blur", () => {
    const onChange = vi.fn();
    render(<JsonField value={{}} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: '{"a":' } }); // intermediate, invalid
    expect((ta as HTMLTextAreaElement).value).toBe('{"a":'); // not reverted
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(ta, { target: { value: '{"a":1}' } });
    fireEvent.blur(ta);
    expect(onChange).toHaveBeenCalledWith({ a: 1 });
  });

  it("shows an inline error for invalid JSON on blur and does not call onChange", () => {
    const onChange = vi.fn();
    render(<JsonField value={{}} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "{nope" } });
    fireEvent.blur(ta);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
  });

  it("treats empty text as undefined", () => {
    const onChange = vi.fn();
    render(<JsonField value={{ a: 1 }} onChange={onChange} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "  " } });
    fireEvent.blur(ta);
    expect(onChange).toHaveBeenCalledWith(undefined);
  });
});
