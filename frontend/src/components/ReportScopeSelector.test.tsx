import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ReportScopeSelector from "./ReportScopeSelector";
import { reportHref } from "../shell/rail";

vi.mock("../api/client", () => ({
  api: { listCampaigns: vi.fn().mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch" },
    { id: "realm", name: "Realm" },
  ]) },
}));

function Probe() {
  const location = useLocation();
  return <><ReportScopeSelector report="stats" cid={null} />
    <output>{location.pathname}</output></>;
}

test("scope selector lists readable campaigns and navigates to the campaign route", async () => {
  render(<MemoryRouter initialEntries={["/stats"]}><Routes>
    <Route path="*" element={<Probe />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole("option", { name: "Saltmarch" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Report campaign"), { target: { value: "saltmarch" } });
  expect(screen.getByText("/campaigns/saltmarch/stats")).toBeInTheDocument();
});

test("missing scope remains selectable and route ids are encoded", async () => {
  render(<MemoryRouter><ReportScopeSelector report="costs" cid="missing" /></MemoryRouter>);
  expect(await screen.findByRole("option", { name: "missing (unavailable)" })).toBeInTheDocument();
  expect(reportHref("todo", "a b")).toBe("/campaigns/a%20b/todo");
});
