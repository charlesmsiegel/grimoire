import { fireEvent, render, screen } from "@testing-library/react";
import { ImageTile } from "./ImageTile";
import type { ImageDescriptor } from "./types";

export const tileImage: ImageDescriptor = { key: "Mara/avatar", label: "Mara primary", alt: "A portrait",
  originalUrl: "/api/art?v=1", thumbnailUrl: "/api/art?w=160&v=1", version: "1", role: "primary" };
test.each([[100, 1000], [1600, 800], [20, 20], [2000, 20]])("tile contains %i by %i art", (width, height) => {
  const open = vi.fn();
  render(<ImageTile image={{ ...tileImage, width, height }} onOpen={open} />);
  const img = screen.getByRole("img", { name: "A portrait" });
  expect(img).toHaveAttribute("loading", "lazy");
  expect(img).toHaveAttribute("src", tileImage.thumbnailUrl);
  fireEvent.click(screen.getByRole("button", { name: "View Mara primary" }));
  expect(open).toHaveBeenCalledWith(screen.getByRole("button"));
});
test("failed thumbnail remains an accessible original viewer entry", () => {
  render(<ImageTile image={tileImage} onOpen={vi.fn()} />);
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByText("Preview unavailable")).toBeVisible();
  expect(screen.getByRole("button")).toBeEnabled();
});
