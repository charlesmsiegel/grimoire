/**
 * Shared edit-state machine for the entity and greeting editors.
 *
 * Holds the in-progress `draft`, the `baseline` it is compared against, and
 * transient UI flags (save in flight, save error, the save-confirmation
 * dialog, the delete dialog). "dirty" is derived by the caller as
 * `!deepEqual(draft, baseline)` — there is no boolean to keep in sync.
 *
 * The baseline tracks the persisted state: it is seeded from the loaded
 * entity, advanced to the just-sent draft on a successful save (so Save goes
 * disabled immediately rather than re-enabling for the duration of the
 * stale-while-revalidate reload), and re-seeded by a `reset` when a fresh
 * entity revision arrives (so a server that normalizes on save — e.g. trims
 * names — leaves the editor clean and showing the canonical values). Callers
 * fire `reset` from React's render-time "previous prop" pattern, not an
 * effect, so there is still no props→state mirror effect.
 */
import type { CampaignRef } from "../../api/library";

export interface EditState<D> {
  draft: D;
  baseline: D;
  saving: boolean;
  saveErr: string | null;
  /** Non-null while the "save to a referenced library entity?" dialog is open. */
  confirm: { dependents: CampaignRef[] } | null;
  /** Non-null while the delete dialog is open. */
  deleting: { busy: boolean; err: string | null } | null;
}

export type EditAction<D> =
  | { type: "reset"; draft: D }
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
  return { draft, baseline: draft, saving: false, saveErr: null, confirm: null, deleting: null };
}

export function editReducer<D>(state: EditState<D>, action: EditAction<D>): EditState<D> {
  switch (action.type) {
    case "reset":
      return initialEditState(action.draft);
    case "edit":
      return { ...state, draft: action.draft };
    case "ask-confirm":
      return { ...state, confirm: { dependents: action.dependents } };
    case "cancel-save":
      return { ...state, confirm: null };
    case "save-start":
      return { ...state, saving: true, saveErr: null };
    case "save-ok":
      // The draft is now the persisted state; advance the baseline so the row
      // is clean immediately (Save disabled) until the reload re-seeds it.
      return { ...state, saving: false, confirm: null, baseline: state.draft };
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
