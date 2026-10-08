import { render, screen } from "@testing-library/react";
import { InferenceBanner } from "./InferenceBanner";

test("InferenceBanner shows each state", () => {
  // Done: nothing to say, so nothing is drawn.
  const { container, rerender } = render(
    <InferenceBanner status={{ state: "done", reason: "", skipped: [] }} />);
  expect(container).toBeEmptyDOMElement();

  // Newer: this build will not change what a newer one wrote, and says so.
  rerender(<InferenceBanner status={{ state: "newer", reason: "", skipped: [] }} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "This library was upgraded by a newer Grimoire");

  // Failed: the server's own reason, which names the safety backup.
  rerender(<InferenceBanner status={{
    state: "failed", reason: "the safety backup failed: disk full", skipped: [] }} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Upgrade pending: the safety backup failed: disk full");

  // Pending and running: not finished yet, and nothing can be saved meanwhile.
  rerender(<InferenceBanner status={{ state: "pending", reason: "", skipped: [] }} />);
  expect(screen.getByRole("status")).toHaveTextContent("Upgrade pending");
  rerender(<InferenceBanner status={{ state: "running", reason: "", skipped: [] }} />);
  expect(screen.getByRole("status")).toHaveTextContent("Upgrade pending");

  // No status yet (the view has not loaded): nothing.
  rerender(<InferenceBanner status={null} />);
  expect(screen.queryByRole("status")).toBeNull();
});
