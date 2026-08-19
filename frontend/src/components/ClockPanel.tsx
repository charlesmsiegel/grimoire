import { useCallback, useEffect, useState } from "react";
import { agingLabel } from "../aging";
import { api, type AdvanceDigest, type CalendarConfig, type CampaignClock,
         type ClockLogEntry } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";

/** What a shown digest *is*, so one block can serve all three states without
 *  the reader having to guess which they are looking at: a preview, a move that
 *  landed, or a confirmed advance the server treated as a no-op because the
 *  clock was already at that moment (`moved: false`). Calling that last one
 *  "Advanced" would claim a write that did not happen. */
type Outcome = "preview" | "moved" | "unchanged";

/** The campaign clock (#100): where the story's present is, and the one control
 *  that moves it deliberately — by a duration or to a date, always with a reason.
 *
 *  Preview before confirm, and the digest stays on screen afterwards. The two
 *  come from the same deterministic computation on the server, so what the
 *  reader approved is what they get; the panel never recomputes anything itself.
 *
 *  Campaign-scoped state in a scene-scoped inspector, deliberately: "when is it"
 *  is the question the neighbouring When section answers for one scene, and this
 *  is that question for the campaign. Advancing writes no transcript — the only
 *  line a time change puts in a scene still comes from setting that scene's own
 *  date.
 */
export function ClockPanel({ cid, refreshKey, onAdvanced }: {
  cid: string;
  /** Bumped by the inspector when a scene's own date may have moved the clock. */
  refreshKey?: number;
  onAdvanced?: () => void;
}) {
  const [clock, setClock] = useState<CampaignClock | null>(null);
  // Only to answer "has a calendar been chosen yet"; the picker itself belongs to
  // the When section, which is where a reader is sent to use it.
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [mode, setMode] = useState<"days" | "date">("days");
  const [days, setDays] = useState("1");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [digest, setDigest] = useState<AdvanceDigest | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("preview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () => api.getCampaignClock(cid).then(setClock).catch(() => setClock(null)),
    [cid]);
  useEffect(() => { reload(); }, [reload, refreshKey]);
  useEffect(() => { api.getCalendarConfig({ kind: "campaign", id: cid }).then(setCfg).catch(() => setCfg(null)); }, [cid]);

  // A digest belongs to the request that produced it. Changing the target (or
  // the campaign) invalidates it, and showing a stale one next to new inputs is
  // how a reader confirms a skip they never previewed.
  useEffect(() => { setDigest(null); setOutcome("preview"); }, [cid, mode, days, target]);

  const request = () => (mode === "days" ? { days: parseInt(days, 10) } : { to: target });
  const ready = mode === "days" ? !isNaN(parseInt(days, 10)) : !!target;

  async function preview() {
    setError(null);
    setBusy(true);
    try {
      const r = await api.previewAdvance(cid, request());
      setDigest(r.digest);
      setOutcome("preview");
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setError(null);
    setBusy(true);
    try {
      const r = await api.advanceTime(cid, { ...request(), reason });
      setDigest(r.digest);
      setOutcome(r.moved ? "moved" : "unchanged");
      // Clearing the reason is load-bearing, not just tidy: the duration is left
      // as typed (a reader who skips a week often skips another), so without this
      // the Advance button would stay live and a second click would skip a second
      // week. An empty reason disables it until the next advance is described.
      setReason("");
      await reload();
      onAdvanced?.();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  // An unconfirmed calendar means the reader has not chosen how this campaign
  // reckons dates yet. The same nudge the When section gives, and for the same
  // reason: a moment recorded in the default calendar's notation becomes
  // unreadable the moment they pick a different one, and the clock is the worst
  // place to leave one -- the whole campaign inherits it. Deliberately not the
  // stricter rule of refusing the *request*: `PUT .../datetime` allows it too,
  // so this stays a nudge in the UI rather than a policy only one route knows.
  if (cfg && !cfg.confirmed) {
    return (
      <div className="clock-panel">
        <div className="field-hint">
          Select a calendar in the When section to track campaign time.
        </div>
      </div>
    );
  }

  return (
    <div className="clock-panel">
      {clock?.now
        ? <div className="field-hint">Now: {clock.friendly || clock.now}</div>
        : <div className="field-hint">No campaign date yet</div>}

      <div className="picker">
        <select aria-label="Advance by" value={mode}
                onChange={(e) => setMode(e.target.value as "days" | "date")}>
          <option value="days">Advance by days</option>
          <option value="date">Skip to a date</option>
        </select>
        {mode === "days"
          ? <input type="number" aria-label="Days" value={days}
                   onChange={(e) => setDays(e.target.value)} />
          : <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={target}
                                onChange={setTarget} ariaLabel="Skip to" />}
      </div>

      <input aria-label="Reason" placeholder="Why time passes…" value={reason}
             onChange={(e) => setReason(e.target.value)} />

      <div className="picker">
        <button onClick={preview} disabled={busy || !ready}>Preview</button>
        {/* A reason is required by the endpoint, so the button that needs one
            says so by being disabled rather than by earning a 400. */}
        <button className="primary" onClick={confirm}
                disabled={busy || !ready || !reason.trim()}
                title={reason.trim() ? undefined : "An advance needs a reason"}>
          Advance time
        </button>
      </div>

      {error && <div className="field-hint error">{error}</div>}

      {digest && <AdvanceDigestView digest={digest} outcome={outcome} />}

      {clock && clock.log.length > 0 && <ClockLog log={clock.log} />}
    </div>
  );
}


/** What an advance crossed, and what it leaves the campaign owing.
 *
 *  Its own component because the panel above it is a form and this is a report:
 *  they change for different reasons, and holding both in one function is what
 *  made the original 197 lines long enough for the code-health sweep to flag.
 *  Everything here is server-computed — the panel never recomputes a number, so
 *  what the reader approved is exactly what they got.
 */
function AdvanceDigestView({ digest, outcome }: { digest: AdvanceDigest; outcome: Outcome }) {
  const span = digest.backward
    ? `${Math.abs(digest.elapsed_days)} days back`
    : `${digest.elapsed_days} day${digest.elapsed_days === 1 ? "" : "s"}`;
  return (
    <div className="clock-digest">
      <div className="field-hint">
        {{ preview: "Would advance", moved: "Advanced",
           unchanged: "Already at" }[outcome]}{" "}
        {digest.from_friendly ? `${digest.from_friendly} → ` : ""}{digest.to_friendly}
        {" · "}{span}
      </div>
      {/* Two ways `truncated` gets set and they need different sentences: the
          span was past what the digest scans (nothing listed) or a list hit
          its row cap (some of it listed). Saying "too long to itemize" above
          sixty itemized rows would be a plain lie. */}
      {digest.truncated && (
        <div className="field-hint">
          {digest.holidays.length || digest.birthdays.length
            ? "More was crossed than is listed here."
            : "Too long a span to itemize — holidays and birthdays are not listed."}
        </div>
      )}
      {digest.events.length > 0 && (
        <div className="side-section-body">
          {/* The one list here that is also a WRITE: confirming stamps these as
              fired (#101), which is why the heading changes with the outcome
              rather than saying "fired" over a preview that fired nothing. A
              backward correction reports what it un-lived and stamps nothing,
              so it keeps the neutral wording too. */}
          <h4>{outcome === "moved" && !digest.backward ? "Events fired" : "Scheduled events"}</h4>
          {digest.events.map((e) => (
            <div className="field-hint" key={e.id}>{e.name} — {e.friendly || e.date}</div>
          ))}
        </div>
      )}
      {digest.holidays.length > 0 && (
        <div className="side-section-body">
          <h4>Holidays crossed</h4>
          {digest.holidays.map((h) => (
            <div className="field-hint" key={`${h.name}-${h.native}`}>
              {h.name} — {h.friendly}
            </div>
          ))}
        </div>
      )}
      {digest.birthdays.length > 0 && (
        <div className="side-section-body">
          <h4>Birthdays crossed</h4>
          {/* Index-keyed: two actors can share a name and a birthday
              (twins, or one character cast at two versions), and a
              duplicate key silently drops a row from a list whose whole
              job is to be complete. The list is regenerated whole on
              every digest, never reordered in place. */}
          {digest.birthdays.map((b, i) => (
            <div className="field-hint" key={i}>
              {b.name} turns {b.age} — {b.friendly}
            </div>
          ))}
        </div>
      )}
      {digest.open_threads.length > 0 && (
        <div className="side-section-body">
          {/* Untouched by construction: a skip contains no scenes, so
              nothing in it can have moved a thread. The badge says how long
              that has been true on the far side of this move (#103). */}
          <h4>Still open</h4>
          {digest.open_threads.map((t) => (
            <div className="field-hint" key={t.id}>{t.title}{badge(agingLabel(t.aging))}</div>
          ))}
        </div>
      )}
      {digest.commitments.length > 0 && (
        <div className="side-section-body">
          {/* Commitments before threads is the ledger's order, and the reason
              holds here: a promise with a deadline is what a skip can break. */}
          <h4>Still owed</h4>
          {digest.commitments.map((c) => (
            <div className="field-hint" key={c.id}>{c.title}{badge(agingLabel(c.aging))}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** An aging label as a trailing clause, or nothing at all. Written once because
 *  a badge that appeared on one of the two lists and not the other would read
 *  as a claim about the difference between them. */
function badge(label: string) {
  return label ? <span className="field-hint"> · {label}</span> : null;
}

/** Where the campaign's present has been, newest first. */
function ClockLog({ log }: { log: ClockLogEntry[] }) {
  return (
    <div className="clock-log">
      <h4>Recent advances</h4>
      {log.slice(-5).reverse().map((e, i) => (
        <div className="field-hint" key={i}>
          {e.from ? `${e.from} → ` : ""}{e.to}{e.reason ? ` — ${e.reason}` : ""}
        </div>
      ))}
    </div>
  );
}
