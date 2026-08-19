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
  /** The event being edited, or null. One at a time: two open forms over one
   *  list is two ways to lose an edit to a reload. */
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () => api.campaignEvents(cid).then((r) => setEvents(r.events)).catch(() => setEvents([])),
    [cid]);
  useEffect(() => { reload(); }, [reload, refreshKey]);

  /** One mutation, one place: every control here does the same three things
   *  around its own call — clear the error, reload, and never leave the panel
   *  stuck busy on a rejection. Returns whether it landed, so a form can stay
   *  open (with what the reader typed) when the server refuses it. */
  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setError(null);
    setBusy(true);
    try {
      await action();
      await reload();
      return true;
    } catch (err: any) {
      setError(err.detail ?? String(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="events-panel">
      {events === null
        ? <div className="field-hint">Loading events…</div>
        : events.length === 0
          ? <div className="field-hint">Nothing scheduled.</div>
          : events.map((e) => (editing === e.id ? (
            <EventForm key={e.id} cid={cid} busy={busy} event={e}
                       onCancel={() => setEditing(null)}
                       onSave={async (body) => {
                         if (await run(() => api.updateCampaignEvent(cid, e.id, body)))
                           setEditing(null);
                       }} />
          ) : (
            <EventRow key={e.id} event={e} busy={busy}
                      onEdit={() => { setError(null); setEditing(e.id); }}
                      onUnfire={() => run(() => api.unfireCampaignEvent(cid, e.id))}
                      onDelete={() => run(() => api.deleteCampaignEvent(cid, e.id))} />
          )))}

      {error && <div className="field-hint error">{error}</div>}

      {adding ? (
        <EventForm cid={cid} busy={busy} onCancel={() => setAdding(false)}
                   onSave={async (body) => {
                     if (await run(() => api.createCampaignEvent(
                       cid, { name: body.name ?? "", date: body.date ?? "", note: body.note })))
                       setAdding(false);
                   }} />
      ) : (
        <button onClick={() => { setError(null); setAdding(true); }}>+ New event</button>
      )}
    </div>
  );
}

/** One event: what it is, when it falls, and where the clock stands on it. */
function EventRow({ event, busy, onEdit, onUnfire, onDelete }: {
  event: ScheduledEvent; busy: boolean;
  onEdit: () => void; onUnfire: () => void; onDelete: () => void;
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
        {/* Not an error, and not the clock's fault: the day is behind the
            campaign's present and no move ever fired it, which no advance can
            now repair — a span starting at "now" cannot contain a day behind
            it. Saying so is the whole remedy; Edit is next to it. */}
        {event.passed && <span className="chip"> missed</span>}
      </div>
      {event.note && <div className="field-hint">{event.note}</div>}
      {event.passed && (
        <div className="field-hint">
          The campaign is already past this day — re-date it to schedule it again.
        </div>
      )}
      <div className="picker">
        <button onClick={onEdit} disabled={busy}>Edit</button>
        {event.fired && <button onClick={onUnfire} disabled={busy}>Unfire</button>}
        <button onClick={onDelete} disabled={busy}>Delete</button>
      </div>
    </div>
  );
}

/** The add and edit form, which is one form: the fields are the same three, and
 *  two copies of it would be two places for the "a day is required" rule to
 *  drift. An edit sends only what changed — the store reads an omitted field as
 *  "leave it alone" — so a reader who clears the name gets the stored one back
 *  rather than an event called "". */
function EventForm({ cid, busy, event, onCancel, onSave }: {
  cid: string; busy: boolean; event?: ScheduledEvent;
  onCancel: () => void;
  onSave: (body: { name?: string; date?: string; note?: string }) => void;
}) {
  const [name, setName] = useState(event?.name ?? "");
  const [date, setDate] = useState(event?.date ?? "");
  const [note, setNote] = useState(event?.note ?? "");
  // A name is always required — an edit that cleared it would be "saved" and
  // then silently ignored by the store, which reads a blank field as "leave it
  // alone". A date is required only when creating: an edit opens on the stored
  // one, and clearing it means the same "leave it alone", which is a sensible
  // thing to do while changing only the name.
  const ready = !!name.trim() && (!!date || !!event);
  return (
    <div className="event-form">
      <input aria-label="Event name" placeholder="What happens…" value={name}
             onChange={(ev) => setName(ev.target.value)} />
      <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={date}
                          onChange={setDate} ariaLabel="Event date" />
      <input aria-label="Event note" placeholder="Note (optional)" value={note}
             onChange={(ev) => setNote(ev.target.value)} />
      <div className="picker">
        {/* Both are required in substance: an event with no day cannot fire,
            and one with no name is a row nobody can read. The button says so by
            being disabled rather than by earning a 400. */}
        <button className="primary" onClick={() => onSave({ name, date, note })}
                disabled={busy || !ready}>Save</button>
        <button onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
