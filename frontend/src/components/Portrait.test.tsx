import { render, screen, fireEvent } from "@testing-library/react";
import { Portrait, initialsOf } from "./Portrait";

test("initialsOf takes the first letters of the first two words", () => {
  expect(initialsOf("Maren Voss")).toBe("MV");
  expect(initialsOf("odo")).toBe("O");
  expect(initialsOf("Brother Aldous the Grey")).toBe("BA");
});

test("renders the image when src is given", () => {
  render(<Portrait src="/img/a.png" name="Maren Voss" />);
  expect(screen.getByAltText("Maren Voss portrait")).toHaveAttribute("src", "/img/a.png");
});

test("falls back to initials when src is null", () => {
  render(<Portrait src={null} name="Maren Voss" />);
  expect(screen.getByText("MV")).toBeInTheDocument();
});

test("falls back to initials when the image fails to load", () => {
  render(<Portrait src="/img/broken.png" name="Maren Voss" />);
  fireEvent.error(screen.getByAltText("Maren Voss portrait"));
  expect(screen.getByText("MV")).toBeInTheDocument();
});
