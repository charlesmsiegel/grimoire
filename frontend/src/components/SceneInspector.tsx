import { useEffect, useMemo, useState } from "react";
import { api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry } from "../api/client";
import { fetchModels, type Model } from "../api/models";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";

export function SceneInspector({ cid, sid, refreshKey }: { cid: string; sid: string; refreshKey: number }) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [ctx, setCtx] = useState<SceneContext | null>(null);
  const [recap, setRecap] = useState<ChronicleEntry[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      Promise.all([api.listCharacters(c.meta.world), api.listPCs(c.meta.world), api.listCampaignPCs(cid)])
        .then(([chars, worldPCs, localPCs]) => {
          const m: Record<string, string> = {};
          for (const x of chars) m[`characters/${x.id}`] = x.name;
          for (const x of [...worldPCs, ...localPCs]) m[`pcs/${x.id}`] = x.name;
          setNames(m);
        });
    });
    fetchModels().then(setModels).catch(() => setModels([]));
  }, [cid]);

  useEffect(() => {
    api.getCast(cid, sid).then(setCast).catch(() => setCast([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));
    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
  }, [cid, sid, refreshKey]);

  const ctxLen = useMemo(
    () => models.find((m) => m.id === ctx?.model)?.context ?? 0,
    [models, ctx]);

  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;

  return (
    <aside className="inspector">
      {recap.length > 0 && (
        <div className="side-section">
          <h4>Story so far</h4>
          {[...recap].reverse().map((r) => (
            <div className="field-hint" key={r.id}>{r.one_line || r.summary}</div>
          ))}
        </div>
      )}
      <div className="side-section">
        <h4>Active characters</h4>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => (
          <button key={`${a.kind}/${a.id}`} className="inspector-row"
                  onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
            {nameOf(a)} <span className="role">{a.role}</span>
          </button>
        ))}
      </div>

      <div className="side-section">
        <h4>Location</h4>
        {setting?.current
          ? <button className="inspector-row" onClick={() => setDrawer({ type: "location", id: setting.current!.id })}>
              {setting.current.name}
            </button>
          : <div className="field-hint">No setting</div>}
      </div>

      <div className="side-section">
        <h4>Context {ctx ? `· ${ctx.total_tokens.toLocaleString()} tok${pct(ctx.total_tokens)}` : ""}</h4>
        <div className="field-hint">token estimates</div>
        {ctx?.sections.map((s) => (
          <details className="ctx-section" key={s.label}>
            <summary>{s.label} <span className="role">{s.tokens.toLocaleString()} tok{pct(s.tokens)}</span></summary>
            <pre className="ctx-text">{s.text}</pre>
          </details>
        ))}
      </div>

      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
}
