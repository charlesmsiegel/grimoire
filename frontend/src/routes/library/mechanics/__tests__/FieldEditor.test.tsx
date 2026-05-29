import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FieldEditor } from "../FieldEditor";
import type { FieldModel } from "../schemaModel";

const base: FieldModel = { key: "str", widget: "dot-rating", required: false, config: {} };

describe("FieldEditor", () => {
  it("edits a numeric widget config field", () => {
    const onChange = vi.fn();
    render(<FieldEditor field={base} onChange={onChange} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/max dots/i), { target: { value: "5" } });
    const next = onChange.mock.calls.at(-1)?.[0] as FieldModel;
    expect(next.config.max).toBe(5);
  });

  it("changing widget resets to that widget", () => {
    const onChange = vi.fn();
    render(<FieldEditor field={base} onChange={onChange} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/widget/i), { target: { value: "boolean" } });
    const next = onChange.mock.calls.at(-1)?.[0] as FieldModel;
    expect(next.widget).toBe("boolean");
  });
});
