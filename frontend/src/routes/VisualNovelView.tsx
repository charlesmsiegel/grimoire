import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type VNScene, type VNStage } from '../api/client';
import { useHotkeys } from '../shortcuts/useHotkeys';
import { RenderedMarkdown } from '../components/play/StreamingMarkdown';
import { ActorSprite } from '../components/vn/ActorSprite';
import { PresentationSettings } from '../components/vn/PresentationSettings';
import '../components/vn/vn.css';

export default function VisualNovelView() {
  const { cid = '', sid = '' } = useParams();
  // A route owns every read and save; switching scenes destroys that scope.
  return <VNReader key={`${cid}/${sid}`} cid={cid} sid={sid} />;
}
function VNReader({ cid, sid }: { cid: string; sid: string }) {
  const [scene, setScene] = useState<VNScene | null>(null);
  const [postId, setPostId] = useState('');
  const [stage, setStage] = useState<VNStage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [settings, setSettings] = useState(false);
  const [auto, setAuto] = useState(false);
  const [seconds, setSeconds] = useState(5);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const readSequence = useRef(0);
  const cursor = useRef(postId); cursor.current = postId;
  const index = scene?.posts.findIndex(p => p.post_id === postId) ?? -1;
  const post = scene?.posts[index];
  const last = !scene || index >= scene.posts.length - 1;
  const canStep = !!post && !settings;
  function move(next: number) { if (scene) { setPostId(scene.posts[next]?.post_id ?? postId); setSettings(false); } }
  async function refresh() {
    const sequence = ++readSequence.current;
    setRefreshing(true); setAuto(false); setError('');
    try {
      const result = await api.getVNScene(cid, sid);
      if (!alive.current || sequence !== readSequence.current) return;
      let desired = cursor.current;
      if (!desired || (scene && result.scene_identity !== scene.scene_identity)) { try { desired = localStorage.getItem(`vn-cursor:${result.scene_identity}`) ?? ''; } catch { desired = ''; } }
      if (desired && !result.posts.some(p => p.post_id === desired)) { setNotice('That post is no longer available. Reading from the first surviving post.'); desired = ''; }
      setScene(result); setPostId(desired || result.posts[0]?.post_id || '');
      // Refresh stage even when the stable cursor did not move.
      setStage(null);
    } catch (e) { if (alive.current && sequence === readSequence.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (alive.current && sequence === readSequence.current) setRefreshing(false); }
  }
  const refreshRef = useRef(refresh); refreshRef.current = refresh;
  useEffect(() => { alive.current = true; void refreshRef.current(); return () => { alive.current = false; }; }, []); // route-keyed lifetime
  useEffect(() => {
    setSettings(false); setStage(null);
    if (!scene || !postId) return;
    let active = true;
    try { localStorage.setItem(`vn-cursor:${scene.scene_identity}`, postId); } catch { /* Reading remains available without browser storage. */ }
    setError('');
    api.getVNPost(cid, sid, postId).then(result => {
      if (active && result.scene_identity === scene.scene_identity && result.post_id === postId) setStage(result);
    }).catch(e => { if (active) { setError(e instanceof Error ? e.message : String(e)); setAuto(false); } });
    return () => { active = false; };
  }, [cid, sid, postId, scene]);
  useEffect(() => {
    if (!auto) return;
    if (last || error || settings) { setAuto(false); return; }
    if (!stage) return;
    const timer = setTimeout(() => setPostId(scene?.posts[index + 1]?.post_id ?? postId), seconds * 1000);
    return () => clearTimeout(timer);
  }, [auto, last, error, settings, stage, index, seconds, scene, postId]);
  useHotkeys([
    { keys: 'arrowleft', label: 'Previous post', group: 'VN READER', enabled: canStep && index > 0, run: () => move(index - 1) },
    { keys: 'arrowright', label: 'Next post', group: 'VN READER', enabled: canStep && !last, run: () => move(index + 1) },
    { keys: 'home', label: 'First post', group: 'VN READER', enabled: canStep && index > 0, run: () => move(0) },
    { keys: 'end', label: 'Last post', group: 'VN READER', enabled: canStep && !last, run: () => move((scene?.posts.length ?? 1) - 1) },
  ]);
  const playHref = `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;
  return <main className="vn-reader" aria-label="Visual novel reader">
    <header className="vn-header"><Link to={playHref}>Back to play</Link><h1>{scene?.title || 'Visual novel'}</h1><button disabled={refreshing} onClick={() => void refresh()}>Refresh</button></header>
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    <section className="vn-stage" aria-label={stage?.location_name ? `Stage: ${stage.location_name}` : 'Stage: unknown location'}>
      {stage?.background_url && <img key={stage.background_url} className="vn-background" src={stage.background_url} alt="" onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />}
      <div className="vn-cast">{stage?.actors.filter(a => !a.hidden).map(a => <ActorSprite key={`${postId}:${a.ref}:${stage.revision}`} actor={a} />)}</div>
      {!stage?.actors.length && <p className="vn-neutral">{stage ? 'No recorded cast' : post ? 'Loading stage…' : 'No posts yet. Continue in play to begin.'}</p>}
      <article className="vn-dialogue" aria-label="Current post"><p className="vn-speaker">{post?.speaker || (post?.role === 'user' ? 'You' : 'Narrator')}</p>{post && <RenderedMarkdown content={post.content} />}{post?.excluded && <small>Excluded from generation context</small>}</article>
    </section>
    {[...new Set(stage?.notices ?? [])].map(n => <p role="status" key={n}>{n}</p>)}
    <nav className="vn-toolbar" aria-label="Reader controls">
      <button disabled={!canStep || index <= 0} onClick={() => move(0)}>First</button><button disabled={!canStep || index <= 0} onClick={() => move(index - 1)}>Previous</button>
      <label>Post<select aria-label="Post" disabled={!post || settings} value={postId} onChange={e => { setPostId(e.target.value); setAuto(false); }}>{scene?.posts.map((p, i) => <option key={p.post_id} value={p.post_id}>{i + 1} · {p.speaker || p.role}</option>)}</select></label>
      <button disabled={!canStep || last} onClick={() => move(index + 1)}>Next</button><button disabled={!canStep || last} onClick={() => move((scene?.posts.length ?? 1) - 1)}>Last</button>
      <button disabled={!post || last || settings || !!error} aria-pressed={auto} onClick={() => setAuto(a => !a)}>{auto ? 'Stop autoplay' : 'Start autoplay'}</button>
      <label>Seconds per post<select aria-label="Seconds per post" value={seconds} onChange={e => setSeconds(Number(e.target.value))}>{[3, 5, 10, 20].map(s => <option key={s} value={s}>{s}</option>)}</select></label>
      <button disabled={!stage} onClick={() => { setAuto(false); setSettings(s => !s); }}>Presentation settings</button>
    </nav>
    {settings && stage && <PresentationSettings key={`${scene?.scene_identity}:${postId}`} cid={cid} sid={sid} stage={stage} onSaved={setStage} onClose={() => setSettings(false)} />}
  </main>;
}
