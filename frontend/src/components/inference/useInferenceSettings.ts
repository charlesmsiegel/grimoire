import { useCallback, useEffect, useRef, useState } from "react";
import { api, type InferenceSettings } from "../../api/client";
import { onConfigChanged } from "../../appEvents";

/** How often a library that is still moving to the new model-settings layout
 *  is asked again whether it has finished (spec 11.2). */
export const UPGRADE_POLL_MS = 1000;

/** Whether `s` is a library whose global switch has not landed yet but is on
 *  its way: pending or running, at the old layout, and not a newer build's.
 *  A failed move is retried on the next start, not by asking again, and a
 *  store at format "2" is writable whatever its status still says. */
export function upgrading(s: InferenceSettings | null | undefined): boolean {
  if (!s || s.newer || s.format === "2") return false;
  return s.migration.state === "pending" || s.migration.state === "running";
}

/** The settings view, kept current for a page that locks on it.
 *
 *  A page reads the view once and gates its writes on the layout; nothing
 *  tells it when a background migration finishes, so a page opened while it
 *  ran stayed locked until it was left. This reads again:
 *  - every `UPGRADE_POLL_MS` while the view says the upgrade is `upgrading`,
 *    re-armed whenever a read settles, answered or not -- keyed on the answer
 *    alone, one failed read changed nothing and the asking stopped;
 *  - on any model-settings change made anywhere (`configChanged`).
 *
 *  The newest read wins, and a view a write handed back (`install`) counts as
 *  the newest: a poll that left before the save cannot land over it.
 *
 *  `enabled` holds every read off until it is first true -- Settings reads the
 *  view only once its Models section is opened -- and is sticky after. */
export function useInferenceSettings(enabled = true): {
  /** The newest view that answered; null before one has. */
  settings: InferenceSettings | null;
  /** The newest read's failure, cleared by the next answer. */
  error: unknown;
  /** Whether any read has settled, answered or not. */
  settled: boolean;
  install: (s: InferenceSettings) => void;
} {
  const [settings, setSettings] = useState<InferenceSettings | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reads, setReads] = useState(0);
  const latest = useRef(0);
  const started = useRef(false);
  const live = useRef(true);

  const read = useCallback(() => {
    const n = ++latest.current;
    api.getInferenceSettings()
      .then((s) => {
        if (!live.current || n !== latest.current) return;
        setSettings(s);
        setError(null);
      })
      .catch((err: unknown) => { if (live.current && n === latest.current) setError(err); })
      .finally(() => { if (live.current && n === latest.current) setReads((k) => k + 1); });
  }, []);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  useEffect(() => {
    if (!enabled || started.current) return;
    started.current = true;
    read();
  }, [enabled, read]);

  useEffect(() => onConfigChanged(() => { if (started.current) read(); }), [read]);

  const polling = upgrading(settings);
  useEffect(() => {
    if (!polling) return;
    const timer = setTimeout(read, UPGRADE_POLL_MS);
    return () => clearTimeout(timer);
  }, [polling, reads, read]);

  const install = useCallback((s: InferenceSettings) => {
    latest.current += 1;
    setSettings(s);
    setError(null);
  }, []);

  return { settings, error, settled: reads > 0, install };
}
