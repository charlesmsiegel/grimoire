import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Conditions from "./Conditions";

function renderConditions(props: Partial<Parameters<typeof Conditions>[0]> = {}) {
  return render(
    <MemoryRouter>
      <Conditions cid="saltmarch" worldName="Saltmarch"
                  location={{ current: { id: "tideflats", name: "The Tideflats" }, visited: [] }}
                  datetime={{
                    current: { native: "1183-07-04", friendly: "4 Reaping 1183",
                               weekday: "Marsday", secondary_friendly: null,
                               holidays_today: [], upcoming: null, cast: [] },
                    history: [], suggested: null,
                  } as any}
                  weather={{ weather: { condition: "Low fog", temperature: "Cold", wind: "Still" },
                             location: null, native: null } as any}
                  {...props} />
    </MemoryRouter>,
  );
}

test("names where, when and the sky", () => {
  renderConditions();
  expect(screen.getByText("The Tideflats")).toBeInTheDocument();
  expect(screen.getByText(/Marsday 4 Reaping 1183/)).toBeInTheDocument();
  expect(screen.getByText(/Low fog · Cold · Still/)).toBeInTheDocument();
});

test("an unset condition is dropped, not dashed", () => {
  // A scene with no location has not been placed yet; "WHERE —" claims it has
  // been placed nowhere.
  renderConditions({ location: null });
  expect(screen.queryByText("Where")).not.toBeInTheDocument();
  expect(screen.getByText("When")).toBeInTheDocument();
});

test("a scene with nothing set at all says so", () => {
  renderConditions({ location: null, datetime: null, weather: null });
  expect(screen.getByText(/no place or time set/i)).toBeInTheDocument();
});

test("the world link says it is the campaign's own copy before the click", () => {
  renderConditions();
  const link = screen.getByRole("link", { name: /this campaign/i });
  expect(link).toHaveAttribute("href", "/campaigns/saltmarch/world");
  expect(link).toHaveTextContent("Saltmarch · this campaign's");
});

test("a campaign with no world name still offers the link", () => {
  renderConditions({ worldName: "" });
  expect(screen.getByRole("link", { name: /this campaign/i }))
    .toHaveAttribute("href", "/campaigns/saltmarch/world");
});
