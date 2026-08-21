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
