import { fireEvent, render, screen } from "@testing-library/react";
import { CurrentArtViewer } from "./CurrentArtViewer";

const image = (name: string) => ({ name, url: (width?: number) =>
  `/art/${name}${width ? `?w=${width}` : ""}` });

test("the avatar opens large, with the original file linked", () => {
  render(<CurrentArtViewer label="Mara" images={[image("avatar"), image("gallery_1")]} />);
  expect(screen.getByRole("img", { name: "Mara avatar" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open original Mara avatar" }))
    .toHaveAttribute("href", "/art/avatar");
  fireEvent.click(screen.getByRole("button", { name: "View gallery_1" }));
  expect(screen.getByRole("link", { name: "Open original Mara gallery_1" }))
    .toHaveAttribute("href", "/art/gallery_1");
});

test("a gallery-only version opens its first image and changes versions cleanly", () => {
  const { rerender } = render(<CurrentArtViewer label="Mara" images={[image("gallery_1")]} />);
  expect(screen.getByRole("img", { name: "Mara gallery_1" })).toBeInTheDocument();
  rerender(<CurrentArtViewer label="Mara" images={[image("avatar")]} />);
  expect(screen.getByRole("img", { name: "Mara avatar" })).toBeInTheDocument();
  rerender(<CurrentArtViewer label="Mara" images={[]} />);
  expect(screen.getByText("No art for this version yet.")).toBeInTheDocument();
});
