import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { MapEditor } from "../MapEditor";
import { ObjectListEditor } from "../ObjectListEditor";

describe("MapEditor", () => {
  it("adds a key/value pair", () => {
    const onChange = vi.fn();
    render(<MapEditor value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText("key"), { target: { value: "boss" } });
    fireEvent.change(screen.getByPlaceholderText("value"), { target: { value: "sir" } });
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith({ boss: "sir" });
  });
});

describe("ObjectListEditor", () => {
  it("adds an empty row and renders fields via renderRow", () => {
    const onChange = vi.fn();
    render(
      <ObjectListEditor
        value={[]}
        fieldKeys={["kind"]}
        onChange={onChange}
        renderRow={(row, patch) => (
          <input
            aria-label="kind"
            value={(row.kind as string) ?? ""}
            onChange={(e) => patch({ ...row, kind: e.target.value })}
          />
        )}
      />,
    );
    fireEvent.click(screen.getByText("+ Add"));
    expect(onChange).toHaveBeenCalledWith([{}]);
  });
});
