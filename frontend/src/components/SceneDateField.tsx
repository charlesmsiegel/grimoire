import { useCallback, useEffect, useState } from "react";
import { api, type SceneDatetime } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { errMsg } from "./errMsg";
import { LOCKED_WHILE_GENERATING } from "./sceneLock";

/** The scene's "When" row: the current date, today's holidays, and a picker
 *  that sets or advances it. Split out of `CastPanel`.
 *
 *  The first date set RENAMES the scene file, which is why this row carries the
 *  rename plumbing (`sceneLocked`, `onRenaming`, `onRenamed`) that the rest of
 *  the panel has no use for. */
export function SceneDateField({ cid, sid, sceneLocked, onAdvanced, onRenamed, onRenaming, onError }: {
  cid: string;
  sid: string;
  /** A turn is streaming into this scene: moving its file mid-turn strands the
   *  abort write that saves the partial (#95), so the control waits. */
  sceneLocked?: boolean;
  /** The date moved without a rename — the host refreshes to show the
   *  transition line. */
  onAdvanced: () => void;
  /** The first date set re-slugged the file; the host adopts the new id. */
  onRenamed?: (id: string) => void;
  onRenaming?: (active: boolean) => void;
  onError: (msg: string | null) => void;
}) {
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [dateInput, setDateInput] = useState("");

  const reload = useCallback(
    () => api.getSceneDatetime(cid, sid).then((w) => {
      setWhen(w);
      // dateless scene with a suggestion: pre-fill the input, but never clobber typing
      if (!w.current && w.suggested) setDateInput((prev) => prev || w.suggested!);
    }).catch(() => setWhen(null)),
    [cid, sid]);

  useEffect(() => { reload(); }, [reload]);

  async function apply() {
    if (!dateInput) return;
    onError(null);
    onRenaming?.(true);      // the first date set re-slugs the file
    try {
      const res = await api.setSceneDatetime(cid, sid, dateInput);
      setDateInput("");
      if (res.id !== sid) {
        // first date set renames the scene file — adopt the new id; the sid
        // prop change re-runs every load effect, so skip the stale reload
        onRenamed?.(res.id);
        return;
      }
      await reload();
      onAdvanced(); // surface the transition line in the stream
    } catch (err: any) {
      onError(errMsg(err));
    } finally {
      onRenaming?.(false);
    }
  }

  return (
    <div>
      <div className="role">When</div>
      <div className="field-hint">
        {when?.current ? `${when.current.friendly} (${when.current.weekday})` : "No date"}
      </div>
      {when?.current?.holidays_today?.length ? (
        <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
      ) : null}
      <div className="picker">
        <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                            onChange={setDateInput} ariaLabel="Scene date" />
        {/* The first date set renames the scene file, so this is a rename
            control in disguise — locked for the same reason the rail's is. */}
        <button className="primary" onClick={apply}
                disabled={!dateInput || sceneLocked}
                title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
          {when?.current ? "Advance to" : "Set date"}
        </button>
      </div>
    </div>
  );
}
