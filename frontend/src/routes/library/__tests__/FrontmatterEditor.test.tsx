import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FrontmatterEditor } from "../FrontmatterEditor";

describe("FrontmatterEditor — no JSON textareas", () => {
  it("renders a nested-object field via StructuredValueEditor, not a textarea", () => {
    const { container } = render(
      <FrontmatterEditor
        value={{ appearance: { hair: "brown", eyes: "green" } }}
        onChange={vi.fn()}
      />,
    );
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByDisplayValue("brown")).toBeInTheDocument();
  });

  it("renders a list field via StructuredValueEditor, not a textarea", () => {
    const { container } = render(
      <FrontmatterEditor value={{ skills: ["sword", "stealth"] }} onChange={vi.fn()} />,
    );
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByDisplayValue("sword")).toBeInTheDocument();
  });

  it("editing inside a nested object propagates the whole frontmatter up", () => {
    const onChange = vi.fn();
    render(
      <FrontmatterEditor value={{ appearance: { hair: "brown" } }} onChange={onChange} />,
    );
    fireEvent.change(screen.getByDisplayValue("brown"), { target: { value: "red" } });
    expect(onChange).toHaveBeenLastCalledWith({ appearance: { hair: "red" } });
  });

  it("Add-field offers list and object (not json)", () => {
    render(<FrontmatterEditor value={{}} onChange={vi.fn()} />);
    const select = screen.getByRole("combobox");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(options).toContain("list");
    expect(options).toContain("object");
    expect(options).not.toContain("json");
  });
});
