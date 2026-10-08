import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { NeedsModel } from "./NeedsModel";

test("says where a Primary model is chosen, and links there", () => {
  render(<MemoryRouter><NeedsModel /></MemoryRouter>);
  const hint = screen.getByText(/to generate\.$/);
  expect(hint).toHaveTextContent(
    "Choose a provider and a Primary model on the Models page to generate.");
  expect(screen.getByRole("link", { name: "Models page" })).toHaveAttribute("href", "/models");
});

test("names what it is needed for", () => {
  render(<MemoryRouter><NeedsModel action="adapt the greeting" /></MemoryRouter>);
  expect(screen.getByText(/to adapt the greeting\.$/)).toBeInTheDocument();
});

test("outside a router it is the same words, unlinked", () => {
  render(<NeedsModel />);
  expect(screen.getByText(
    "Choose a provider and a Primary model on the Models page to generate.")).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});
