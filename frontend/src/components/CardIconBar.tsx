import type { ReactNode } from "react";

import { SpinnerIcon } from "./icons";

export interface CardIconAction {
  key: string;
  /** Icon glyph — an SVG icon component from `components/icons` (#516). A bare
   *  string still renders for one-off glyphs. */
  icon: ReactNode;
  /** Becomes both aria-label and title. */
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "default" | "danger";
  /**
   * Which edge of the bar the icon sits against. Defaults to "end" (right);
   * "start" pins it to the left, with a flexible gap separating the groups.
   */
  align?: "start" | "end";
}

function IconButton({ action }: { action: CardIconAction }) {
  return (
    <button
      type="button"
      className={action.variant === "danger" ? "card-icon-button danger" : "card-icon-button"}
      aria-label={action.label}
      title={action.label}
      disabled={action.disabled || action.busy}
      onClick={action.onClick}
    >
      <span aria-hidden="true">{action.busy ? <SpinnerIcon /> : action.icon}</span>
    </button>
  );
}

/**
 * The action bar pinned to a card's bottom edge. Every card renders one — even
 * with no actions (the empty bar is invisible and reserved for future icons).
 * Actions default to the right edge; those with `align: "start"` sit at the
 * left, separated from the right-aligned group by a flexible spacer.
 */
export function CardIconBar({ actions }: { actions: CardIconAction[] }) {
  const start = actions.filter((a) => a.align === "start");
  const end = actions.filter((a) => a.align !== "start");
  return (
    <div className="card-icon-bar" role="toolbar" aria-label="Card actions">
      {start.map((a) => (
        <IconButton key={a.key} action={a} />
      ))}
      {start.length > 0 && end.length > 0 && (
        <span className="card-icon-bar-spacer" aria-hidden="true" />
      )}
      {end.map((a) => (
        <IconButton key={a.key} action={a} />
      ))}
    </div>
  );
}
