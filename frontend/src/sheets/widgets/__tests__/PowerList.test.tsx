import { useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { PowerListWidget } from "../PowerList";
import type { PowerItem } from "../../types";

function Harness({ initial }: { initial: PowerItem[] }) {
  const [value, setValue] = useState<ReadonlyArray<PowerItem> | null>(initial);
  return <PowerListWidget property={{}} name="powers" value={value} onChange={setValue} />;
}

describe("PowerListWidget", () => {
  it("keeps each row's DOM identity when an earlier row is removed", () => {
    render(<Harness initial={[{ name: "Alpha" }, { name: "Beta" }, { name: "Gamma" }]} />);

    // Capture the live input node for the last row before mutating the list.
    const gammaBefore = screen.getByDisplayValue("Gamma");

    fireEvent.click(screen.getByRole("button", { name: "Remove Alpha" }));

    expect(screen.queryByDisplayValue("Alpha")).toBeNull();
    // With stable keys the surviving row is the *same* element — input state
    // (focus, cursor, uncontrolled children) does not bleed across the delete.
    expect(screen.getByDisplayValue("Gamma")).toBe(gammaBefore);
    expect(screen.getByDisplayValue("Beta")).toBeInTheDocument();
  });
});
