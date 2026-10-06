import type { AnchorRelation, Driver, DriverControl, DriversSnapshot, PressureState,
              TimeMode } from "../api/types";
import { ANCHOR_RELATIONS, MUST_CAP, chooseAnchor, controlOf, groupDrivers, mustAllowed,
         steeredCount, timeLabel, whenPhrase, type PressureControls } from "./pressureControls";

/** The Story Pressure controls (capstone §16.2): a disclosure inside the
 *  chooser's Generated group, under Direction, collapsed unless a Story Graph
 *  handoff seeded it.
 *
 *  Rendering only. What the reader sets lives in `NewSceneChooser` beside
 *  `direction` (so it survives Back), the rules about what a set of controls
 *  may send are `pressureControls.ts`'s, and nothing here starts a request:
 *  a control rides the next one the reader makes.
 *
 *  `open` is controlled, and `onToggle` reports the element's own state rather
 *  than flipping a flag, as `tracker/TrackerDisclosure.tsx` does -- so a
 *  summary click and the prop cannot disagree.
 *
 *  Every disabled control says why in visible text, not in a hover-only
 *  `title` the Android WebView never shows. */
export function StoryPressure({ snap, value, onChange, disabled, open, onToggle }: {
  snap: DriversSnapshot;
  value: PressureControls;
  onChange: (next: PressureControls) => void;
  disabled: boolean;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const steered = steeredCount(value);
  const time = timeLabel(value);
  const parts = [steered > 0 ? `${steered} not Normal` : "", time].filter(Boolean);
  const anchored = value.time === "anchor" ? value.anchor : "";
  const anchor = snap.anchors.find((a) => a.ref === value.anchor);
  const precisionOf = (ref: string) => snap.anchors.find((a) => a.ref === ref)?.precision;

  function setDriver(ref: string, control: DriverControl) {
    const drivers = { ...value.drivers };
    if (control === "normal") delete drivers[ref];
    else drivers[ref] = control;
    onChange({ ...value, drivers });
  }

  function setTime(mode: TimeMode) {
    if (mode !== "anchor") { onChange({ ...value, time: mode }); return; }
    // An anchor is always selected: the one held from before if the read
    // still offers it, else the soonest.
    const held = snap.anchors.some((a) => a.ref === value.anchor);
    onChange(chooseAnchor(value, held ? value.anchor : snap.anchors[0].ref, snap));
  }

  return (
    <details className="story-pressure" open={open}
             onToggle={(event) => onToggle(event.currentTarget.open)}>
      <summary>Story pressure{parts.length ? ` (${parts.join(" · ")})` : ""}</summary>
      <div className="story-pressure-body">
        {groupDrivers(snap.drivers).map((group) => (
          <div className="story-pressure-group" key={group.kind}>
            <h4>{group.heading}</h4>
            {group.rows.map((d) => (
              <DriverRow key={d.ref} driver={d} control={controlOf(value, d.ref)}
                         chip={chipText(d, precisionOf(d.ref))}
                         mustOk={mustAllowed(d.kind, d.ref, value)}
                         avoidOk={d.ref !== anchored} disabled={disabled}
                         onChange={(c) => setDriver(d.ref, c)} />
            ))}
          </div>
        ))}
        <div className="field-hint">
          Must-include: up to {MUST_CAP} threads or commitments; dates are set under Time.
        </div>

        <h4>Time</h4>
        <div className="radio-group" role="radiogroup" aria-label="Time">
          {TIME_OPTIONS.map(([mode, label]) => (
            <label className="radio-row" key={mode}>
              <input type="radio" name="pressure-time" value={mode}
                     checked={value.time === mode}
                     disabled={disabled || (mode === "anchor" && snap.anchors.length === 0)}
                     onChange={() => setTime(mode)} />
              {label}
            </label>
          ))}
        </div>
        {snap.anchors.length === 0 && (
          <div className="field-hint">
            No upcoming dated events, holidays or birthdays to anchor to.
          </div>
        )}
        {value.time === "anchor" && anchor && (
          <div className="story-pressure-anchor">
            <select aria-label="Anchor" value={anchor.ref} disabled={disabled}
                    onChange={(e) => onChange(chooseAnchor(value, e.target.value, snap))}>
              {snap.anchors.map((a) => (
                <option key={a.ref} value={a.ref}>
                  {`${a.label} — ${a.friendly} (${whenPhrase(a.in_days, a.precision)})`}
                </option>
              ))}
            </select>
            {/* A month-only birthday has no day to be before or after. */}
            <select aria-label="Relation" disabled={disabled}
                    value={anchor.precision === "month" ? "on" : value.relation}
                    onChange={(e) => onChange({ ...value,
                                                relation: e.target.value as AnchorRelation })}>
              {(anchor.precision === "month" ? ["on" as const] : ANCHOR_RELATIONS).map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
        )}
      </div>
    </details>
  );
}

const TIME_OPTIONS: readonly [TimeMode, string][] = [
  ["auto", "Any date"],
  ["near", "Stay near current date"],
  ["move", "Let time move"],
  ["anchor", "Choose anchor…"],
];

const CONTROL_OPTIONS: readonly [DriverControl, string][] = [
  ["normal", "Normal"], ["focus", "Focus"], ["avoid", "Avoid"], ["must", "Must"],
];

const STATE_LABELS: Record<PressureState, string> = {
  overdue: "overdue", today: "today", due_soon: "due soon", upcoming: "upcoming",
  passed: "passed", stale: "stale", ok: "ok",
};

/** The row's pressure, or `""` for an `ok` driver. A distance is added only
 *  when there is one to say ("stale" alone, never "stale · undated"), and
 *  `today` is not said twice. */
export function chipText(d: Driver, precision?: string): string {
  const { state, in_days } = d.pressure;
  if (state === "ok") return "";
  const label = STATE_LABELS[state];
  const when = in_days === null && precision !== "month" ? "" : whenPhrase(in_days, precision);
  return when && when !== label ? `${label} · ${when}` : label;
}

/** One driver: its label, its pressure chip, and one segmented control, so it
 *  cannot hold two states. Must is offered to threads and commitments under
 *  the cap; Avoid is withheld from the batch's anchor (plan Decision 26). */
function DriverRow({ driver, control, chip, mustOk, avoidOk, disabled, onChange }: {
  driver: Driver; control: DriverControl; chip: string; mustOk: boolean; avoidOk: boolean;
  disabled: boolean; onChange: (control: DriverControl) => void;
}) {
  return (
    <div className="story-pressure-row">
      <span className="story-pressure-label">
        {driver.label}
        {chip && <span className={`chip${HOT.has(driver.pressure.state) ? " warn" : ""}`}>
          {chip}
        </span>}
      </span>
      <div className="radio-group" role="radiogroup"
           aria-label={`${driver.label}: story pressure`}>
        {CONTROL_OPTIONS.map(([c, label]) => (
          <label className="radio-row" key={c}>
            <input type="radio" name={`pressure-${driver.ref}`} value={c}
                   checked={control === c}
                   disabled={disabled || (c === "must" && !mustOk)
                             || (c === "avoid" && !avoidOk)}
                   onChange={() => onChange(c)} />
            {label}
          </label>
        ))}
      </div>
    </div>
  );
}

/** `pressure.HIGH_PRESSURE`: drawn in the warning colour. */
const HOT: ReadonlySet<PressureState> = new Set(["overdue", "today", "due_soon"]);
