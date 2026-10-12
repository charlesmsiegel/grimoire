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

test("preview can open the viewer while View art opens the panel", () => {
  const preview = vi.fn(), open = vi.fn();
  render(<MobileArtEntry name="Mara" image={() => "/api/art"} onOpenPreview={preview} onOpenArt={open} />);
  const button = screen.getByRole("button", { name: "View Mara art" });
  fireEvent.click(button);
  expect(preview).toHaveBeenCalledWith(button);
  expect(open).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "View art" }));
  expect(open).toHaveBeenCalledOnce();
});
test("empty preview opens browsing even when a viewer action is supplied", () => {
  const open = vi.fn(), preview = vi.fn();
  render(<MobileArtEntry name="Mara" image={null} onOpenPreview={preview} onOpenArt={open} />);
  fireEvent.click(screen.getByRole("button", { name: "View Mara art" }));
  expect(open).toHaveBeenCalledOnce(); expect(preview).not.toHaveBeenCalled();
});

test("phone preview honors the effective placement crop", () => {
  render(<MobileArtEntry name="Mara" image={() => "/api/art"} focus={75} onOpenArt={vi.fn()} />);
  expect(screen.getByRole("img", { name: "Mara art preview" })).toHaveStyle({ objectPosition: "75% 75%" });
});
