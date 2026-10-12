import { fireEvent, render, screen } from "@testing-library/react";
import { DescriptionConflictPanel } from "./DescriptionConflictPanel";

test.each(["shared", "legacy"] as const)("reconciliation identifies the %s write target", (scope) => {
  const onChoose = vi.fn();
  render(<DescriptionConflictPanel snapshot={{ available: true, description_rev: "current",
    effective_description: { scope, text: "A map", described: true, conflicts: [] },
    write_target_description: { scope, text: "A map", described: true, conflicts: [] },
  }} draft="A portrait" onChoose={onChoose} onCancel={() => {}} busy={false} />);
  const shared = scope === "shared";
  expect(screen.getByText(shared ? "Shared description" : "Description for this placement")).toBeVisible();
  if (!shared) expect(screen.queryByText("Shared description")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: shared ? "Use shared value" : "Use this placement's value" }));
  expect(onChoose).toHaveBeenCalledWith("shared");
});
