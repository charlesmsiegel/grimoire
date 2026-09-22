import { render, screen, fireEvent } from "@testing-library/react";
import { thumbSet } from "../api/thumbs";
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

const set = () => thumbSet((w) => `/img/mara?w=${w}`, "120px");

test("a portrait loads lazily and decodes off the main thread", () => {
  render(<Portrait src="/img/mara" name="Mara" />);
  const img = screen.getByAltText("Mara portrait");
  expect(img.getAttribute("loading")).toBe("lazy");
  expect(img.getAttribute("decoding")).toBe("async");
  expect(img.getAttribute("srcset")).toBeNull();
});

test("a thumbSet renders as src, srcset and sizes", () => {
  render(<Portrait src={set()} name="Mara" />);
  const img = screen.getByAltText("Mara portrait");
  expect(img.getAttribute("src")).toBe("/img/mara?w=512");
  expect(img.getAttribute("srcset")).toContain("/img/mara?w=256 170w");
  expect(img.getAttribute("sizes")).toBe("120px");
});

test("loading, sizes and srcset reach the element before src does", () => {
  // Attributes are set in prop order. Firefox ignores a `loading="lazy"` set
  // after `src`, and an engine that fetches as `src` lands picks without the
  // srcset -- so a portrait that set `src` first was neither lazy nor sized.
  render(<Portrait src={set()} name="Mara" />);
  const names = screen.getByAltText("Mara portrait").getAttributeNames();
  const at = (n: string) => names.indexOf(n);
  expect(at("loading")).toBeLessThan(at("src"));
  expect(at("sizes")).toBeLessThan(at("src"));
  expect(at("srcset")).toBeLessThan(at("src"));
});

test("a broken thumbSet stays initials across renders that rebuild it", () => {
  // The set is a fresh object every render. Keyed on the object, the reset
  // effect would clear `broken` straight after the error set it, and a 404ing
  // portrait would re-request itself forever.
  const { rerender } = render(<Portrait src={set()} name="Mara Winifred" />);
  fireEvent.error(screen.getByAltText("Mara Winifred portrait"));
  expect(screen.getByText("MW")).toBeInTheDocument();
  rerender(<Portrait src={set()} name="Mara Winifred" />);
  expect(screen.queryByAltText("Mara Winifred portrait")).toBeNull();
  // ...while a different picture is a fresh chance to load.
  rerender(<Portrait src={thumbSet((w) => `/img/winifred?w=${w}`, "120px")} name="Mara Winifred" />);
  expect(screen.getByAltText("Mara Winifred portrait")).toBeInTheDocument();
});
