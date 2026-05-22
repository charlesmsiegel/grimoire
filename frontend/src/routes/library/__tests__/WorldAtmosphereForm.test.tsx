import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { WorldAtmosphereForm } from "../WorldAtmosphereForm";

describe("WorldAtmosphereForm", () => {
  it("renders known fields as labeled inputs and edits propagate", () => {
    const onChange = vi.fn();
    render(
      <WorldAtmosphereForm
        value={{ default_register: "warm", default_palette: "pink" }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/default register/i), {
      target: { value: "cold" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      default_register: "cold",
      default_palette: "pink",
    });
  });

  it("round-trips unknown extra keys via StructuredValueEditor", () => {
    render(
      <WorldAtmosphereForm
        value={{
          default_register: "",
          default_palette: "",
          custom_note: "weather is hot",
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue("weather is hot")).toBeInTheDocument();
  });
});
