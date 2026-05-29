import { useEffect, useState } from "react";

import { calendarsApi } from "../../api/library";
import type { Calendar, HolidaySet } from "../../api/library";

interface Props {
  calendarIds: string[];
  holidaySetIds: string[];
  displayCalendarId: string | null;
  onChange: (next: {
    calendar_ids: string[];
    holiday_set_ids: string[];
    display_calendar_id: string | null;
  }) => void;
}

/**
 * Sub-form for managing the multi-calendar attachments on a world (or a
 * campaign that has the same shape). Lets the user pick which calendars
 * are attached, which holiday packs apply, and which calendar is the
 * "display" one rendered in scenes by default.
 */
export function WorldCalendarAttachments({
  calendarIds,
  holidaySetIds,
  displayCalendarId,
  onChange,
}: Props) {
  const [allCalendars, setAllCalendars] = useState<Calendar[]>([]);
  const [allHolidaySets, setAllHolidaySets] = useState<HolidaySet[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([calendarsApi.listCalendars(), calendarsApi.listHolidaySets()])
      .then(([cals, sets]) => {
        if (cancelled) return;
        setAllCalendars(cals);
        setAllHolidaySets(sets);
        setLoading(false);
      })
      .catch(() => setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <p>Loading calendars…</p>;

  function toggleCalendar(id: string) {
    const next = calendarIds.includes(id)
      ? calendarIds.filter((x) => x !== id)
      : [...calendarIds, id];
    // If we removed the display calendar, clear or move it.
    let display: string | null = displayCalendarId;
    if (display === id && !next.includes(id)) {
      display = next.length > 0 ? next[0]! : null;
    } else if (display === null && next.length > 0) {
      display = next[0]!;
    }
    onChange({
      calendar_ids: next,
      holiday_set_ids: holidaySetIds,
      display_calendar_id: display,
    });
  }

  function toggleHolidaySet(id: string) {
    const next = holidaySetIds.includes(id)
      ? holidaySetIds.filter((x) => x !== id)
      : [...holidaySetIds, id];
    onChange({
      calendar_ids: calendarIds,
      holiday_set_ids: next,
      display_calendar_id: displayCalendarId,
    });
  }

  function setDisplay(id: string | null) {
    onChange({
      calendar_ids: calendarIds,
      holiday_set_ids: holidaySetIds,
      display_calendar_id: id,
    });
  }

  const groupedCalendars = {
    builtin: allCalendars.filter((c) => c.builtin),
    custom: allCalendars.filter((c) => !c.builtin),
  };
  const groupedSets = {
    builtin: allHolidaySets.filter((s) => s.builtin),
    custom: allHolidaySets.filter((s) => !s.builtin),
  };

  return (
    <fieldset className="world-calendar-attachments">
      <legend>Calendars &amp; holidays</legend>
      <p className="library-section-intro">
        Attach any number of calendars; their dates reconcile via a shared Julian Day Number. Pick
        one as the display calendar for scene tracking.
      </p>

      <h5>Built-in calendars</h5>
      <ul className="calendar-checkbox-grid">
        {groupedCalendars.builtin.map((c) => (
          <li key={c.id}>
            <label>
              <input
                type="checkbox"
                checked={calendarIds.includes(c.id)}
                onChange={() => toggleCalendar(c.id)}
              />{" "}
              {c.name}
            </label>
          </li>
        ))}
      </ul>

      {groupedCalendars.custom.length > 0 && (
        <>
          <h5>Custom calendars</h5>
          <ul className="calendar-checkbox-grid">
            {groupedCalendars.custom.map((c) => (
              <li key={c.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={calendarIds.includes(c.id)}
                    onChange={() => toggleCalendar(c.id)}
                  />{" "}
                  {c.name || c.id}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}

      {calendarIds.length > 0 && (
        <label className="display-calendar-select">
          <span>Display calendar (shown in scenes)</span>
          <select
            value={displayCalendarId ?? ""}
            onChange={(e) => setDisplay(e.target.value || null)}
          >
            <option value="">(none)</option>
            {calendarIds.map((id) => {
              const c = allCalendars.find((x) => x.id === id);
              return (
                <option key={id} value={id}>
                  {c?.name ?? id}
                </option>
              );
            })}
          </select>
        </label>
      )}

      <h5>Built-in holiday sets</h5>
      <ul className="calendar-checkbox-grid">
        {groupedSets.builtin.map((s) => (
          <li key={s.id}>
            <label>
              <input
                type="checkbox"
                checked={holidaySetIds.includes(s.id)}
                onChange={() => toggleHolidaySet(s.id)}
              />{" "}
              {s.name} <small>({s.calendar_system})</small>
            </label>
          </li>
        ))}
      </ul>

      {groupedSets.custom.length > 0 && (
        <>
          <h5>Custom holiday sets</h5>
          <ul className="calendar-checkbox-grid">
            {groupedSets.custom.map((s) => (
              <li key={s.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={holidaySetIds.includes(s.id)}
                    onChange={() => toggleHolidaySet(s.id)}
                  />{" "}
                  {s.name || s.id}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </fieldset>
  );
}
