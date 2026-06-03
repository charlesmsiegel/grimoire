import type { CardIconAction } from "./CardIconBar";

// TODO(#516): replace emoji icons with shared SVG icon components.
export const DELETE_ICON = "🗑";
export const FORK_ICON = "⑂";
export const SETTINGS_ICON = "⚙";
export const CONVERT_ICON = "⇄";

/** Build the standard Delete (trash) action for a card icon bar. */
export function deleteAction(opts: {
  onClick: () => void;
  label?: string;
  busy?: boolean;
  disabled?: boolean;
}): CardIconAction {
  return {
    key: "delete",
    icon: DELETE_ICON,
    label: opts.label ?? "Delete",
    variant: "danger",
    onClick: opts.onClick,
    busy: opts.busy,
    disabled: opts.disabled,
  };
}
