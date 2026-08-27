import { useEffect, useState } from "react";
import { ApiError, api, type CalendarYear } from "../api/client";
import LibraryPage from "../components/LibraryPage";

/** The calendars this store can reckon time in, and what each one observes.
 *
 *  A calendar on its own has no holidays. They fall out of a *configured*
 *  calendar: `gregorian` produces none until it is given a region, `hebrew`
 *  switches its set on Israel-vs-diaspora, and any provider adds whatever
 *  `custom_holidays` its config carries. The Library has neither a world nor a
 *  campaign to take a config from, so this page supplies one — which is why
 *  there is a region control and why it only appears for the calendars that
 *  take one.
 *
 *  Read-only. Nothing here is a world's or a campaign's calendar; changing
 *  those is `CalendarConfig`'s job, on the world and on the campaign hub. This
 *  answers "what does this calendar do", which is the question you have while
 *  deciding whether to use it.
 *
 *  The year defaults to **this calendar's** current year rather than to a
 *  Gregorian one. A Hebrew year is around 5786 and a homebrew calendar's could
 *  be anything, so a shared default is a date most calendars cannot represent.
 */

/** Which calendars take a region, and what the choice is called in each.
 *
 *  Kept here rather than asked of the server because it is a question about
 *  *wording*: `gregorian`'s region picks a country's public holidays, and
 *  `hebrew`'s picks an observance. Calling both "region" would be accurate and
 *  useless. A provider not named here is shown without the control, which is
 *  the right default for a homebrew calendar whose holidays are its own.
 */
const REGIONED: Record<string, { label: string; options: [string, string][] }> = {
  gregorian: {
    label: "Public holidays",
    options: [["US", "United States"], ["GB", "United Kingdom"], ["CA", "Canada"],
              ["AU", "Australia"], ["IL", "Israel"], ["", "None"]],
  },
  hebrew: {
    label: "Observance",
    options: [["", "Diaspora"], ["IL", "Israel"]],
  },
};

export default function CalendarsView() {
  const [providers, setProviders] = useState<{ id: string; name: string }[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [region, setRegion] = useState("");
  /** The year, WITH the calendar it belongs to.
   *
   *  Not a bare number reset by an effect on `picked`: effects in the same
   *  pass read this render's values, so the fetch would fire once carrying the
   *  previous calendar's year — asking Hebrew for 2026, which it cannot
   *  represent. Pairing them makes "this year is not for this calendar" a
   *  thing the render can see rather than a thing an effect has to have
   *  already fixed. */
  const [held, setHeld] = useState<{ id: string; year: number } | null>(null);
  const year = held && held.id === picked ? held.year : null;
  const [data, setData] = useState<CalendarYear | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.listCalendarProviders()
      .then((ps) => {
        if (!live) return;
        setProviders(ps);
        setPicked((p) => p ?? ps[0]?.id ?? null);
      })
      .catch(() => { if (live) setProviders([]); });
    return () => { live = false; };
  }, []);

  // The region is reset on a switch for the year's reason: "IL" means nothing
  // to a homebrew calendar. It is safe as an effect because a region the new
  // calendar does not know is ignored rather than refused, where a year it
  // cannot represent is a 400.
  useEffect(() => {
    setRegion(picked && REGIONED[picked] ? REGIONED[picked].options[0][0] : "");
  }, [picked]);

  useEffect(() => {
    if (!picked) return;
    let live = true;
    api.getCalendarYear(picked, year ?? undefined, region)
      .then((d) => {
        if (!live) return;
        // Cleared on SUCCESS, not on dispatch. Clearing it up front makes the
        // banner flicker off the moment a second request starts -- and two
        // fire on mount, because adopting the region is a state change. A
        // failure that erases itself is one the reader never gets to read.
        setFailed(null);
        setData(d);
        // Adopt the year the calendar resolved, so the input shows the one
        // being displayed rather than staying empty.
        setHeld((h) => (h && h.id === d.id ? h : { id: d.id, year: d.year }));
      })
      .catch((e: unknown) => {
        if (!live) return;
        setData(null);
        setFailed(e instanceof ApiError && e.detail
          ? e.detail : "This calendar could not be read.");
      });
    return () => { live = false; };
  }, [picked, year, region]);

  const regioned = picked ? REGIONED[picked] : undefined;
  // Holidays grouped under the month they land in, so the page reads as a year
  // rather than as a list of dates.
  //
  // On `month_key`, which the server resolves. NOT on `month`: that is the
  // calendar's month NUMBER and the months' own keys are tokens ("01",
  // "Tishrei"), so grouping by it matches nothing and renders every month
  // empty — a year that looks answered and observes nothing.
  const byMonth = new Map<string, CalendarYear["holidays"]>();
  for (const h of data?.holidays ?? []) {
    byMonth.set(h.month_key, [...(byMonth.get(h.month_key) ?? []), h]);
  }
  // Anything the server could not place stays visible rather than vanishing
  // into a month that is not on the page.
  const unplaced = (data?.holidays ?? []).filter((h) => !h.month_key);

  return (
    <LibraryPage>
      <div className="page-wide view-anim">
        <div className="eyebrow">The Library</div>
        <h1 className="screen-title">Calendars</h1>
        <p className="field-hint">
          How a world can reckon time, and what each calendar observes. Worlds and
          campaigns choose one of these; this page only shows what they do.
        </p>

        {providers === null && <p className="field-hint">…</p>}
        {providers?.length === 0 && (
          <p className="empty-state">No calendars are registered.</p>
        )}

        {!!providers?.length && (
          <div className="cal-picker">
            {providers.map((p) => (
              <button key={p.id} type="button"
                      className={"chip" + (picked === p.id ? " on" : "")}
                      aria-pressed={picked === p.id}
                      onClick={() => setPicked(p.id)}>
                {p.name}
              </button>
            ))}
          </div>
        )}

        {picked && (
          <div className="cal-controls">
            <label>
              Year
              <input type="number" value={year ?? ""} aria-label="Year"
                     onChange={(e) => {
                       const n = parseInt(e.target.value, 10);
                       if (!Number.isNaN(n) && picked) setHeld({ id: picked, year: n });
                     }} />
            </label>
            {/* Only for the calendars that take one. A homebrew calendar's
                holidays are its own, and offering it a country picker would be
                asking a question it has no answer to. */}
            {regioned && (
              <label>
                {regioned.label}
                <select value={region} aria-label={regioned.label}
                        onChange={(e) => setRegion(e.target.value)}>
                  {regioned.options.map(([v, l]) => (
                    <option key={v || "none"} value={v}>{l}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}

        {failed && <div className="banner error-banner">{failed}</div>}

        {data && !failed && (
          <>
            <p className="field-hint">
              {data.months.length} month{data.months.length === 1 ? "" : "s"}
              {" · "}
              {data.holidays.length === 0
                ? "no observances in this year"
                : `${data.holidays.length} observance${data.holidays.length === 1 ? "" : "s"}`}
              {regioned && !data.region && " — pick one above to see its holidays"}
            </p>
            <ol className="cal-months">
              {data.months.map((m) => {
                const hs = byMonth.get(String(m.key)) ?? [];
                return (
                  <li key={String(m.key)} className="cal-month">
                    <div className="cal-month-head">
                      <span className="cal-month-name">{m.name}</span>
                      <span className="cal-month-days">{m.days} days</span>
                    </div>
                    {hs.length > 0 && (
                      <ul className="cal-holidays">
                        {hs.map((h) => (
                          <li key={`${h.fixed}-${h.name}`}>
                            <span className="cal-holiday-day">{h.day}</span>
                            <span className="cal-holiday-name">{h.name}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ol>
            {unplaced.length > 0 && (
              <div className="cal-unplaced">
                <div className="money-label">Not in a listed month</div>
                <ul className="cal-holidays">
                  {unplaced.map((h) => (
                    <li key={`${h.fixed}-${h.name}`}>
                      <span className="cal-holiday-name">{h.name}</span>
                      <span className="field-hint"> {h.friendly || h.month_name}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </LibraryPage>
  );
}
