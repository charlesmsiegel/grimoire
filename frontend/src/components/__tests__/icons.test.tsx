import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { SpinnerIcon, TrashIcon } from "../icons";

describe("icons", () => {
  it("renders an svg that paints with currentColor and is decorative by default", () => {
    const { container } = render(<TrashIcon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });

  it("forwards size and extra svg props", () => {
    const { container } = render(<TrashIcon size={32} data-testid="x" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("width", "32");
    expect(svg).toHaveAttribute("height", "32");
    expect(svg).toHaveAttribute("data-testid", "x");
  });

  it("can be made a meaningful image via overrides", () => {
    const { container } = render(<TrashIcon aria-hidden={false} role="img" aria-label="Delete" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "Delete");
  });

  it("spins the busy spinner while preserving any caller class", () => {
    const { container } = render(<SpinnerIcon className="extra" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveClass("icon-spin");
    expect(svg).toHaveClass("extra");
  });
});
