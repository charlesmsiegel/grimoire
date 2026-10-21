import { useId } from "react";
import type { CalendarConfig as Cfg, CalendarScope } from "../api/client";
import { ConfirmedField, ProviderFields, ReckoningFields } from "./CalendarConfigFields";
import { useCalendarConfig } from "./useCalendarConfig";

/** The calendar editor for either scope (#223).
 *
 *  One store file under two roots: a campaign's own calendar.json, and the
 *  world default `create_campaign` copies into every campaign started from that
 *  world. The form is the same because the file is the same; the scope only
 *  decides the URL, the wording of the hints, and whether `confirmed` gets a
 *  control of its own — campaign-side the scene inspector already owns that
 *  flag, world-side nothing else can set it.
 */
export function CalendarConfig({ scope, onConfig, onDirtyChange }: {
  scope: CalendarScope;
  /** Every config this panel comes to hold — the one it loads, and each one it
   *  saves. The world Overview's checklist row is derived from it rather than
   *  from a read of its own: two components fetching one endpoint on one mount
   *  is a request nobody needs and a second `confirmed` that can disagree with
   *  this one. Not called when the load fails, which is what leaves that row
   *  off the list entirely — unknown is not unconfirmed. */
  onConfig?: (cfg: Cfg) => void;
  /** Opt-in for a page that protects all of its retained editors together. */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const isWorld = scope.kind === "world";
  const uid = useId();
  const { cfg, providers, error, saved, saving, failed, dirty, edit, save, cancel } =
    useCalendarConfig({ scope, onConfig, onDirtyChange });

  if (!cfg) {
    return <div className="field-hint">
      {failed ? "Could not load this calendar." : "Loading calendar…"}
    </div>;
  }

  return (
    <div className="calendar-config">
      {error && <div className="banner">{error}</div>}
      {/* The settings form's own furniture (`.config-field`: a small-caps
          caption over the control, its note under it), laid out once for the
          four places this panel is mounted -- the world Overview, the campaign
          hub, campaign Settings and the play view. Bare inline <label>s ran
          the whole form together on one line, "CalendarGregorian Holidays
          regionUS Stale after30". Each control keeps its `aria-label`, which
          names it the same way whether or not the caption is on screen. */}
      <ProviderFields cfg={cfg} uid={uid} edit={edit} providers={providers} />
      {/* World scope only, and above the aging knob because it is the decision
          the world Overview's checklist points at. `confirmed` means "a person
          chose this calendar"; campaign-side that answer is given to the scene
          inspector's prompt, and a second control there would be two ways to
          say the same thing. World-side there is no prompt to answer, and
          `create_campaign` copies the whole file — this flag included — into
          every campaign started from this world. */}
      {isWorld && <ConfirmedField cfg={cfg} edit={edit} />}
      <ReckoningFields cfg={cfg} uid={uid} edit={edit} isWorld={isWorld} />
      <div className="form-actions">
        {/* World-side this panel sits beside Mechanics, which has a Save of its
            own; "Save" twice in a row is ambiguous to anyone reading the page by
            its controls. The visible text stays "Save" and the name only extends
            it, so "click Save" still resolves (WCAG 2.5.3). */}
        <button className="primary" onClick={() => { void save(); }} disabled={saving}
                aria-label={isWorld ? "Save calendar" : undefined}>{saving ? "Saving..." : "Save"}</button>
        {/* Live only once there is something to put back: on a page that holds
            its other settings read-only until Edit, a Cancel that is pressable
            before anything was touched reads as a form already open for
            editing. */}
        {onDirtyChange && <button className="subtle" disabled={saving || !dirty} onClick={cancel}>Cancel</button>}
        {saved && <span className="field-hint">Saved.</span>}
      </div>
    </div>
  );
}
