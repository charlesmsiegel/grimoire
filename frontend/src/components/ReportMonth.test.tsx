import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import ReportMonth, { shiftMonth, useReportMonth } from "./ReportMonth";

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

test("month choices show the newest month first", () => {
  render(<ReportMonth month="2026-09" available={["2026-07", "2026-08", "2026-09"]}
    onChange={() => {}} />);
  const choices = screen.getAllByRole("option").map((option) => option.textContent);
  expect(choices).toEqual(["2026-09", "2026-08", "2026-07"]);
});
