import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ImageViewer, type ImageViewerProps } from "./ImageViewer";
import { activeHotkeys } from "../../shortcuts/registry";
import { useHotkeys } from "../../shortcuts/useHotkeys";
import { TestPointerEvent } from "../../testkit/pointer";
import { CHANGED_NOT_REMOVED, EDIT_FIRST } from "./removal";
import type { ImageDescriptor, RemoveOutcome } from "./types";

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

// ---- Removal (2026-10-12 viewer removal design, §1.3) ----

const removable: ImageDescriptor[] = images.map((image) => ({ ...image, removal: { question: `Remove Mara's ${image.label}?` } }));
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function viewer(props: Partial<ImageViewerProps> = {}) {
  const onClose = vi.fn(), pending = deferred<RemoveOutcome>(), onRemove = vi.fn(() => pending.promise);
  const base: ImageViewerProps = { images: removable, initialKey: "avatar", onClose, onRemove, ...props };
  const view = render(<ImageViewer {...base} />);
  const refresh = (next: ImageDescriptor[]) => view.rerender(<ImageViewer {...base} images={next} />);
  return { view, onClose, onRemove, pending, refresh };
}
const group = () => screen.getByRole("group", { name: "Confirm removal" });
const shown = () => screen.getByTestId("viewer-image").getAttribute("src");
const insideDialog = () => screen.getByRole("dialog", { name: "Image viewer" }).contains(document.activeElement);
function confirmRemoval() {
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(within(group()).getByRole("button", { name: "Remove" }));
}
async function settle(pending: ReturnType<typeof deferred<RemoveOutcome>>, outcome: RemoveOutcome | Error) {
  await act(async () => {
    if (outcome instanceof Error) pending.reject(outcome); else pending.resolve(outcome);
    await pending.promise.catch(() => undefined);
  });
}

it("draws Remove only with onRemove and a removal", () => {
  const first = render(<ImageViewer images={removable} initialKey="avatar" onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Delete" });
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  first.unmount();
  const second = render(<ImageViewer images={images} initialKey="avatar" onClose={vi.fn()} onRemove={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Delete" });
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  second.unmount();
  render(<ImageViewer images={removable} initialKey="avatar" onClose={vi.fn()} onRemove={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
});

it("Remove opens the confirmation with focus on its Remove button", () => {
  viewer();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(group()).toHaveTextContent("Remove Mara's avatar?");
  expect(within(group()).getByRole("button", { name: "Remove" })).toHaveFocus();
  expect(within(group()).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
});

it("Cancel and Escape cancel without closing", () => {
  const { onClose, onRemove } = viewer();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(within(group()).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  expect(insideDialog()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(onRemove).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
});

it("Delete and Backspace open the confirmation", () => {
  viewer();
  const rows = activeHotkeys().filter(row => row.reachable).map(row => row.key);
  for (const chord of ["delete", "backspace"])
    expect(rows.find(key => key.keys === chord)).toMatchObject({ label: "Remove image", group: "IMAGE VIEWER" });
  fireEvent.keyDown(window, { key: "Delete" });
  expect(group()).toHaveTextContent("Remove Mara's avatar?");
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.keyDown(window, { key: "Backspace" });
  expect(group()).toHaveTextContent("Remove Mara's avatar?");
});

it("Backspace in a text field does not open the confirmation", () => {
  render(<input type="text" aria-label="Notes" />);
  viewer();
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Notes", hidden: true }), { key: "Backspace" });
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
});

it("Enter on a focused Cancel does not remove", () => {
  const { onRemove } = viewer();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  const cancel = within(group()).getByRole("button", { name: "Cancel" });
  cancel.focus();
  fireEvent.keyDown(cancel, { key: "Enter" });
  expect(onRemove).not.toHaveBeenCalled();
  expect(group()).toBeInTheDocument();
});

it("Previous/Next and arrows do nothing while the confirmation is open", () => {
  viewer({ initialKey: "gallery_2" });
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.getByRole("button", { name: "Previous image" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Next image" })).toBeDisabled();
  fireEvent.keyDown(window, { key: "ArrowRight" });
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  expect(shown()).toBe(images[1].originalUrl);
  expect(group()).toHaveTextContent("Remove Mara's gallery_2?");
});

it.each(["before", "after"])("steps to the next image after removal (refresh %s the promise resolves)", async (when) => {
  const { onRemove, onClose, pending, refresh } = viewer();
  confirmRemoval();
  expect(onRemove).toHaveBeenCalledWith("avatar");
  if (when === "before") refresh(removable.slice(1));
  await settle(pending, "removed");
  if (when === "after") refresh(removable.slice(1));
  expect(shown()).toBe(images[1].originalUrl);
  expect(screen.getByRole("status", { name: "Image position" })).toHaveTextContent("1 / 2");
  expect(onClose).not.toHaveBeenCalled();
});

it("keeps the image, its keys and focus while a removal is pending after a refresh dropped it", async () => {
  const page = vi.fn();
  function Page() { useHotkeys([{ keys: "escape", run: page }]); return null; }
  render(<Page />);
  const { onClose, pending, refresh } = viewer();
  confirmRemoval();
  refresh(removable.slice(1));
  expect(shown()).toBe(images[0].originalUrl);
  expect(group()).toHaveTextContent("Removing…");
  expect(insideDialog()).toBe(true);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(page).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.keyDown(document.activeElement ?? window, { key: "Tab" });
  expect(insideDialog()).toBe(true);
  await settle(pending, "removed");
  expect(shown()).toBe(images[1].originalUrl);
  expect(insideDialog()).toBe(true);
  expect(onClose).not.toHaveBeenCalled();
});

it("steps to the previous when the last is removed", async () => {
  const { pending } = viewer({ initialKey: "gallery_10" });
  confirmRemoval();
  await settle(pending, "removed");
  expect(shown()).toBe(images[1].originalUrl);
});

it("closes plainly when the last image is removed", async () => {
  const { onClose, pending, refresh } = viewer({ images: [removable[0]] });
  confirmRemoval();
  await settle(pending, "removed");
  expect(onClose).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledWith();
  refresh([]);
  await waitFor(() => expect(onClose).not.toHaveBeenCalledWith("unavailable"));
});

it('"changed" with the key still present stays and shows the sentence', async () => {
  const { onClose, pending } = viewer();
  confirmRemoval();
  await settle(pending, "changed");
  expect(shown()).toBe(images[0].originalUrl);
  expect(screen.getByText(CHANGED_NOT_REMOVED)).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  expect(insideDialog()).toBe(true);
  expect(onClose).not.toHaveBeenCalled();
});

it('"changed" with the key gone steps on', async () => {
  const { onClose, pending, refresh } = viewer();
  confirmRemoval();
  refresh(removable.slice(1));
  await settle(pending, "changed");
  expect(shown()).toBe(images[1].originalUrl);
  expect(screen.queryByText(CHANGED_NOT_REMOVED)).not.toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it("a rejection stays and shows its message", async () => {
  const { onClose, pending } = viewer();
  confirmRemoval();
  await settle(pending, new Error(EDIT_FIRST));
  expect(shown()).toBe(images[0].originalUrl);
  expect(screen.getByText(EDIT_FIRST)).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Confirm removal" })).not.toBeInTheDocument();
  expect(insideDialog()).toBe(true);
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(screen.queryByText(EDIT_FIRST)).not.toBeInTheDocument();
});

it("controls are disabled while pending", () => {
  const { onClose, onRemove } = viewer({ initialKey: "gallery_2" });
  confirmRemoval();
  expect(group()).toHaveTextContent("Removing…");
  for (const name of ["Previous image", "Next image", "Fit", "100%", "Zoom in", "Remove", "Cancel"])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Close image viewer" })).toBeEnabled();
  for (const key of ["ArrowLeft", "ArrowRight", "Delete", "Backspace", "Escape", "+"]) fireEvent.keyDown(window, { key });
  expect(shown()).toBe(images[1].originalUrl);
  expect(onRemove).toHaveBeenCalledOnce();
  expect(onClose).not.toHaveBeenCalled();
  expect(group()).toHaveTextContent("Removing…");
  fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
  expect(onClose).toHaveBeenCalledWith();
});

it("a removal resolving after the viewer closed does nothing", async () => {
  const onSelectionChange = vi.fn();
  const { view, onClose, pending } = viewer({ onSelectionChange });
  confirmRemoval();
  view.unmount();
  onSelectionChange.mockClear();
  await settle(pending, "removed");
  expect(onClose).not.toHaveBeenCalled();
  expect(onSelectionChange).not.toHaveBeenCalled();
});
