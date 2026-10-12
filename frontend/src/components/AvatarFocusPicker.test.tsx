import { render, screen, fireEvent } from "@testing-library/react";
import { AvatarFocusPicker } from "./AvatarFocusPicker";

function open(onSave = vi.fn(), onClose = vi.fn()) {
  render(<AvatarFocusPicker src="/a.png" initial={40} onSave={onSave} onClose={onClose} />);
  return { onSave, onClose };
}

test("the slider moves the crop and Save reports where it landed", () => {
  const { onSave } = open();
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "70" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  expect(onSave).toHaveBeenCalledWith(70);
});

// Nothing is written until Save, so leaving by the key discards exactly what
// leaving by the button does (#193).
test("Escape cancels without saving", () => {
  const { onSave, onClose } = open();
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "70" } });
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
});

test("16:10 crop follows the axis that overflows and supports controlled focus", () => {
  const change = vi.fn();
  render(<AvatarFocusPicker src="/api/art" initial={50} aspect={1.6} label="Primary image"
    value={50} onChange={change} onSave={vi.fn()} onClose={vi.fn()} />);
  const image = screen.getByRole("img");
  Object.defineProperties(image, { naturalWidth: { value: 1024 }, naturalHeight: { value: 768 } });
  fireEvent.load(image);
  expect(document.querySelector(".focus-window")).toHaveStyle({ width: "100%", height: "83.33333333333334%" });
  fireEvent.change(screen.getByLabelText("Crop position"), { target: { value: "100" } });
  expect(change).toHaveBeenCalledWith(100);
});
