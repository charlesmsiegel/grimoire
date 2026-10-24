import { useEffect, useState, type ReactNode } from 'react';
import { api, type VNStage, type VNOverride, type VNActorOverride } from '../../api/client';
import { useHotkeys } from '../../shortcuts/useHotkeys';
export function PresentationSettings({ stage, cid, sid, showPCSprites, onShowPCSpritesChange, onSaved, onClose }: {
  stage: VNStage; cid: string; sid: string; showPCSprites: boolean;
  onShowPCSpritesChange: (show: boolean) => void; onSaved: (stage: VNStage) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState<VNOverride>(() => structuredClone(stage.override));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // Cleanup invalidates saves after this post's settings leave the screen.
  const [scope] = useState(() => ({ active: true }));
  useHotkeys([{ keys: 'escape', run: onClose }], { modal: true });
  function actorChange(ref: string, patch: VNActorOverride) { setDraft(d => ({ ...d, actors: { ...d.actors, [ref]: { ...d.actors[ref], ...patch } } })); }
  async function save(reset: boolean) {
    setBusy(true); setError('');
    try { const saved = await api.saveVNPresentation(cid, sid, stage.post_id, { scene_identity: stage.scene_identity, expected_revision: stage.revision, ...(reset ? { actors: {}, background: null } : draft) }); if (scope.active) { setDraft(structuredClone(saved.override)); onSaved(saved); } }
    catch (e) { if (scope.active) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (scope.active) setBusy(false); }
  }
  return <SettingsLifetime scope={scope}><aside className="vn-settings" aria-label="Presentation settings" aria-busy={busy}>
    <h2>Presentation</h2>
    <fieldset><legend>Reader</legend>
      <label><input type="checkbox" checked={showPCSprites} onChange={e => onShowPCSpritesChange(e.target.checked)} />Show PC sprites</label>
      <small>Applies immediately across scenes. Remembered in this browser.</small>
    </fieldset>
    <h3>This post</h3><p>Appearance for this post only. Automatic follows its recorded scene.</p>
    {stage.actors.map(actor => { const choice = draft.actors[actor.ref] ?? {}; const outfits = actor.outfits.filter(o => o.id === (choice.outfit || actor.automatic_outfit)); const expressions = [...new Set(outfits.flatMap(o => o.expressions))]; return <fieldset key={actor.ref} disabled={busy}><legend>{actor.name}</legend>
      <label>Outfit<select value={choice.outfit ?? ''} onChange={e => actorChange(actor.ref, { outfit: e.target.value || undefined, expression: undefined })}><option value="">Automatic</option>{actor.outfits.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>
      <label>Expression<select value={choice.expression ?? ''} onChange={e => actorChange(actor.ref, { expression: e.target.value || undefined })}><option value="">Automatic</option>{expressions.map(expression => <option key={expression}>{expression}</option>)}</select></label>
      <label><input type="checkbox" checked={choice.hidden ?? false} onChange={e => actorChange(actor.ref, { hidden: e.target.checked })} />Hide {actor.name}</label>
    </fieldset>; })}
    <label>Background<select aria-label="Background" disabled={busy} value={draft.background ?? ''} onChange={e => setDraft(d => ({ ...d, background: e.target.value || null }))}><option value="">Automatic</option><option value="none">None</option>{stage.backgrounds.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}</select></label>
    {error && <p role="alert">{error}</p>}<button disabled={busy} onClick={() => void save(false)}>Save presentation</button><button disabled={busy} onClick={() => void save(true)}>Reset to automatic</button><button onClick={onClose}>Close settings</button>
  </aside></SettingsLifetime>;
}
function SettingsLifetime({ scope, children }: { scope: { active: boolean }; children: ReactNode }) { useEffect(() => { scope.active = true; return () => { scope.active = false; }; }, [scope]); return children; }
