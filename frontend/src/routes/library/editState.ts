/**
 * Shared edit-state machine for the entity and greeting editors.
 *
 * Holds the in-progress `draft` plus transient UI flags (save in flight, save
 * error, the save-confirmation dialog, the delete dialog). "dirty" is NOT part
 * of this state — callers derive it by comparing `draft` against the loaded
 * entity (see `deepEqual`), so there is no boolean to keep in sync.
 *
 * Resetting on entity change is handled by remounting the editor body via a
 * React `key`, so there is no props→state mirror effect either.
 */
import type { CampaignRef } from "../../api/library";

export interface EditState<D> {
  draft: D;
  saving: boolean;
  saveErr: string | null;
  /** Non-null while the "save to a referenced library entity?" dialog is open. */
  confirm: { dependents: CampaignRef[] } | null;
  /** Non-null while the delete dialog is open. */
  deleting: { busy: boolean; err: string | null } | null;
}

export type EditAction<D> =
  | { type: "edit"; draft: D }
  | { type: "ask-confirm"; dependents: CampaignRef[] }
  | { type: "cancel-save" }
  | { type: "save-start" }
  | { type: "save-ok" }
  | { type: "save-fail"; message: string }
  | { type: "delete-open" }
  | { type: "delete-start" }
  | { type: "delete-fail"; message: string }
  | { type: "delete-close" };

export function initialEditState<D>(draft: D): EditState<D> {
  return { draft, saving: false, saveErr: null, confirm: null, deleting: null };
}

export function editReducer<D>(state: EditState<D>, action: EditAction<D>): EditState<D> {
  switch (action.type) {
    case "edit":
      return { ...state, draft: action.draft };
    case "ask-confirm":
      return { ...state, confirm: { dependents: action.dependents } };
    case "cancel-save":
      return { ...state, confirm: null };
    case "save-start":
      return { ...state, saving: true, saveErr: null };
    case "save-ok":
      return { ...state, saving: false, confirm: null };
    case "save-fail":
      return { ...state, saving: false, saveErr: action.message, confirm: null };
    case "delete-open":
      return { ...state, deleting: { busy: false, err: null } };
    case "delete-start":
      return { ...state, deleting: { busy: true, err: null } };
    case "delete-fail":
      return { ...state, deleting: { busy: false, err: action.message } };
    case "delete-close":
      return { ...state, deleting: null };
  }
}
