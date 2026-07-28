import { useCallback, useEffect, useState } from "react";
import { api, WEATHER_AXES, type SceneWeather, type WeatherAxis } from "../api/client";

const LABELS: Record<WeatherAxis, string> = {
  condition: "Condition", temperature: "Temperature", wind: "Wind",
};
const POSITIONS_PER_DAY = 5;

/** Duration options, with "the rest of today" counting the blocks actually left.
 *
 *  A fixed 5 would always span a whole day *starting here*: at 09:00 the
 *  current block is morning and only four remain, so five would also override
 *  the following day's dawn. The server returns the block ordinal, and the
 *  position within its day is what tells us the remainder. */
function durationsFor(ordinal: number | null | undefined) {
  const remaining = ordinal === null || ordinal === undefined
    ? POSITIONS_PER_DAY
    : POSITIONS_PER_DAY - (((ordinal % POSITIONS_PER_DAY) + POSITIONS_PER_DAY) % POSITIONS_PER_DAY);
  return [
    { label: "this block", blocks: 1 },
    { label: "the rest of today", blocks: remaining },
    { label: "three days", blocks: 15 },
    { label: "until I clear it", blocks: null as number | null },
  ];
}
const CHANCE = "__chance__"; // "leave to chance" — clears the axis over the range

export function WeatherWidget({ cid, sid, refreshKey }:
  { cid: string; sid: string; refreshKey?: number }) {
  const [data, setData] = useState<SceneWeather | null>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<WeatherAxis, string>>(
    { condition: "", temperature: "", wind: "" });
  const [duration, setDuration] = useState(0);
  const [note, setNote] = useState("");
  // Which controls the user actually touched. Comparing the draft against the
  // resolved value is not enough: opening an existing override and changing
  // only its note or duration leaves every axis equal, so the save would issue
  // no request at all — and pinning a currently *procedural* value for a
  // chosen duration would be impossible for the same reason.
  const [touched, setTouched] = useState<Set<WeatherAxis>>(new Set());
  const [metaDirty, setMetaDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () => api.getSceneWeather(cid, sid).then(setData).catch(() => setData(null)),
    [cid, sid]);
  useEffect(() => { reload(); }, [reload, refreshKey]);

  // Resolution returns null when there is no location or no moment yet; the
  // widget renders nothing rather than a placeholder, matching how the
  // neighbouring When and Location widgets degrade.
  if (!data?.weather) return null;

  const axes = data.weather;
  const source = data.source ?? { condition: "procedural", temperature: "procedural", wind: "procedural" };
  const tables = data.tables ?? { condition: [], temperature: [], wind: [] };
  const authored = WEATHER_AXES.filter((a) => source[a] !== "procedural");
  const stack = data.stack ?? [];
  const DURATIONS = durationsFor(data.ordinal);

  function noteOf(span: { note?: string } | undefined) {
    return span?.note || undefined;
  }

  /** The note behind an authored axis, for the title tooltip. */
  function noteFor(axis: WeatherAxis) {
    const span = stack.find((s) => (s as Record<string, unknown>)[axis]);
    return span?.note || (source[axis] === "extractor" ? "Set from narration" : "Set by you");
  }

  /** A suppression covering this axis — rendered as generated, but resumable. */
  function suppressed(axis: WeatherAxis) {
    return stack.some((s) => (s.suppress ?? []).includes(axis));
  }

  function openPopover() {
    // Seed each select with what is actually in force, so opening and saving
    // one axis cannot silently discard another.
    setDraft({ condition: axes.condition, temperature: axes.temperature, wind: axes.wind });
    setNote(noteOf(stack[0]) ?? "");
    setTouched(new Set());
    setMetaDirty(false);
    setError(null);
    setOpen(true);
  }

  async function save() {
    if (!data?.native) return;
    setBusy(true);
    setError(null);
    try {
      const chosen = DURATIONS[duration];
      const clearing = WEATHER_AXES.filter((a) => draft[a] === CHANCE);
      // An axis is written when the user picked it, or when they changed the
      // note/duration *of the span being edited*.
      //
      // Scoped to that one span rather than to every authored axis. Axes can
      // come from different spans — a local condition and an inherited
      // `_default` wind — and the PUT is location-scoped, so including the
      // inherited wind would copy it into a new local override and quietly
      // stop later campaign-wide wind changes reaching this location.
      const editing = stack[0];
      const editingHere = editing?.location === (data.location ?? "_default");
      const setting = WEATHER_AXES.filter((a) => draft[a] && draft[a] !== CHANCE
        && (touched.has(a)
            || (metaDirty && editingHere && Boolean((editing as Record<string, unknown>)[a]))));
      // "Leave to chance" clears the axis over the selected duration rather
      // than merely omitting it from the record: a user selecting it on an
      // overridden axis means "stop overriding this", and omitting would let
      // the setting appear to do nothing.
      if (clearing.length) {
        await api.clearWeather(cid, { location: data.location ?? "_default",
                                      start: data.native, blocks: chosen.blocks,
                                      axes: clearing });
      }
      if (setting.length) {
        const body: Record<string, unknown> = {
          location: data.location ?? "_default", start: data.native,
          blocks: chosen.blocks, note,
        };
        for (const a of setting) body[a] = draft[a];
        await api.setWeatherOverride(cid, body as never);
      }
      await reload();
      setOpen(false);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    if (!data?.native) return;
    setBusy(true);
    try {
      // Clears all three at once — the three-axis case of "leave to chance",
      // not a separate mechanism. It routes through the same operation, which
      // walks the whole covering stack: deleting only the winners would
      // *promote* a shadowed span, so the sky would change rather than return
      // to generated.
      await api.clearWeather(cid, { location: data.location ?? "_default",
                                    start: data.native, blocks: DURATIONS[duration].blocks });
      await reload();
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  async function resume(axis: WeatherAxis) {
    if (!data?.native) return;
    setBusy(true);
    try {
      await api.resumeWeather(cid, { location: data.location ?? "_default",
                                     start: data.native, end: null, axes: [axis] });
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="weather-widget">
      <button type="button" className="row" onClick={openPopover}
              aria-label="Weather — click to override">
        {axes.condition}, {axes.temperature}, wind {axes.wind}
        {authored.map((a) => (
          <span key={a} className="chip on" title={noteFor(a)}>{LABELS[a]} set</span>
        ))}
      </button>

      {open && (
        <div className="popover" role="dialog" aria-label="Override weather">
          {error && <div className="banner">{error}</div>}
          {WEATHER_AXES.map((axis) => {
            // The authored value is offered even when the active season has no
            // such entry: overrides are deliberately not confined to the table,
            // and the extractor writes whatever the narration invented. Without
            // it the select shows blank and saving another axis would quietly
            // discard this one.
            const table = tables[axis] ?? [];
            const extra = axes[axis] && !table.includes(axes[axis]) ? [axes[axis]] : [];
            return (
              <div key={axis} className="field">
                <label htmlFor={`weather-${axis}`}>{LABELS[axis]}</label>
                <select id={`weather-${axis}`} value={draft[axis]}
                        onChange={(e) => {
                          setDraft({ ...draft, [axis]: e.target.value });
                          setTouched(new Set(touched).add(axis));
                        }}>
                  <option value={CHANCE}>leave to chance</option>
                  {table.map((v) => <option key={v} value={v}>{v}</option>)}
                  {extra.map((v) => <option key={v} value={v}>{v} (authored)</option>)}
                </select>
                {suppressed(axis) && (
                  <button className="chip" disabled={busy} onClick={() => resume(axis)}>
                    Resume inheriting
                  </button>
                )}
              </div>
            );
          })}

          <div className="field">
            <label htmlFor="weather-duration">For</label>
            <select id="weather-duration" value={duration}
                    onChange={(e) => { setDuration(Number(e.target.value)); setMetaDirty(true); }}>
              {DURATIONS.map((d, i) => <option key={d.label} value={i}>{d.label}</option>)}
            </select>
          </div>

          <div className="field">
            <label htmlFor="weather-note">Note</label>
            {/* What the prompt actually gets to work with: "the Wintertide
                storm" tells the model more than condition: storm does. */}
            <input id="weather-note" value={note}
                   onChange={(e) => { setNote(e.target.value); setMetaDirty(true); }}
                   placeholder="the Wintertide storm" />
          </div>

          <div className="form-actions">
            <button className="primary" disabled={busy} onClick={save}>Save</button>
            {authored.length > 0 && (
              <button className="subtle" disabled={busy} onClick={clearAll}>Clear override</button>
            )}
            <button className="subtle" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
