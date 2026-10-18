import { fireEvent, render, screen, within } from "@testing-library/react";
import { LedgerRowEditor, type SceneOption } from "./LedgerRowEditor";

test("repeated scene titles name the stored identifier while unique titles stay readable", () => {
  const scenes: SceneOption[] = [
    { id: "004", title: "New scene" }, { id: "009", title: "New scene" },
    { id: "011", title: "Realm road" }, { id: "012", title: "" },
  ];
  const save = vi.fn();
  render(<LedgerRowEditor fields={[{ key: "scene", label: "Scene", kind: "scene" }]}
                          initial={{ scene: "004" }} scenes={scenes} busy={false}
                          onSave={save} onCancel={vi.fn()} />);
  const picker = screen.getByRole("combobox", { name: "Scene" });
  expect(within(picker).getByRole("option", { name: "New scene (004)" })).toHaveValue("004");
  expect(within(picker).getByRole("option", { name: "New scene (009)" })).toHaveValue("009");
  expect(within(picker).getByRole("option", { name: "Realm road" })).toHaveValue("011");
  expect(within(picker).getByRole("option", { name: "012" })).toHaveValue("012");
  fireEvent.change(picker, { target: { value: "009" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(save).toHaveBeenCalledWith({ scene: "009" });
});
