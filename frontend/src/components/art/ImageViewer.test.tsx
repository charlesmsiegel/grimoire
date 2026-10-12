import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ImageViewer } from "./ImageViewer";
import { activeHotkeys } from "../../shortcuts/registry";
import { useHotkeys } from "../../shortcuts/useHotkeys";
import { TestPointerEvent } from "../../testkit/pointer";
import type { ImageDescriptor } from "./types";

const images: ImageDescriptor[] = ["avatar", "gallery_2", "gallery_10"].map((name) => ({
  key: name, originalUrl: `/api/${name}?v=1`, thumbnailUrl: `/api/${name}?w=160`, version: "1",
  label: name, alt: name, imageId: "shared", width: 1600, height: 800,
  downloadUrl: `/api/${name}?download=1`, recordDestination: "/worlds/Realm/characters/Mara?tab=art",
}));
test("placement identity, bounded steps, and active original only", () => {
  render(<ImageViewer images={images} initialKey="gallery_2" onClose={vi.fn()} />);
  expect(screen.getAllByRole("img", { hidden: true })).toHaveLength(1);
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", images[1].originalUrl);
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", images[2].originalUrl);
  expect(screen.getByRole("button", { name: "Next image" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("href", images[2].downloadUrl);
});
test("natural zoom, max 400%, and byte replacement resets", () => {
  const view = render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "100%" }));
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "1");
  for (let n = 0; n < 20; n++) fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "4");
  view.rerender(<ImageViewer images={[{ ...images[0], version: "2", originalUrl: "/api/avatar?v=2" }]} initialKey="avatar" onClose={vi.fn()} />);
  expect(Number(screen.getByTestId("viewer-image").getAttribute("data-scale"))).toBeLessThan(1);
});
test("failed retry keeps the selected original and closes removed selection", async () => {
  const close = vi.fn();
  const view = render(<ImageViewer images={images} initialKey="gallery_2" onClose={close} />);
  fireEvent.error(screen.getByTestId("viewer-image"));
  fireEvent.click(screen.getByRole("button", { name: "Retry original" }));
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", images[1].originalUrl);
  fireEvent.error(screen.getByTestId("viewer-image"));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", images[1].originalUrl);
  view.rerender(<ImageViewer images={[images[0]]} initialKey="gallery_2" onClose={close} />);
  await waitFor(() => expect(close).toHaveBeenCalled());
});
test("tiny image fit never upscales and one image has no stepping", () => {
  render(<ImageViewer images={[{ ...images[0], width: 20, height: 20 }]} initialKey="avatar" onClose={vi.fn()} />);
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "1");
  expect(screen.queryByRole("button", { name: "Next image" })).not.toBeInTheDocument();
});
test("viewer Escape owns the top modal and restores opener and scroll", () => {
  const underlying = vi.fn(), close = vi.fn();
  function Drawer() { useHotkeys([{ keys: "escape", run: underlying }], { modal: true }); return <button>Mara art</button>; }
  const drawer = render(<Drawer />);
  const opener = screen.getByRole("button"); opener.focus();
  const viewer = render(<ImageViewer images={images} initialKey="avatar" opener={opener} onClose={close} />);
  expect(document.body.style.overflow).toBe("hidden");
  expect(drawer.container).toHaveAttribute("inert");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce(); expect(underlying).not.toHaveBeenCalled();
  viewer.unmount();
  expect(opener).toHaveFocus(); expect(drawer.container).not.toHaveAttribute("inert");
  expect(document.body.style.overflow).not.toBe("hidden");
});

test("drag and two-pointer pinch do not step and remain bounded", () => {
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  try {
    render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    const stage = screen.getByTestId("viewer-stage");
    fireEvent.pointerDown(stage, { pointerId: 1, pointerType: "touch", clientX: 200, clientY: 200 });
    fireEvent.pointerMove(stage, { pointerId: 1, pointerType: "touch", clientX: 250, clientY: 230 });
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-pan", "50,30");
    fireEvent.pointerDown(stage, { pointerId: 2, pointerType: "touch", clientX: 350, clientY: 230 });
    fireEvent.pointerMove(stage, { pointerId: 2, pointerType: "touch", clientX: 450, clientY: 230 });
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "2");
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", images[0].originalUrl);
    fireEvent.pointerUp(stage, { pointerId: 1 });
    fireEvent.pointerUp(stage, { pointerId: 2 });
  } finally { vi.unstubAllGlobals(); }
});
test("keyboard zoom mirrors controls and Tab stays in the viewer", () => {
  render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} />);
  fireEvent.keyDown(window, { key: "1" });
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "1");
  fireEvent.keyDown(window, { key: "+" });
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "1.25");
  const close = screen.getByRole("button", { name: "Close image viewer" });
  close.focus(); fireEvent.keyDown(close, { key: "Tab" });
  expect(screen.getByRole("button", { name: "Fit" })).toHaveFocus();
  fireEvent.keyDown(window, { key: "f" });
  expect(Number(screen.getByTestId("viewer-image").getAttribute("data-scale"))).toBeLessThan(1);
});

test("keyboard can reach collapsible image information", () => {
  render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} />);
  const download = screen.getByRole("link", { name: "Download original" });
  download.focus(); fireEvent.keyDown(download, { key: "Tab" });
  expect(screen.getByText("Image information")).toHaveFocus();
});

test("removing the selected placement closes with a distinguishable unavailable reason", async () => {
  const close = vi.fn();
  const view = render(<ImageViewer images={images} initialKey="avatar" onClose={close} />);
  view.rerender(<ImageViewer images={[]} initialKey="avatar" onClose={close} />);
  await waitFor(() => expect(close).toHaveBeenCalledWith("unavailable"));
});

test.each([[4000, 2000], [200, 4000]])("a %s x %s original is centered independently of its intrinsic grid size", (width, height) => {
  render(<ImageViewer images={[{ ...images[0], width, height }]} initialKey="avatar" onClose={vi.fn()} />);
  const img = screen.getByTestId("viewer-image");
  expect(img.style.position).toBe("absolute");
  const centerX = width / 2 + parseFloat(img.style.marginLeft);
  const centerY = height / 2 + parseFloat(img.style.marginTop);
  expect(centerX).toBe(0);
  expect(centerY).toBe(0);
  expect(img.style.left).toBe("50%");
  expect(img.style.top).toBe("50%");
});


test("viewer actions are discoverable in the active shortcut registry", () => {
  render(<ImageViewer images={images} initialKey="gallery_2" onClose={vi.fn()} />);
  const keys = activeHotkeys().filter(row => row.reachable).map(row => row.key);
  for (const chord of ["escape", "arrowleft", "arrowright", "f", "1", "+"]) {
    expect(keys.find(key => key.keys === chord)).toMatchObject({ label: expect.any(String), group: "IMAGE VIEWER" });
  }
});

test("viewer position is announced after keyboard stepping", () => {
  render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} />);
  expect(screen.getByRole("status", { name: "Image position" })).toHaveTextContent("1 / 3");
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(screen.getByRole("status", { name: "Image position" })).toHaveTextContent("2 / 3");
  expect(screen.getByRole("status", { name: "Image position" })).toHaveAttribute("aria-live", "polite");
});

test("a larger viewport clamps retained zoom to the new Fit minimum", () => {
  let size = 400;
  const measure = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width: size, height: size, x: 0, y: 0, top: 0, left: 0, right: size, bottom: size, toJSON: () => ({}) }));
  try {
    render(<ImageViewer images={[images[0]]} initialKey="avatar" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "0.3125");
    size = 800;
    fireEvent(window, new Event("resize"));
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "0.5");
    size = 400;
    fireEvent(window, new Event("resize"));
    expect(screen.getByTestId("viewer-image")).toHaveAttribute("data-scale", "0.5");
  } finally { measure.mockRestore(); }
});


test.each([1, 1.25])("original loading preserves explicit %s zoom and valid pan", (scale) => {
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  try {
    render(<ImageViewer images={[images[0]]} initialKey="avatar" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    if (scale > 1) fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    const stage = screen.getByTestId("viewer-stage");
    fireEvent.pointerDown(stage, { pointerId: 1, clientX: 200, clientY: 200 });
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 250, clientY: 230 });
    fireEvent.pointerUp(stage, { pointerId: 1 });
    const img = screen.getByTestId("viewer-image");
    Object.defineProperties(img, { naturalWidth: { value: 1600 }, naturalHeight: { value: 800 } });
    fireEvent.load(img);
    expect(img).toHaveAttribute("data-scale", String(scale));
    expect(img).toHaveAttribute("data-pan", "50,30");
  } finally { vi.unstubAllGlobals(); }
});

test("an unknown original's dimensions update Fit without resetting explicit zoom", () => {
  const view = render(<ImageViewer images={[{ ...images[0], width: undefined, height: undefined }]}
    initialKey="avatar" onClose={vi.fn()} />);
  const img = screen.getByTestId("viewer-image");
  Object.defineProperties(img, { naturalWidth: { value: 1600 }, naturalHeight: { value: 800 } });
  fireEvent.load(img);
  const fit = Number(img.getAttribute("data-scale"));
  expect(fit).toBeLessThan(1);
  fireEvent.click(screen.getByRole("button", { name: "100%" }));
  fireEvent.error(img);
  fireEvent.click(screen.getByRole("button", { name: "Retry original" }));
  const retried = screen.getByTestId("viewer-image");
  Object.defineProperties(retried, { naturalWidth: { value: 1600 }, naturalHeight: { value: 800 } });
  fireEvent.load(retried);
  expect(retried).toHaveAttribute("data-scale", "1");
  view.unmount();
});
