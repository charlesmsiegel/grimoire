import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SchemaBuilder } from "../SchemaBuilder";

describe("SchemaBuilder", () => {
  it("adds a field and emits an updated schema", () => {
    const onChange = vi.fn();
    render(
      <SchemaBuilder
        title="Character"
        value={{ type: "object", title: "Character", properties: {} }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /add field/i }));
    const emitted = onChange.mock.calls.at(-1)?.[0];
    expect(emitted.properties).toHaveProperty("field_1");
  });

  it("toggles to raw JSON and back", () => {
    render(
      <SchemaBuilder
        title="C"
        value={{ type: "object", title: "C", properties: {} }}
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /raw json/i }));
    expect(screen.getByRole("textbox", { name: /schema json/i })).toBeInTheDocument();
  });
});
