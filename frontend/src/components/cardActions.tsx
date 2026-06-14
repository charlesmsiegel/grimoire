import type { CardIconAction } from "./CardIconBar";
import { TrashIcon } from "./icons";

// Card-icon-bar actions use the shared SVG icon set (#516); render the icon
// component for the action (e.g. `icon: <ForkIcon />`).

/** Build the standard Delete (trash) action for a card icon bar. */
export function deleteAction(opts: {
  onClick: () => void;
  label?: string;
  busy?: boolean;
  disabled?: boolean;
}): CardIconAction {
  return {
    key: "delete",
    icon: <TrashIcon />,
    label: opts.label ?? "Delete",
    variant: "danger",
    onClick: opts.onClick,
    busy: opts.busy,
    disabled: opts.disabled,
  };
}
