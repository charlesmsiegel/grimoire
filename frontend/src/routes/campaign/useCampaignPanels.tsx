import { useState } from "react";
import {
  type IncomingRef
} from "../../api/client";
import { type DrawerTarget } from "../../components/RecordDrawer";

export function useCampaignPanels() {

  const [showCalendar, setShowCalendar] = useState(false);
  const [showMechanics, setShowMechanics] = useState(false);
  const [showStyle, setShowStyle] = useState(false);
  const [showCover, setShowCover] = useState(false);
  /** The per-scene tracker fields: what this scene stops tracking, and what it adds. */
  const [showTracker, setShowTracker] = useState(false);

  const [showGroup, setShowGroup] = useState(false);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  /** The campaign side of push/sync (#6). Its own toggle rather than a tab
   *  inside Changes: that panel is what this campaign's own play changed, and
   *  this is what the world changed underneath it — the same shape of question
   *  about a different author. */
  const [showIncoming, setShowIncoming] = useState(false);
  /** The composition overview (#199): where every piece of this campaign stands
   *  relative to the world it came from. Its own toggle beside World updates
   *  rather than a tab inside it, for the reason that panel is its own toggle
   *  beside Changes — three different questions about the same records, and
   *  a reader mid-turn opens the one they mean. */
  const [showComposition, setShowComposition] = useState(false);
  /** The ref the composition panel sent to World updates, so the review opens on
   *  the change the reader just read a status word for. Held here because it
   *  crosses two panels, and re-boxed on every hand-off so pointing at the same
   *  ref twice re-selects it. */
  const [reviewRef, setReviewRef] = useState<IncomingRef | null>(null);
  /** Bumped when an accept or reject lands in World updates. Both panels can be
   *  open at once and read the same `/incoming`, so without it the composition
   *  rows and its pending count go on reporting a change that is already
   *  resolved — and one accept can resolve several of those rows. */
  const [resolvedEpoch, setResolvedEpoch] = useState(0);
  // The other direction of the same pairing: a pin lands in the composition
  // panel and changes what `/incoming` answers the review panel beside it.
  const [pinEpoch, setPinEpoch] = useState(0);

  /** The scene inspector, which used to be a permanently-open third column.
   *
   *  It is a panel now, opened from the composer's "What the model saw →".
   *  That is a promotion rather than a demotion: what it answers — what went
   *  into the last prompt, and what was dropped to fit — is a question about a
   *  turn, so it belongs beside the control that takes the next one. The
   *  continuity it also carried (who is here, where, when) does not live here
   *  any more; that is the context column, permanently visible, which is the
   *  whole point of the redesign.
   *
   *  Closed by default: an open panel over the transcript is the state the old
   *  third column was in permanently. */
  const [showInspector, setShowInspector] = useState(false);

  /** Which panel is above the transcript, if any.
   *
   *  Derived rather than stored beside the eight flags, so it cannot disagree
   *  with them. Only ONE can be open at a time in practice — every menu item
   *  toggles and the panels stack — but the first match is what the Close
   *  button names, and closing clears all eight anyway.
   */
  const openPanel = ([
    [showChanges, "Changes"], [showIncoming, "World updates"],
    [showComposition, "Composition"], [showMechanics, "Mechanics"],
    [showCalendar, "Calendar"], [showStyle, "Response"], [showCover, "Cover"],
    [showTracker, "Tracker"],
  ] as const).flatMap(([open, label]) => (open ? [{ label }] : []))[0];

  /** Shut everything the menu can open.
   *
   *  All eight rather than the one `openPanel` named: two can be open at once
   *  (nothing forbids it), and a Close that shut only the one it happened to
   *  name would leave the other with no control on the bar at all. */
  function closePanels() {
    setShowChanges(false); setShowIncoming(false); setShowComposition(false);
    setShowMechanics(false); setShowCalendar(false); setShowStyle(false);
    setShowCover(false); setShowTracker(false);
  }
  return {
    setDrawer, showChanges, setShowChanges, showIncoming, setShowIncoming, showComposition,
    setShowComposition, showMechanics, setShowMechanics, showCalendar, setShowCalendar, showStyle,
    setShowStyle, showCover, setShowCover, showTracker, setShowTracker, openPanel, closePanels, reviewRef,
    pinEpoch, setResolvedEpoch, resolvedEpoch, setPinEpoch, setReviewRef, showInspector, showGroup,
    setShowGroup, setShowInspector, drawer,
  };
}
