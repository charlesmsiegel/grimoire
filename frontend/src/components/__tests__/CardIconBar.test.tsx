import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CardIconBar } from "../CardIconBar";
import { deleteAction } from "../cardActions";

describe("CardIconBar", () => {
  it("renders an action as an icon button with an accessible name and fires onClick", () => {
    const onClick = vi.fn();
    render(<CardIconBar actions={[deleteAction({ onClick, label: "Delete world" })]} />);
    const btn = screen.getByRole("button", { name: "Delete world" });
    expect(btn).toHaveAttribute("title", "Delete world");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables the button while busy", () => {
    render(<CardIconBar actions={[deleteAction({ onClick: () => {}, busy: true })]} />);
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("renders an empty toolbar with no buttons when actions is empty", () => {
    render(<CardIconBar actions={[]} />);
    expect(screen.getByRole("toolbar", { name: "Card actions" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
