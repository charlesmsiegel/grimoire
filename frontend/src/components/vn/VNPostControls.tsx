import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { api, type Message, type SceneMeta, type ScenePage, type VNStage } from '../../api/client';
import { PostImagePicker, type PickerTarget } from '../PostImagePicker';
import RerollRoutePicker, { canGenerateOnRoute, NO_REROLL_ROUTE, rerollOverrides } from '../RerollRoute';
import { ROLL_SPEAKER, TRANSITION_SPEAKER } from '../play/TranscriptPost';
import { VNDialog } from './VNDialog';
import type { useVNReply } from './useVNReply';
import type { Attempt } from '../../runs/RunRegistryProvider';

type Snapshot = { page: ScenePage; meta: SceneMeta; post: Message; index: number };
type Editor = { original: string; text: string };
const reason = (error: unknown) => error instanceof Error ? error.message : String(error);

function rerollable(snapshot: Snapshot) {
  const { post, page, index, meta } = snapshot;
  if (meta.closed_by || post.role !== 'assistant' || post.speaker === ROLL_SPEAKER) return false;
  if (post.response_id) return !!post.response_can_reroll;
  let last = page.messages.length - 1;
  while (last >= 0 && page.messages[last].speaker === TRANSITION_SPEAKER) last--;
  return index === last && page.messages.some(message => message.role === 'user');
}

/** VN is a reader of transformed passages. Writes deliberately go back to a
 * fresh raw transcript and resolve the stable post ID there, never saving a
 * displayed sentence (or an old index) as the whole post. */
export function VNPostControls({ cid, sid, postId, revision, stage, reply, onChanged, onReroll, onOpen }: {
  cid: string; sid: string; postId: string; revision: unknown; stage: VNStage | null;
  reply: ReturnType<typeof useVNReply>; onChanged: () => Promise<boolean>; onReroll: (postId: string) => Attempt['readingTarget'];
  onOpen?: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [image, setImage] = useState(false);
  const [reroll, setReroll] = useState(false);
  const [guidance, setGuidance] = useState('');
  const [route, setRoute] = useState(NO_REROLL_ROUTE);
  const [pendingRoute, setPendingRoute] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  const latch = useRef(false);
  const reads = useRef(0);
  const editField = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function read(): Promise<Snapshot> {
    const [page, scenes] = await Promise.all([api.getScene(cid, sid), api.listScenes(cid)]);
    const index = page.messages.findIndex(message => message.post_id === postId);
    const meta = scenes.find(scene => scene.id === sid);
    if (index < 0 || !meta) throw new Error('This post is no longer available. Refresh the reader.');
    return { page, meta, index: index + (page.offset ?? 0), post: page.messages[index] };
  }
  useEffect(() => {
    const request = ++reads.current;
    setSnapshot(null); setError('');
    if (!postId) return;
    void read().then(value => { if (alive.current && request === reads.current) setSnapshot(value); })
      .catch(why => { if (alive.current && request === reads.current) setError(reason(why)); });
    // revision is the reader's completed refresh, not a render counter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, sid, postId, revision]);

  async function guarded(work: () => Promise<void>) {
    if (latch.current || reply.locked) return;
    latch.current = true; setBusy(true); setError('');
    try { await work(); }
    catch (why) { if (alive.current) setError(reason(why)); }
    finally { latch.current = false; if (alive.current) setBusy(false); }
  }
  async function preflight() {
    const [fresh, running, roll] = await Promise.all([read(), api.findRun(cid, sid), api.getRollProposal(cid, sid)]);
    if (!alive.current) throw new Error('Reader closed');
    if (fresh.meta.closed_by) throw new Error('This scene is closed by an absorbed branch.');
    if (running.run?.state === 'running') throw new Error('A response is still running. Wait for it before changing this post.');
    if (roll.record && !['narrated', 'superseded'].includes(roll.record.status)) throw new Error('Resolve the pending roll in normal play first.');
    return fresh;
  }
  function open(kind: 'edit' | 'image' | 'reroll') {
    onOpen?.();
    void guarded(async () => {
      const fresh = await preflight();
      setSnapshot(fresh);
      if (kind === 'reroll') {
        if (!rerollable(fresh)) throw new Error('This post cannot be rerolled.');
        setReady(false); setReroll(true);
        try {
          const view = await api.getCampaignInference(cid);
          if (alive.current) setReady(view.routes.find(item => item.key === 'scene')?.problem === null);
        } catch { /* An explicit provider override can still be chosen. */ }
      } else {
        if (fresh.post.speaker === ROLL_SPEAKER) throw new Error('Roll records cannot be edited here.');
        setEditor({ original: fresh.post.content, text: fresh.post.content });
        setImage(kind === 'image');
      }
    });
  }
  function save(retcon: boolean) {
    if (!editor) return;
    if (retcon && !window.confirm('Retcon this post?\n\nAn absorbed scene becomes unfinished and its recorded write-backs are reversed. Later posts are left alone; use replay in normal play if they no longer follow.')) return;
    void guarded(async () => {
      const fresh = await preflight();
      if (fresh.post.speaker === ROLL_SPEAKER) throw new Error('Roll records cannot be edited here.');
      if (fresh.post.content !== editor.original) throw new Error('This post changed elsewhere. Your draft is preserved; copy it before reopening the current post.');
      let report;
      if (retcon) report = await api.retconMessage(cid, sid, fresh.index, editor.text);
      else await api.editMessage(cid, sid, fresh.index, editor.text);
      if (!alive.current) return;
      setEditor(null);
      const notes = ['Post saved.'];
      if (report?.refused.length) notes.push(`Could not restore: ${report.refused.map(item => item.label).join(', ')}.`);
      if (report?.failed.length) notes.push(`Some reversals failed: ${report.failed.join(', ')}.`);
      if (report?.later.length) notes.push('Later scenes may need review in normal play.');
      setNotice(notes.join(' '));
      // As in play (#85/#84), the suggestion requests need a verified boundary.
      // A failed suggestion never turns a completed transcript edit into failure.
      const page = await api.getScene(cid, sid);
      if (!alive.current) return;
      const upto = page.total ?? page.messages.length;
      void api.refreshRollingSummary(cid, sid, false, upto).catch(() => {});
      void api.askSceneBreak(cid, sid, false, upto).catch(() => {});
      if (!await onChanged() && alive.current) setError('Post saved, but the reader could not refresh. Use Refresh in the menu.');
    });
  }
  function submitReroll() {
    if (pendingRoute || !canGenerateOnRoute(ready, route)) return;
    void guarded(async () => {
      const fresh = await preflight();
      if (!rerollable(fresh)) throw new Error('This post can no longer be rerolled.');
      if (fresh.post.content !== snapshot?.post.content || fresh.post.response_id !== snapshot.post.response_id)
        throw new Error('This post changed elsewhere. Close and reopen Reroll for the current response.');
      if (!route.provider) {
        const view = await api.getCampaignInference(cid);
        if (!alive.current) return;
        if (view.routes.find(item => item.key === 'scene')?.problem !== null)
          throw new Error('The scene route is no longer ready. Choose a provider or configure it before rerolling.');
      }
      const first = fresh.post.response_id
        ? fresh.page.messages.find(message => message.response_id === fresh.post.response_id) : fresh.post;
      const readingTarget = onReroll(first?.post_id ?? postId);
      setReroll(false);
      const body = { guidance: guidance.trim(), ...rerollOverrides(route) };
      // One durable lifecycle owns chat AND rerolls, including lost POSTs and
      // stop/check recovery. An empty text attempt must not erase a reply draft.
      await reply.run((emit, signal, attempt) => fresh.post.response_id
        ? api.regenerateResponse(cid, sid, fresh.post.response_id, emit, body, signal, attempt)
        : api.regenerate(cid, sid, emit, body, signal, attempt), readingTarget);
    });
  }
  const actor = stage?.actors.find(item => item.speaking && item.ref === snapshot?.post.actor_ref)
    ?? stage?.actors.find(item => item.speaking);
  const kind = actor?.ref.split(':')[0];
  const target: PickerTarget = actor && (kind === 'characters' || kind === 'pcs') && actor.version_id
    ? { kind, id: actor.ref.slice(kind.length + 1), version: actor.version_id, name: actor.name }
    : { kind: 'campaign', name: 'Campaign' };
  const disabled = busy || reply.locked || reply.proposal || !snapshot || !!snapshot.meta.closed_by;
  const editable = !disabled && snapshot?.post.speaker !== ROLL_SPEAKER;
  const playHref = `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;
  return <>
    <button disabled={disabled || !snapshot || !rerollable(snapshot)} onClick={() => open('reroll')}>Reroll</button>
    <button disabled={!editable} onClick={() => open('edit')}>Edit</button>
    <button className="vn-insert-image" disabled={!editable} onClick={() => open('image')}>Insert image</button>
    {!editor && !reroll && error && <p className="vn-action-status" role="alert">{error}</p>}
    {notice && <p className="vn-action-status" role="status">{notice} <Link to={playHref}>Review in normal play</Link></p>}
    {editor && <VNDialog title="Edit post" busy={busy} onClose={() => { setEditor(null); setImage(false); }}>
      <label htmlFor="vn-edit-post">Post text</label>
      <textarea data-initial-focus id="vn-edit-post" ref={editField} value={editor.text} disabled={busy}
        onChange={event => setEditor({ ...editor, text: event.target.value })} />
      {snapshot?.meta.done && <p>This scene is absorbed. Save changes the words; Retcon also reverses the scene’s recorded effects.</p>}
      {error && <p role="alert">{error}</p>}
      <div className="vn-menu-actions">
        <button disabled={busy || reply.locked} onClick={() => save(false)}>Save</button>
        <button disabled={busy || reply.locked} onClick={() => save(true)}>Retcon</button>
        <button disabled={busy} onClick={() => setImage(true)}>Insert image into post</button>
      </div>
    </VNDialog>}
    {image && editor && createPortal(<PostImagePicker cid={cid} target={target} onClose={() => { setImage(false); editField.current?.focus(); }}
      onInsert={markdown => { setEditor(value => value && ({ ...value, text: `${value.text.trimEnd()}${value.text.trim() ? '\n\n' : ''}${markdown}` })); setImage(false); editField.current?.focus(); }} />, document.body)}
    {reroll && <VNDialog title="Reroll post" busy={busy} onClose={() => setReroll(false)}>
      <RerollRoutePicker cid={cid} value={route} onChange={setRoute} onPending={setPendingRoute} />
      <label htmlFor="vn-reroll-guidance">Reroll guidance</label>
      <textarea data-initial-focus id="vn-reroll-guidance" value={guidance} onChange={event => setGuidance(event.target.value)} placeholder="What should this response do differently? (optional)" />
      {error && <p role="alert">{error}</p>}
      {!ready && !route.provider && <p>Choose a ready provider or configure the campaign’s scene route.</p>}
      <button disabled={busy || reply.locked || pendingRoute || !canGenerateOnRoute(ready, route)} onClick={submitReroll}>Reroll with guidance</button>
    </VNDialog>}
  </>;
}
