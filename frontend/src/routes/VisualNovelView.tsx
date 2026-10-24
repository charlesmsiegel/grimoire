import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type VNScene, type VNStage } from '../api/client';
import { useHotkeys, useOtherModalOpen } from '../shortcuts/useHotkeys';
import { ActorSprite } from '../components/vn/ActorSprite';
import { PresentationSettings } from '../components/vn/PresentationSettings';
import { readingSteps, type Splash } from '../components/vn/readingSteps';
import { ReadingStepContent } from '../components/vn/ReadingStep';
import { useVNReply } from '../components/vn/useVNReply';
import { VNReply } from '../components/vn/VNReply';
import { VNDialog } from '../components/vn/VNDialog';
import { nativeControl, useVNNavigation } from '../components/vn/useVNNavigation';
import { VNPostControls } from '../components/vn/VNPostControls';
import type { Attempt } from '../runs/RunRegistryProvider';
import '../components/vn/vn.css';

type Position = { postId: string; step: number };
const beginning: Position = { postId: '', step: 0 };
function bookmark(identity: string): Position {
  try {
    const raw = localStorage.getItem(`vn-cursor:${identity}`) || '';
    try {
      const saved: unknown = JSON.parse(raw);
      if (saved && typeof saved === 'object' && 'postId' in saved && 'step' in saved
        && typeof saved.postId === 'string' && typeof saved.step === 'number' && Number.isSafeInteger(saved.step) && saved.step >= 0) {
        return { postId: saved.postId, step: saved.step };
      }
    } catch { /* Older versions stored only a stable post ID. */ }
    return { postId: raw, step: 0 };
  } catch { return beginning; }
}

export default function VisualNovelView() {
  const { cid = '', sid = '' } = useParams();
  // A route owns every read and save; switching scenes destroys that scope.
  return <VNReader key={`${cid}/${sid}`} cid={cid} sid={sid} />;
}

function VNReader({ cid, sid }: { cid: string; sid: string }) {
  const [scene, setScene] = useState<VNScene | null>(null);
  const [position, setPosition] = useState<Position>(beginning);
  const [stage, setStage] = useState<VNStage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [settings, setSettings] = useState(false);
  const [menu, setMenu] = useState(false);
  const [hidden, setHidden] = useState(false);
  const modal = useOtherModalOpen();
  const root = useRef<HTMLElement>(null);
  // This is a reader preference, independent of a historical post's saved
  // outfit/visibility overrides. A new browser starts with the player offstage.
  const [showPCSprites, setShowPCSprites] = useState(() => {
    try { return localStorage.getItem('vn-show-pc-sprites') === 'true'; }
    catch { return false; }
  });
  const [auto, setAuto] = useState(false);
  const [seconds, setSeconds] = useState(5);
  const [refreshing, setRefreshing] = useState(false);
  const [failedSplash, setFailedSplash] = useState('');
  const [dismissedSplash, setDismissedSplash] = useState('');
  const [carry, setCarry] = useState<{ key: string; allowed: boolean } | null>(null);
  const alive = useRef(true);
  const readSequence = useRef(0);
  const cursor = useRef(position);
  const dialogue = useRef<HTMLDivElement>(null);
  const sequence = useMemo(() => {
    let inherited: Splash | undefined;
    return scene?.posts.flatMap(post => {
      const result = readingSteps(post.content, post.post_id, inherited);
      inherited = result.trailingSplash;
      return result.steps.map((step, number) => ({ ...step, post, number }));
    }) ?? [];
  }, [scene]);
  const postSteps = sequence.filter(s => s.post.post_id === position.postId);
  const current = postSteps[Math.min(position.step, postSteps.length - 1)] ?? sequence[0];
  const index = current ? sequence.indexOf(current) : -1;
  const post = current?.post;
  const postId = post?.post_id ?? '';
  cursor.current = current ? { postId, step: current.number } : position;
  const first = index <= 0;
  const last = index >= sequence.length - 1;
  // Do not paint a previous post's stage during the render before its effect
  // cleans up. The asynchronous response also carries both stable identities.
  const shownStage = stage?.post_id === postId && stage.scene_identity === scene?.scene_identity ? stage : null;
  const splash = current?.splash;
  const carryKey = `${postId}/${splash?.id ?? ''}`;
  const splashVisible = shownStage && splash && splash.id !== failedSplash && splash.id !== dismissedSplash
    && (splash.postId === postId || (carry?.key === carryKey && carry.allowed));

  function changePCSprites(show: boolean) {
    setShowPCSprites(show);
    try { localStorage.setItem('vn-show-pc-sprites', String(show)); }
    catch { /* The preference still works for this reader without storage. */ }
  }

  function move(next: number, manual = true) {
    const target = sequence[next];
    if (!target) return;
    if (manual) setAuto(false);
    setPosition({ postId: target.post.post_id, step: target.number });
  }
  async function refresh(advance = false, rerollStart?: Attempt['readingTarget']) {
    const request = ++readSequence.current;
    setRefreshing(true); setAuto(false); setError(''); setNotice('');
    try {
      const result = await api.getVNScene(cid, sid);
      if (!alive.current || request !== readSequence.current) return false;
      let desired = cursor.current;
      if (advance && scene?.scene_identity === result.scene_identity) {
        const known = new Set(scene.posts.map(p => p.post_id));
        const appended = result.posts.filter(p => !known.has(p.post_id));
        const next = appended.find(p => p.role !== 'user') ?? appended[0];
        if (next) desired = { postId: next.post_id, step: 0 };
      }
      if (advance && rerollStart?.sceneIdentity === result.scene_identity) {
        const start = result.posts.find(p => p.post_id === rerollStart.postId)
          ?? result.posts[Math.min(rerollStart.index, result.posts.length - 1)];
        if (start) desired = { postId: start.post_id, step: 0 };
      }
      if (!desired.postId || (scene && result.scene_identity !== scene.scene_identity)) desired = bookmark(result.scene_identity);
      if (desired.postId && !result.posts.some(p => p.post_id === desired.postId)) {
        setNotice('That post is no longer available. Reading from the first surviving post.'); desired = beginning;
      }
      setScene(result); setPosition(desired.postId ? desired : { postId: result.posts[0]?.post_id ?? '', step: 0 });
      setStage(null); setCarry(null); setFailedSplash(''); setDismissedSplash('');
      return true;
    } catch (e) { if (alive.current && request === readSequence.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (alive.current && request === readSequence.current) setRefreshing(false); }
    return false;
  }
  const reply = useVNReply(cid, sid, async target => {
    if (!await refresh(true, target)) throw new Error('Could not load the reply. Check reply to try again.');
  });
  const canStep = !!post && !settings && !menu && !hidden && !modal && !reply.locked && !refreshing;
  const canJump = !!post && !hidden && !reply.locked && !refreshing;
  function showMenu() { setAuto(false); setMenu(true); }
  function hideDialogue() { setAuto(false); setHidden(h => !h); }
  const advanceDialogue = useVNNavigation(root, {
    enabled: canStep, hidden, blocked: modal || settings || menu || reply.locked || refreshing,
    move: direction => move(index + direction), show: () => setHidden(false),
  });
  const refreshRef = useRef(refresh); refreshRef.current = refresh;
  useEffect(() => { alive.current = true; void refreshRef.current(); return () => { alive.current = false; }; }, []); // route-keyed lifetime
  useEffect(() => {
    if (!scene || !current) return;
    const bounded = { postId, step: current.number };
    cursor.current = bounded;
    try { localStorage.setItem(`vn-cursor:${scene.scene_identity}`, JSON.stringify(bounded)); } catch { /* Reading remains available without browser storage. */ }
    if (dialogue.current) dialogue.current.scrollTop = 0;
  }, [scene, current, postId]);
  useEffect(() => {
    setSettings(false); setStage(null);
    if (!scene || !postId) return;
    let active = true;
    setError('');
    api.getVNPost(cid, sid, postId).then(result => {
      if (active && result.scene_identity === scene.scene_identity && result.post_id === postId) setStage(result);
    }).catch(e => { if (active) { setError(e instanceof Error ? e.message : String(e)); setAuto(false); } });
    return () => { active = false; };
  }, [cid, sid, postId, scene]);
  useEffect(() => {
    setCarry(null);
    if (!scene || !splash || splash.postId === postId || !shownStage) return;
    let active = true;
    const from = scene.posts.findIndex(p => p.post_id === splash.postId);
    const to = scene.posts.findIndex(p => p.post_id === postId);
    // A bookmark may open directly after a trailing illustration. Resolve all
    // crossed stages, not just a cache of whatever the reader visited before.
    const crossed = scene.posts.slice(from, to);
    void Promise.all(crossed.map(p => api.getVNPost(cid, sid, p.post_id))).then(results => {
      if (active) setCarry({ key: carryKey, allowed: results.every((s, i) => s.scene_identity === scene.scene_identity
        && s.post_id === crossed[i].post_id && s.background_url === shownStage.background_url) });
    }).catch(() => { if (active) setCarry({ key: carryKey, allowed: false }); });
    return () => { active = false; };
  }, [cid, sid, scene, splash, postId, shownStage, carryKey]);
  useEffect(() => {
    if (!auto) return;
    if (last || error || settings || menu || hidden || modal || reply.locked) { setAuto(false); return; }
    if (!shownStage || (splash && splash.postId !== postId && carry?.key !== carryKey)) return;
    const target = sequence[index + 1];
    const timer = setTimeout(() => setPosition({ postId: target.post.post_id, step: target.number }), seconds * 1000);
    return () => clearTimeout(timer);
  }, [auto, last, error, settings, menu, hidden, modal, reply.locked, shownStage, splash, postId, carry, carryKey, seconds, sequence, index]);
  useHotkeys([
    { keys: 'arrowleft', label: 'Previous passage', group: 'VN READER', enabled: canStep && !first, run: () => move(index - 1) },
    { keys: 'arrowright', label: 'Next passage', group: 'VN READER', enabled: canStep && !last, run: () => move(index + 1) },
    { keys: 'home', label: 'First passage', group: 'VN READER', enabled: canStep && !first, run: () => move(0) },
    { keys: 'end', label: 'Last passage', group: 'VN READER', enabled: canStep && !last, run: () => move(sequence.length - 1) },
    { keys: 'enter', label: 'Advance passage', group: 'VN READER',
      get enabled() { return canStep && !last && !document.activeElement?.closest(nativeControl); }, run: () => move(index + 1) },
    { keys: 'h', label: 'Hide/show dialogue', group: 'VN READER', enabled: !settings && !menu, run: hideDialogue },
    { keys: 'escape', label: 'Open VN menu', group: 'VN READER', whileTyping: true, run: showMenu },
  ]);
  function menuMove(next: number) { move(next); setMenu(false); }
  const playHref = `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;
  return <main ref={root} className="vn-reader" aria-label="Visual novel reader">
    {/* The stage click complements the native Next button and registered keys. */}
    {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events */}
    <section onClick={advanceDialogue} className={`vn-stage${splashVisible ? ' vn-stage-illustration' : ''}`} aria-label={shownStage?.location_name ? `Stage: ${shownStage.location_name}` : 'Stage: unknown location'}>
      {!splashVisible && shownStage?.background_url && <img key={shownStage.background_url} className="vn-background" src={shownStage.background_url} alt="" onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />}
      {splashVisible && <div className="vn-illustration"><img key={splash.id} className="vn-splash" src={splash.url} alt={splash.alt} onError={() => setFailedSplash(splash.id)} /></div>}
      <header className="vn-header"><h1>{scene?.title || 'Visual novel'}</h1></header>
      {!splashVisible && <div className="vn-cast">{shownStage?.actors.filter(a => !a.hidden && (showPCSprites || !a.ref.startsWith('pcs:'))).map(a => <ActorSprite key={`${postId}:${a.ref}:${shownStage.revision}`} actor={a} />)}</div>}
      {!splashVisible && !shownStage?.actors.length && <p className="vn-neutral">{shownStage ? 'No recorded cast' : post ? 'Loading stage…' : 'No posts yet. Write your reply to begin.'}</p>}
      {hidden && <button className="vn-show-dialogue" onClick={() => setHidden(false)}>Show dialogue</button>}
      <div className="vn-dialogue-panel" hidden={hidden}>
        {/* Native Next and registered arrows provide keyboard access. Prose
            stays selectable and links semantic instead of nesting in a button. */}
        <article className="vn-dialogue" aria-label="Current post">
          <div className="vn-speaker-line"><p className="vn-speaker">{post?.speaker || (post?.role === 'user' ? 'You' : 'Narrator')}</p>
            <span className="vn-progress">{current ? `${current.number + 1} / ${postSteps.length}` : ''}</span></div>
          <div className="vn-dialogue-text" ref={dialogue} aria-live="polite">
            {current && <ReadingStepContent step={current} />}
            {current && !splash && !current.tree.children.some(n => n.type !== 'definition' && n.type !== 'footnoteDefinition') && <p>No text in this post.</p>}
            {post?.excluded && <small>Excluded from generation context</small>}
          </div>
        </article>
        {scene && !settings && <VNReply reply={reply} playHref={playHref} canReply={last} />}
        <nav className="vn-toolbar" aria-label="Reader controls">
            <Link to={playHref}>Exit VN</Link>
            <button disabled={!canStep || last || !!error} aria-label={auto ? 'Stop autoplay' : 'Start autoplay'} aria-pressed={auto} onClick={() => setAuto(a => !a)}>Auto</button>
            {scene && postId && <VNPostControls key={postId} cid={cid} sid={sid} postId={postId} revision={scene} stage={shownStage} reply={reply}
              onOpen={() => setAuto(false)} onChanged={() => refresh()} onReroll={id => { setAuto(false); return { sceneIdentity: scene.scene_identity, postId: id, index: Math.max(0, scene.posts.findIndex(p => p.post_id === id)) }; }} />}
            <button onClick={showMenu}>Menu</button>
          <button className="vn-continue" aria-label="Next" disabled={!canStep || last} onClick={() => move(index + 1)}><span aria-hidden="true">▸</span></button>
        </nav>
        <div className="vn-notices">{notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
          {[...new Set(shownStage?.notices ?? [])].map(n => <p role="status" key={n}>{n}</p>)}</div>
      </div>
      {menu && <VNDialog title="VN menu" onClose={() => setMenu(false)}>
        <div className="vn-menu-actions">
          <button disabled={!shownStage || reply.locked} onClick={() => { setMenu(false); setSettings(true); }}>Presentation settings</button>
          <button onClick={() => { setMenu(false); hideDialogue(); }}>{hidden ? 'Show dialogue' : 'Hide dialogue'}</button>
          <button disabled={refreshing || reply.locked} onClick={() => { setMenu(false); void refresh(); }}>Refresh</button>
        </div>
        <label>Seconds per passage<select aria-label="Seconds per passage" value={seconds} onChange={e => setSeconds(Number(e.target.value))}>{[3, 5, 10, 20].map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <div className="vn-menu-actions">
          <button disabled={!canJump || first} onClick={() => menuMove(0)}>First</button>
          <button disabled={!canJump || first} onClick={() => menuMove(index - 1)}>Previous</button>
          <button disabled={!canJump || last} onClick={() => menuMove(index + 1)}>Next passage</button>
          <button disabled={!canJump || last} onClick={() => menuMove(sequence.length - 1)}>Last</button>
        </div>
        <label>Post<select aria-label="Post" disabled={!canJump} value={postId} onChange={e => menuMove(sequence.findIndex(s => s.post.post_id === e.target.value))}>
          {scene?.posts.map((p, i) => <option key={p.post_id} value={p.post_id}>{i + 1} · {p.speaker || p.role}</option>)}
        </select></label>
        <p>Click, Enter or wheel down: advance. Wheel up or Left: previous. Right: next. Home/End: first/last.</p>
        <p>H: hide/show dialogue. Esc: menu. In your reply, Enter sends and Shift+Enter adds a line.</p>
      </VNDialog>}
      {settings && shownStage && <PresentationSettings key={`${scene?.scene_identity}:${postId}`} cid={cid} sid={sid} stage={shownStage}
        showPCSprites={showPCSprites} onShowPCSpritesChange={changePCSprites} onSaved={updated => {
        if (updated.background_url !== shownStage.background_url && splash) setDismissedSplash(splash.id);
        setStage(updated);
      }} onClose={() => setSettings(false)} />}
    </section>
  </main>;
}
