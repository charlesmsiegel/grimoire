import { useMemo } from "react";
import { api } from "../../api/client";
import { CalendarDatePicker } from "../CalendarDatePicker";
import { NoticeBanner } from "../NoticeBanner";
import type { DrawerTarget } from "../RecordDrawer";
import { LOCKED_WHILE_GENERATING } from "../sceneLock";
import { SideSection, type SectionControls } from "./SideSection";
import type { SceneMoment } from "./useSceneMoment";

export function LocationSection({ cid, location, setDrawer, collapsed, toggleSection }:
  SectionControls & { cid: string; location: SceneMoment["location"]; setDrawer: (target: DrawerTarget) => void }) {
  const { setting, locImages, locations, locPick, setLocPick, moveTo } = location;
  const hereId = setting?.current?.id;
  const moveOptions = useMemo(
    () => locations.filter((l) => l.id !== hereId)
      .map((l) => <option key={l.id} value={l.id}>{l.name}</option>),
    [locations, hereId]);
  return <>
      <SideSection id="location" title="Location" collapsed={!!collapsed.location} onToggle={toggleSection}>
        {setting?.current
          ? <button className={"inspector-row" + (locImages.includes("avatar") ? " inspector-loc" : "")}
                    onClick={() => setDrawer({ type: "location", id: setting.current!.id })}>
              {locImages.includes("avatar") && (
                // The original, not a `?w=` bucket. The inspector opens in
                // `.panel-slot` above the transcript, as wide as the main
                // column, and this is drawn at `width: 100%` of it -- 800px
                // and more on a desktop, past what the 1024 bucket fills at
                // DPR 2 and far past the 512 a narrower `sizes` would pick at
                // DPR 1. (The stylesheet's "286px column" describes a layout
                // nothing renders any more.) One picture, fetched only once the
                // reader opens the panel.
                <img className="inspector-loc-thumb" alt={setting.current.name} decoding="async"
                     src={api.entityImageUrl({ kind: "campaign", id: cid }, "locations",
                                             setting.current.id, "avatar")}
                     onError={(e) => { (e.currentTarget).style.display = "none"; }} />
              )}
              <span>{setting.current.name}</span>
            </button>
          : <div className="field-hint">No setting</div>}
        {locations.length > 0 && (
          <div className="picker">
            <select aria-label="Move to location" value={locPick}
                    onChange={(e) => setLocPick(e.target.value)}>
              <option value="">Move to…</option>
              {moveOptions}
            </select>
            <button className="primary" onClick={() => { void moveTo(); }} disabled={!locPick}>Move to</button>
          </div>
        )}
      </SideSection>

  </>;
}
export function WhenSection({ cid, sid, sceneLocked, date, collapsed, toggleSection }:
  SectionControls & { cid: string; sid: string; sceneLocked?: boolean | undefined; date: SceneMoment["date"] }) {
  const { cfg, when, provider, calendars, dateInput, setProvider, setDateInput, chooseCalendar, applyDatetime } = date;
  return <>
      <SideSection id="when" title="When" collapsed={!!collapsed.when} onToggle={toggleSection}>
        {when?.current ? (
          <>
            <div className="field-hint">{when.current.friendly} ({when.current.weekday})</div>
            {when.current.holidays_today.length > 0 && (
              <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
            )}
            {/* What is about to happen and has not been mentioned yet (#106),
                judged from THIS scene's moment. Under the date rather than
                above the picker: the reader reads where they are first, and the
                warning is about somewhere they are not yet. `reloadWhen` on
                dismiss keeps the panel's copy of the payload honest -- the
                banner hides the row locally, but the next date change would
                otherwise bring it back. */}
            <NoticeBanner cid={cid} notices={when.current.notices ?? []} scene={sid} />
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={() => { void applyDatetime(); }}
                      disabled={!dateInput || sceneLocked}
                      title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>Advance to</button>
            </div>
          </>
        ) : cfg && !cfg.confirmed ? (
          <>
            <div className="field-hint">Select a calendar to track dates.</div>
            <div className="picker">
              <select aria-label="Calendar" value={provider} onChange={(e) => setProvider(e.target.value)}>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="primary" onClick={() => { void chooseCalendar(); }}>Use this calendar</button>
            </div>
          </>
        ) : (
          <>
            <div className="field-hint">No date</div>
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={() => { void applyDatetime(); }}
                      disabled={!dateInput || sceneLocked}
                      title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>Set date</button>
            </div>
          </>
        )}
      </SideSection>

  </>;
}
