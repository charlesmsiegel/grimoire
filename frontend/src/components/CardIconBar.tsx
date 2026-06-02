export interface CardIconAction {
  key: string;
  /** Emoji/glyph for now; see the icon-library issue (#516). */
  icon: string;
  /** Becomes both aria-label and title. */
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "default" | "danger";
}

/**
 * The action bar pinned to a card's bottom edge. Every card renders one — even
 * with no actions (the empty bar is invisible and reserved for future icons).
 */
export function CardIconBar({ actions }: { actions: CardIconAction[] }) {
  return (
    <div className="card-icon-bar" role="toolbar" aria-label="Card actions">
      {actions.map((a) => (
        <button
          key={a.key}
          type="button"
          className={a.variant === "danger" ? "card-icon-button danger" : "card-icon-button"}
          aria-label={a.label}
          title={a.label}
          disabled={a.disabled || a.busy}
          onClick={a.onClick}
        >
          <span aria-hidden="true">{a.busy ? "…" : a.icon}</span>
        </button>
      ))}
    </div>
  );
}
