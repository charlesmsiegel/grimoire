import { useCallback, useEffect, useState } from "react";
import { api, type ScheduledEvent } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";

/** Scheduled events (#101): the dated things this campaign has planned, and
 *  which of them the clock has already reached.
 *
 *  Campaign-scoped, beside the campaign clock in a scene-scoped inspector and
 *  for the same reason that panel gives: this is the clock's other half. An
 *  event is authored here and *fired* there — advancing time, or dating a scene
 *  past the day, stamps it — so the two belong on the same rail.
 *
 *  Nothing in this panel fires anything. The stamp is the record of the clock
 *  having reached a day, so only the clock writes it; Unfire is the reader's
 *  undo for an advance made by mistake, which is why it is a separate control
 *  with a name rather than a checkbox on the edit form.
 */
export function EventsPanel({ cid, refreshKey }: {
  cid: string;
  /** Bumped by the inspector when time may have moved — an advance and a
   *  scene's own date both fire events, and a list that did not follow would
   *  show a campaign its own past as still upcoming. */
  refreshKey?: number;
}) {
  const [events, setEvents] = useState<ScheduledEvent[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () => api.campaignEvents(cid).then((r) => setEvents(r.events)).catch(() => setEvents([])),
    [cid]);
  useEffect(() => { reload(); }, [reload, refreshKey]);

  /** One mutation, one place: every control here does the same three things
   *  around its own call — clear the error, reload, and never leave the panel
   *  stuck busy on a rejection. */
  async function run(action: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await action();
      await reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    await run(async () => {
      await api.createCampaignEvent(cid, { name, date, note });
      setName("");
      setDate("");
      setNote("");
      setAdding(false);
    });
  }

  return (
    <div className="events-panel">
      {events === null
        ? <div className="field-hint">Loading events…</div>
        : events.length === 0
          ? <div className="field-hint">Nothing scheduled.</div>
          : events.map((e) => (
            <EventRow key={e.id} event={e} busy={busy}
                      onUnfire={() => run(() => api.unfireCampaignEvent(cid, e.id))}
                      onDelete={() => run(() => api.deleteCampaignEvent(cid, e.id))} />
          ))}

      {error && <div className="field-hint error">{error}</div>}

      {adding ? (
        <div className="event-form">
          <input aria-label="Event name" placeholder="What happens…" value={name}
                 onChange={(ev) => setName(ev.target.value)} />
          <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={date}
                              onChange={setDate} ariaLabel="Event date" />
          <input aria-label="Event note" placeholder="Note (optional)" value={note}
                 onChange={(ev) => setNote(ev.target.value)} />
          <div className="picker">
            {/* Both are required in substance: an event with no day cannot
                fire, and one with no name is a row nobody can read. The button
                says so by being disabled rather than by earning a 400. */}
            <button className="primary" onClick={create}
                    disabled={busy || !name.trim() || !date}>Save</button>
            <button onClick={() => setAdding(false)} disabled={busy}>Cancel</button>
          </div>
        </div>
      ) : (
        <button onClick={() => setAdding(true)}>+ New event</button>
      )}
    </div>
  );
}

/** One event: what it is, when it falls, and whether the clock has reached it. */
function EventRow({ event, busy, onUnfire, onDelete }: {
  event: ScheduledEvent; busy: boolean;
  onUnfire: () => void; onDelete: () => void;
}) {
  return (
    <div className={"event-row" + (event.fired ? " fired" : "")}>
      <div className="field-hint">
        {/* `friendly` is empty when the campaign's calendar cannot read the
            stored date — a re-pointed campaign, a broken plugin. The raw date
            still shows, because this row is the only place a reader can see
            the value that broke and fix it. */}
        {event.name} — {event.friendly || event.date}
        {event.fired && <span className="chip on"> fired</span>}
      </div>
      {event.note && <div className="field-hint">{event.note}</div>}
      <div className="picker">
        {event.fired && <button onClick={onUnfire} disabled={busy}>Unfire</button>}
        <button onClick={onDelete} disabled={busy}>Delete</button>
      </div>
    </div>
  );
}
