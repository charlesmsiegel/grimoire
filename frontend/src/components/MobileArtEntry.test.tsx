import { fireEvent, render, screen } from "@testing-library/react";
import { MobileArtEntry } from "./MobileArtEntry";

test("gallery-only art offers a visible preview and direct action", () => {
  const open = vi.fn();
  render(<MobileArtEntry name="Mara" image={() => "/art/gallery_1"} onOpenArt={open} />);
  expect(screen.getByRole("img", { name: "Mara art preview" })).toHaveAttribute("src", "/art/gallery_1");
  fireEvent.click(screen.getByRole("button", { name: "View art" }));
  expect(open).toHaveBeenCalledOnce();
});

test("the Art action remains available before the first image", () => {
  const open = vi.fn();
  render(<MobileArtEntry name="Mara" image={null} onOpenArt={open} />);
  fireEvent.click(screen.getByRole("button", { name: "View art" }));
  expect(open).toHaveBeenCalledOnce();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
