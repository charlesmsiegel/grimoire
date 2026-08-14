import { useId, useRef, useState } from "react";
import type { Citation } from "../../api/client";

/** The five-segment certainty meter. Distinct from the dossier's five *pips*,
 *  which measure a 0–5 relationship axis: this is a 0–1 rating rendered in
 *  fifths, and an unrated citation draws none rather than drawing zero — the
 *  model declining to rate itself is not the same claim as rating itself
 *  hopeless. */
function Certainty({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="certainty-unrated">CERTAINTY UNRATED</span>;
  }
  const lit = Math.round(value * 5);
  return (
    <span className="certainty">
      <span className="certainty-label">CERTAINTY {value.toFixed(2)}</span>
      <span className="certainty-meter" role="img" aria-label={`certainty ${value.toFixed(2)} of 1`}>
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={"seg" + (i < lit ? " on" : "")} />
        ))}
      </span>
    </span>
  );
}

/** A stored field, with the citation behind it.
 *
 *  The marker is the whole feature at a glance: ◆ in the accent means the
 *  extractor quoted the transcript for this and the quote checks out, ◇ in
 *  `--muted` means nothing cited it at all, and ◇ in `--alert` means it was
 *  cited but landed under the review panel's low band — a line the model was
 *  unsure of, visible *before* it is quoted back at you in ten scenes' time.
 *
 *  Hover or focus opens the popover. Focus as well as hover is not politeness:
 *  the marker is the only route to the citation, and a hover-only affordance
 *  puts it out of reach of a keyboard and a touchscreen both. */
export default function CitedRow(
  { label, value, citation, onHoverQuote, onGoToTurn }: {
    label: string;
    value: string;
    /** Absent means uncited, which is a normal state and shown as one. */
    citation?: Citation;
    /** Called with the quote while the popover is open, and with "" when it
     *  closes, so the transcript can highlight the line this came from. */
    onHoverQuote?: (quote: string) => void;
    onGoToTurn?: (quote: string) => void;
  },
) {
  const [open, setOpen] = useState(false);
  // Where to draw the popover, in viewport coordinates.
  //
  // Fixed, not absolute, and this is not a preference: the column is a 274px
  // `overflow-y: auto` scroll port, so an absolutely-positioned child is
  // clipped by it — and a 470px popover anchored inside 274px has nowhere to go
  // but off the left edge of the screen. Measuring the marker and escaping to
  // the viewport is what puts it beside the row, over main, where the room is.
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const markerRef = useRef<HTMLButtonElement>(null);
  const popId = useId();

  const cited = Boolean(citation?.quote);
  const low = citation?.band === "low";
  const marker = cited ? "◆" : "◇";
  const markerClass = "cite-marker" + (cited ? (low ? " low" : " cited") : " uncited");
  const title = cited
    ? (low ? "Cited, but below the low certainty band" : "Cited")
    : "No citation on file";

  const POP_W = 470;
  function show(next: boolean) {
    if (next) {
      const r = markerRef.current?.getBoundingClientRect();
      if (r) {
        // Opens to the right of the column by default; flips back over it only
        // when the viewport cannot take 470px there (a phone, a split window).
        const left = r.right + 12 + POP_W <= window.innerWidth
          ? r.right + 12
          : Math.max(8, window.innerWidth - POP_W - 8);
        setAt({ top: Math.max(8, r.top - 8), left });
      }
    }
    setOpen(next);
    onHoverQuote?.(next && citation ? citation.quote : "");
  }

  return (
    <div className="dossier-row cited-row"
         onMouseEnter={() => citation && show(true)}
         onMouseLeave={() => citation && show(false)}>
      <span className="dossier-row-label">{label}</span>
      <span className="dossier-row-value">{value}</span>
      {/* A button even when uncited, so the marker's meaning is reachable by
          keyboard and by long-press rather than living only in a `title`. */}
      <button ref={markerRef} type="button" className={markerClass} title={title}
              aria-label={`${label}: ${title}`}
              aria-expanded={citation ? open : undefined}
              aria-controls={citation && open ? popId : undefined}
              onFocus={() => citation && show(true)}
              onBlur={() => citation && show(false)}
              onClick={() => citation && show(!open)}>
        {marker}
      </button>

      {open && citation && (
        <div className="cite-pop" id={popId} role="note"
             style={at ? { top: at.top, left: at.left } : undefined}>
          <div className="cite-pop-head">
            <span className="section-label">Why this line is here</span>
            <span className="cite-pop-scene">
              {citation.scene_title || citation.scene || "UNRECORDED"}
            </span>
          </div>
          <blockquote className="cite-quote">{citation.quote}</blockquote>
          <div className="cite-pop-foot">
            <span className="cite-speaker">
              <span className="section-label">Speaker</span>
              <span>{citation.speaker || "Unattributed"}</span>
            </span>
            <Certainty value={citation.certainty} />
            {onGoToTurn && (
              // `onMouseDown`, not `onClick`: the row's own `onMouseLeave`
              // closes the popover, and on a pointer that leaves the row on the
              // way to this button the click would never land.
              <button type="button" className="btn-outline cite-goto"
                      onMouseDown={() => onGoToTurn(citation.quote)}>
                Go to turn →
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
