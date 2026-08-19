import { useCallback, useEffect, useState } from "react";
import { api, type EntitySummary, type SceneLocation } from "../api/client";
import { errMsg } from "./errMsg";

/** The scene's "Setting" row: where it is now, and a picker that moves it.
 *  Split out of `CastPanel`, which was carrying five of these inline. */
export function SceneSettingField({ cid, sid, onMoved, onError }: {
  cid: string;
  sid: string;
  /** A move appends a transition line, so the host refreshes the stream. */
  onMoved: () => void;
  onError: (msg: string | null) => void;
}) {
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locId, setLocId] = useState("");

  const reload = useCallback(
    () => api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null)),
    [cid, sid]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then(setLocations).catch(() => setLocations([]));
  }, [cid]);

  async function setLocation() {
    if (!locId) return;
    onError(null);
    try {
      await api.setSceneLocation(cid, sid, locId);
      setLocId("");
      await reload();
      onMoved();
    } catch (err: any) {
      onError(errMsg(err));
    }
  }

  return (
    <div>
      <div className="role">Setting</div>
      <div className="field-hint">{setting?.current ? setting.current.name : "No setting"}</div>
      <div className="picker">
        <select aria-label="Location" value={locId} onChange={(e) => setLocId(e.target.value)}>
          <option value="">— pick —</option>
          {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <button className="primary" onClick={setLocation}
                disabled={!locId || locId === setting?.current?.id}>
          {setting?.current ? "Move here" : "Set location"}
        </button>
      </div>
    </div>
  );
}
