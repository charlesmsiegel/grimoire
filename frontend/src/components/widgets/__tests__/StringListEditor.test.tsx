import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { StringListEditor } from "../StringListEditor";

describe("StringListEditor", () => {
  it("adds an empty row on +Add", () => {
    const onChange = vi.fn();
    render(<StringListEditor label="Samples" value={["hi"]} onChange={onChange} />);
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith(["hi", ""]);
  });

  it("removes a row", () => {
    const onChange = vi.fn();
    render(<StringListEditor label="Samples" value={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Remove")[0]!);
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
