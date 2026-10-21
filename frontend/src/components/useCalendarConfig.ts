import { useEffect, useRef, useState } from "react";
import { api, type CalendarConfig as Cfg, type CalendarScope } from "../api/client";
import { errorText } from "../api/errors";

/** `calendars.WARN_DAYS`, mirrored for the same reason as `MAX_WARN_DAYS` in
 *  `CalendarConfigFields`. Only a fallback: a GET always answers this field
 *  with a resolved number, so this is reached only if a response arrives
 *  without one. */
const DEFAULT_WARN_DAYS = 7;

/** One scope's calendar config as `CalendarConfig` edits it: the load, the
 *  draft, the save and its guards, and whether the draft differs from what is
 *  stored. */
export function useCalendarConfig(
  { scope, onConfig, onDirtyChange }: {
    scope: CalendarScope;
    onConfig?: ((cfg: Cfg) => void) | undefined;
    onDirtyChange?: ((dirty: boolean) => void) | undefined;
  },
) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [stored, setStored] = useState<Cfg | null>(null);
  const [saving, setSaving] = useState(false);
  const pending = useRef<object | null>(null);
  // The window the SERVER currently holds. `save` resolves a cleared input
  // against it so the request always carries a number -- see `save`.
  const storedWarn = useRef(DEFAULT_WARN_DAYS);
  // The scope this render belongs to, readable from a settled promise -- see `save`.
  const current = useRef(`${scope.kind}/${scope.id}`);
  current.current = `${scope.kind}/${scope.id}`;
  // The config on screen right now, readable from a settled promise. Every edit
  // replaces the object, so identity against it answers "has the reader typed
  // since this save went out?" -- which the scope guard cannot, because editing
  // on does not change the scope. See `save`.
  const latest = useRef(cfg);
  latest.current = cfg;
  // A load that failed is not a load still running. Without the distinction
  // "Loading calendar…" is what an unreachable store shows forever, and on the
  // world Overview that is the first thing a new library puts on screen.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    // Blanked first, so the form never shows one record's calendar under
    // another record's Save button: `save` posts to the CURRENT scope, so
    // pressing it in that window would write the record you left into the one
    // you are looking at. "Loading calendar…" has no Save to press.
    setCfg(null);
    setStored(null);
    pending.current = null;
    setSaving(false);
    setSaved(false);
    setError(null);
    setFailed(false);
    api.getCalendarConfig(scope)
      .then((c) => {
        if (!live) return;
        setCfg(c);
        setStored(c);
        storedWarn.current = c.warn_days ?? DEFAULT_WARN_DAYS;
        onConfig?.(c);
      })
      .catch(() => { if (live) { setCfg(null); setFailed(true); } });
    return () => { live = false; };
    // `onConfig` is deliberately not a dependency: callers pass an inline
    // lambda, and re-running this on every render of the parent would refetch
    // forever. The scope is the only thing that decides what to load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id]);

  // Once per mount, not once per scope: the provider list is a global — the
  // built-in calendars plus whatever plugins the store holds — and nothing
  // about which record you are editing changes it.
  useEffect(() => {
    api.getCalendarProviders().then((r) => setProviders(r.providers)).catch(() => setProviders([]));
  }, []);

  // A blank warning field resolves to the stored window at Save, so compare
  // that value, not its temporary null input sentinel. A successful save moves
  // this baseline even when later keystrokes keep the visible draft dirty.
  const dirty = cfg !== null && stored !== null
    && JSON.stringify({ ...cfg, warn_days: cfg.warn_days ?? storedWarn.current })
      !== JSON.stringify({ ...stored, warn_days: stored.warn_days ?? storedWarn.current });
  useEffect(() => { onDirtyChange?.(dirty || saving); }, [onDirtyChange, dirty, saving]);
  useEffect(() => () => { onDirtyChange?.(false); }, [onDirtyChange]);

  /** Every field edit: the draft changes, so "Saved." no longer describes it. */
  function edit(patch: Partial<Cfg>) {
    if (!cfg) return;
    setSaved(false);
    setCfg({ ...cfg, ...patch });
  }

  async function save() {
    const activeConfig = cfg;
    if (pending.current || !activeConfig) return;
    const attempt = {};
    pending.current = attempt;
    setSaving(true);
    setError(null);
    // Blank resolves to what is stored BEFORE the request goes out, so this
    // form never sends the `null` sentinel. That sentinel means "this request
    // expressed no opinion", and the store answers it by keeping the stored
    // window (#106) -- correct for a client that predates the field, and wrong
    // for this one, which shows the field and would be left displaying an empty
    // box over a server that kept 14. Resolving here keeps the invariant the
    // comment below rests on: the server normalizes what the form sends, it
    // never decides a field the form is showing.
    const outgoing = { ...activeConfig, warn_days: activeConfig.warn_days ?? storedWarn.current };
    // The scope this save belongs to. This component is reused across worlds and
    // campaigns, so a save still in flight when the scope changes would
    // otherwise install ITS config over the new scope's load -- leaving the
    // previous record's calendar on screen under the new record's Save button,
    // where the next edit writes it over the new one.
    const startedIn = `${scope.kind}/${scope.id}`;
    // What was on screen when this save went out, for the same kind of check one
    // scope narrower -- see below.
    const startedFrom = activeConfig;
    try {
      await api.setCalendarConfig(scope, outgoing);
      if (current.current !== startedIn) return;
      storedWarn.current = outgoing.warn_days;
      setStored(outgoing);
      // The saved config, not a re-read: the server normalizes but does not
      // decide any field the form shows, and a second GET would race this
      // component's own scope effect if the reader had already moved on. This
      // one is what the STORE now holds, so it is reported whether or not the
      // form has moved on from it.
      onConfig?.(outgoing);
      // Kept editing while the request was out? Then `outgoing` is a snapshot
      // from before those keystrokes, and installing it would silently undo them
      // -- and stamp "Saved." over a form that no longer matches what was saved,
      // which is the reading a reader cannot recover from. The scope guard above
      // does not catch this: editing on stays in the same scope.
      if (latest.current !== startedFrom) return;
      setCfg(outgoing);   // the control shows the number that was actually saved
      setSaved(true);
    } catch (err: unknown) {
      // Guarded like the success path above, and for the same reason: a
      // rejection from the scope the reader has already left would otherwise
      // put its error banner over the record now on screen.
      if (current.current === startedIn) setError(errorText(err));
    } finally {
      if (pending.current === attempt) {
        pending.current = null;
        setSaving(false);
      }
    }
  }

  /** Put the stored config back over the draft. */
  function cancel() {
    setCfg(stored);
    setSaved(false);
    setError(null);
  }

  return { cfg, providers, error, saved, saving, failed, dirty, edit, save, cancel };
}
