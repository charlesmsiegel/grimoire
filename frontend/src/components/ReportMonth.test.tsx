import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { shiftMonth, useReportMonth } from "./ReportMonth";

test("month navigation crosses calendar years", () => {
  expect(shiftMonth("2025-12", 1)).toBe("2026-01");
  expect(shiftMonth("2026-01", -1)).toBe("2025-12");
});

function Probe() {
  const [month] = useReportMonth();
  const location = useLocation();
  return <output>{month} {location.search}</output>;
}

test("an explicit month remains in the shareable URL", () => {
  render(<MemoryRouter initialEntries={["/costs?month=2025-12"]}><Probe /></MemoryRouter>);
  expect(screen.getByText("2025-12 ?month=2025-12")).toBeInTheDocument();
});
