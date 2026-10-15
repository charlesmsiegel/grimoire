import { useCallback, useEffect, useRef, useState } from "react";
import { api, type InferenceSettings } from "../../api/client";
import { onConfigChanged } from "../../appEvents";

/** How often a library that is still moving to the new model-settings layout
 *  is asked again whether it has finished (spec 11.2). */
export const UPGRADE_POLL_MS = 1000;
/** How many reads are asked at `UPGRADE_POLL_MS` before the asking slows to
 *  `UPGRADE_POLL_SLOW_MS`. An upgrade that says `pending` or `running` and never
 *  finishes is a real state -- the automatic switch turned off, or another
 *  process's run that died and left its note -- and a tab left open on it
 *  would otherwise ask every second for as long as it stays open. Past this
 *  many, a surface may also say the upgrade is not moving (`upgradeStalled`). */
export const UPGRADE_POLL_QUICK = 30;
/** The slower cadence past that: the run poll's own (`RUN_POLL_SLOW_MS`). */
export const UPGRADE_POLL_SLOW_MS = 5000;

/** The wait before the next upgrade read, after `reads` reads have settled. */
export function upgradePollDelay(reads: number): number {
  return reads < UPGRADE_POLL_QUICK ? UPGRADE_POLL_MS : UPGRADE_POLL_SLOW_MS;
}

/** Whether an upgrade still on its way after `reads` reads has stopped
 *  looking like one that will finish while this page is open. */
export function upgradeStalled(reads: number): boolean {
  return reads >= UPGRADE_POLL_QUICK;
}

/** Keep watching the global switch and any live archive work (#496).
 *  Activity includes the format-2/done planning gap before retirement's
 *  archive. This only controls reads; editability still follows the format. */
export function upgrading(s: InferenceSettings | null | undefined): boolean {
  if (!s || s.newer || s.migration.state === "newer") return false;
  if (s.migration.active || s.migration.progress) return true;
  return s.format !== "2" && (s.migration.state === "pending" || s.migration.state === "running");
}

/** The settings view, kept current for a page that locks on it.
 *
 *  A page reads the view once and gates its writes on the layout; nothing
 *  tells it when a background migration finishes, so a page opened while it
 *  ran stayed locked until it was left. This reads again:
 *  - every `UPGRADE_POLL_MS` while the view says the upgrade is `upgrading`
 *    (slowing to `UPGRADE_POLL_SLOW_MS` after `UPGRADE_POLL_QUICK` reads),
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
  const [installs, setInstalls] = useState(0);
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
    const timer = setTimeout(read, upgradePollDelay(reads));
    return () => clearTimeout(timer);
  }, [polling, reads, read, installs]);

  const install = useCallback((s: InferenceSettings) => {
    latest.current += 1;
    // A superseded poll cannot settle reads. Rearm even when the saved
    // response is still active, without changing the quick/slow read count.
    setInstalls((n) => n + 1);
    setSettings(s);
    setError(null);
  }, []);

  return { settings, error, settled: reads > 0, install };
}
