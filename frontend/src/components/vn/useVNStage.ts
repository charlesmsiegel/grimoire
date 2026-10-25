import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type VNScene, type VNStage } from '../../api/client';
import { awaitRun } from '../../api/runs';
import { expressionResult } from '../tracker/expressionResult';

/** Stage reads are independent of the sentence cursor. Every request belongs
 * to a scene/post scope and a generation: navigation and saved presentation
 * invalidate older reads, including detached tracker completions. Discovery
 * keeps polling with no active run, since a turn can reserve its tracker work
 * after the reader has already fetched the new post. */
export function useVNStage(cid: string, sid: string, scene: VNScene | null, postId: string, paused = false) {
  const [stage, setStage] = useState<VNStage | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [notice, setNotice] = useState('');
  const scope = useMemo(() => ({ scene, postId, paused, active: true }), [scene, postId, paused]);
  const current = useRef(scope); current.current = scope;
  const generation = useRef(0);
  const waiting = useRef<AbortController | null>(null);

  const read = useCallback(async () => {
    const mine = current.current;
    if (!mine.scene || !mine.postId || mine.paused) return;
    const request = ++generation.current;
    try {
      const result = await api.getVNPost(cid, sid, mine.postId);
      if (mine.active && current.current === mine && request === generation.current
        && result.scene_identity === mine.scene.scene_identity && result.post_id === mine.postId) {
        setStage(result); setReadError('');
      }
    } catch (e) {
      if (mine.active && current.current === mine && request === generation.current) setReadError(e instanceof Error ? e.message : String(e));
    }
  }, [cid, sid]);

  useEffect(() => {
    scope.active = true;
    setStage(null); setBusy(false); setError(''); setReadError(''); setNotice('');
    void read();
    return () => { scope.active = false; waiting.current?.abort(); };
  }, [scope, read]);

  useEffect(() => {
    if (!scene) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const seen = new Map<string, string>();
    setEnabled(false); setTracking(false);
    void api.getTracker(cid, sid).then(summary => { if (active) setEnabled(summary.enabled); }).catch(() => {});
    async function poll() {
      try {
        const { runs } = await api.getTrackerRuns(cid, sid);
        if (!active) return;
        const relevant = runs.filter(run => run.scene_identity === scene!.scene_identity
          && (run.kind === 'tracker-update' || run.kind === 'tracker-expressions'));
        setTracking(relevant.some(run => run.state === 'running'));
        let settled = false;
        for (const run of relevant) {
          if (run.state !== 'running' && seen.get(run.id) !== run.state) settled = true;
          seen.set(run.id, run.state);
        }
        if (settled) void read();
      } catch { /* Transient discovery failures retry without disturbing reading. */ }
      if (active) timer = setTimeout(() => void poll(), 1000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [cid, sid, scene, read]);

  function saved(updated: VNStage) {
    const mine = current.current;
    if (!mine.scene || updated.scene_identity !== mine.scene.scene_identity || updated.post_id !== mine.postId) return;
    ++generation.current;
    setStage(updated);
  }

  async function refreshExpressions() {
    const mine = current.current;
    if (!enabled || busy || tracking || !mine.scene || !mine.postId || mine.paused) return;
    const controller = new AbortController(); waiting.current = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const { run } = await api.refreshVNExpressions(cid, sid, mine.postId);
      if (current.current !== mine || controller.signal.aborted) return;
      const done = await awaitRun(cid, sid, run, controller.signal);
      if (current.current !== mine || controller.signal.aborted) return;
      setNotice(expressionResult(done.result));
      await read();
    } catch (e) {
      if (current.current === mine && !controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (current.current === mine) setBusy(false);
      if (waiting.current === controller) waiting.current = null;
    }
  }
  return { stage, enabled, busy, tracking, error: error || readError, notice, saved, refreshExpressions };
}
