import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ConfirmDestructiveDialog } from "../ConfirmDestructiveDialog";

describe("ConfirmDestructiveDialog", () => {
  function renderOpen(props: Partial<React.ComponentProps<typeof ConfirmDestructiveDialog>> = {}) {
    return render(
      <ConfirmDestructiveDialog
        open
        title="Delete thing?"
        onConfirm={props.onConfirm ?? vi.fn()}
        onCancel={props.onCancel ?? vi.fn()}
        {...props}
      />,
    );
  }

  it("does not render when open=false", () => {
    render(
      <ConfirmDestructiveDialog
        open={false}
        title="Delete thing?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByText("Delete thing?")).not.toBeInTheDocument();
  });

  it("confirm fires when no dependents and no typed confirmation", () => {
    const onConfirm = vi.fn();
    renderOpen({ onConfirm, dependents: [] });
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("confirm is disabled while dependents=undefined", () => {
    renderOpen({ dependents: undefined, body: "Loading dependents…" });
    expect(screen.getByRole("button", { name: /^Delete$/ })).toBeDisabled();
  });

  it("renders dependent list when populated", () => {
    renderOpen({
      dependents: [
        { id: "c1", name: "First Campaign" },
        { id: "c2", name: "Second Campaign" },
      ],
    });
    expect(screen.getByText("First Campaign")).toBeInTheDocument();
    expect(screen.getByText("Second Campaign")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Delete$/ })).toBeEnabled();
  });

  it("typed confirmation gates the confirm button case-sensitively", () => {
    const onConfirm = vi.fn();
    renderOpen({
      onConfirm,
      dependents: [],
      typedConfirmation: { expected: "sakura-high", label: "Type id to confirm" },
    });
    const input = screen.getByLabelText(/Type id to confirm/);
    const confirm = screen.getByRole("button", { name: /^Delete$/ });
    expect(confirm).toBeDisabled();

    fireEvent.change(input, { target: { value: "Sakura-High" } });
    expect(confirm).toBeDisabled();

    fireEvent.change(input, { target: { value: "sakura-high" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("shows busy state and disables confirm when busy=true", () => {
    renderOpen({ busy: true, dependents: [] });
    const confirm = screen.getByRole("button", { name: /Deleting…/ });
    expect(confirm).toBeDisabled();
  });

  it("renders error inside the dialog and stays open", () => {
    renderOpen({ dependents: [], error: "boom" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("cancel fires onCancel", () => {
    const onCancel = vi.fn();
    renderOpen({ onCancel });
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
