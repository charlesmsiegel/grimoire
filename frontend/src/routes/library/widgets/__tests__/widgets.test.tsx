import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { EnumSelect } from "../EnumSelect";
import { TagsInput } from "../TagsInput";

describe("EnumSelect", () => {
  it("renders options and reports selection", () => {
    const onChange = vi.fn();
    render(
      <EnumSelect
        value="pc"
        options={[
          { value: "pc", label: "PC" },
          { value: "major_npc", label: "Major NPC" },
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "major_npc" } });
    expect(onChange).toHaveBeenCalledWith("major_npc");
  });
});

describe("TagsInput", () => {
  it("adds a tag on Enter", () => {
    const onChange = vi.fn();
    render(<TagsInput value={["a"]} onChange={onChange} />);
    const input = screen.getByPlaceholderText("add tag…");
    fireEvent.change(input, { target: { value: "b" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["a", "b"]);
  });

  it("removes a tag", () => {
    const onChange = vi.fn();
    render(<TagsInput value={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Remove a"));
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
