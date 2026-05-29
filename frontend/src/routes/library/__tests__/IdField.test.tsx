import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { IdField } from "../IdField";

describe("IdField", () => {
  it("derives id from name until id is edited", () => {
    const onName = vi.fn();
    const onId = vi.fn();
    render(<IdField nameLabel="Name" name="" id="" onNameChange={onName} onIdChange={onId} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
    expect(onName).toHaveBeenCalledWith("Ravenmark");
    expect(onId).toHaveBeenCalledWith("ravenmark");
  });

  it("stops auto-syncing once the id is manually edited", () => {
    const onId = vi.fn();
    function Harness() {
      const [name, setName] = useState("");
      const [id, setId] = useState("");
      return (
        <IdField
          nameLabel="Name"
          name={name}
          id={id}
          onNameChange={setName}
          onIdChange={(v) => {
            setId(v);
            onId(v);
          }}
        />
      );
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("ID"), { target: { value: "custom" } });
    onId.mockClear();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
    expect(onId).not.toHaveBeenCalledWith("ravenmark");
  });
});
