import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RoutePageRedirect } from "./ModelsRedirect";

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.hash}</div>; }

test("an old route page lands on its task's row, the key encoded once", () => {
  render(
    <MemoryRouter initialEntries={["/models/route/scene%20break"]}>
      <Where />
      <Routes>
        <Route path="/models/route/:key" element={<RoutePageRedirect />} />
        <Route path="/models/edit" element={<div>form</div>} />
      </Routes>
    </MemoryRouter>);
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit#task-scene%20break");
});
