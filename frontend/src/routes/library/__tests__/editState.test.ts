import { describe, expect, it } from "vitest";

import { editReducer, initialEditState, type EditState } from "../editState";

interface Draft {
  body: string;
}

const start = initialEditState<Draft>({ body: "" });

describe("editReducer", () => {
  it("seeds a clean draft with no dialogs open", () => {
    expect(start).toEqual<EditState<Draft>>({
      draft: { body: "" },
      saving: false,
      saveErr: null,
      confirm: null,
      deleting: null,
    });
  });

  it("walks load → edit → ask-confirm → save → done", () => {
    let s = editReducer(start, { type: "edit", draft: { body: "hi" } });
    expect(s.draft.body).toBe("hi");

    s = editReducer(s, { type: "ask-confirm", dependents: [{ id: "c1", name: "C1" }] });
    expect(s.confirm?.dependents).toHaveLength(1);

    s = editReducer(s, { type: "save-start" });
    expect(s).toMatchObject({ saving: true, saveErr: null });

    s = editReducer(s, { type: "save-ok" });
    expect(s).toMatchObject({ saving: false, confirm: null });
    // The edited draft survives a successful save (it now matches the reload).
    expect(s.draft.body).toBe("hi");
  });

  it("records a save error and closes the confirm dialog", () => {
    let s = editReducer(start, { type: "ask-confirm", dependents: [] });
    s = editReducer(s, { type: "save-start" });
    s = editReducer(s, { type: "save-fail", message: "boom" });
    expect(s).toMatchObject({ saving: false, saveErr: "boom", confirm: null });
  });

  it("cancel-save closes the dialog without touching the draft", () => {
    let s = editReducer(start, { type: "edit", draft: { body: "x" } });
    s = editReducer(s, { type: "ask-confirm", dependents: [] });
    s = editReducer(s, { type: "cancel-save" });
    expect(s.confirm).toBeNull();
    expect(s.draft.body).toBe("x");
  });

  it("drives the delete dialog open → start → fail → close", () => {
    let s = editReducer(start, { type: "delete-open" });
    expect(s.deleting).toEqual({ busy: false, err: null });
    s = editReducer(s, { type: "delete-start" });
    expect(s.deleting).toEqual({ busy: true, err: null });
    s = editReducer(s, { type: "delete-fail", message: "nope" });
    expect(s.deleting).toEqual({ busy: false, err: "nope" });
    s = editReducer(s, { type: "delete-close" });
    expect(s.deleting).toBeNull();
  });
});
