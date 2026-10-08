import { fireEvent, render, screen } from "@testing-library/react";
import { PRESET_CLEAR } from "../../api/client";
import { PresetSelect } from "./PresetSelect";

const PRESETS = [{ id: "warm", name: "Warm" }, { id: "cold", name: "Cold" }];

test("lists the presets and reports a choice", () => {
  const onChange = vi.fn();
  render(<PresetSelect value="" onChange={onChange} presets={PRESETS} />);
  const select = screen.getByLabelText<HTMLSelectElement>("Preset");
  expect([...select.options].map((o) => o.text)).toEqual(["No preset", "Warm", "Cold"]);
  fireEvent.change(select, { target: { value: "cold" } });
  expect(onChange).toHaveBeenCalledWith("cold");
});

test("allowClear offers the sentinel that stops inheriting", () => {
  const onChange = vi.fn();
  render(<PresetSelect value="" onChange={onChange} presets={PRESETS} allowClear />);
  const select = screen.getByLabelText<HTMLSelectElement>("Preset");
  expect([...select.options].map((o) => o.value)).toContain(PRESET_CLEAR);
  fireEvent.change(select, { target: { value: PRESET_CLEAR } });
  expect(onChange).toHaveBeenCalledWith(PRESET_CLEAR);
});

test("a preset that no longer exists is shown as missing, not as another", () => {
  render(<PresetSelect value="gone" onChange={() => {}} presets={PRESETS} />);
  const select = screen.getByLabelText<HTMLSelectElement>("Preset");
  expect(select.value).toBe("gone");
  expect(select.selectedOptions[0].text).toMatch(/missing/i);
});
