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


test("the pending banner reports archive preparation and copy counts", () => {
  const status = { state: "running" as const, reason: "", skipped: [], active: true,
    progress: { phase: "safety_backup" as const, done: 0, total: null as number | null } };
  const { rerender } = render(<InferenceBanner status={status} />);
  expect(screen.getByRole("status")).toHaveTextContent(/Preparing/);
  rerender(<InferenceBanner status={{ ...status,
    progress: { ...status.progress, done: 3, total: 8 } }} />);
  expect(screen.getByRole("status")).toHaveTextContent(/3 of 8 files/);
});
