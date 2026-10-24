import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { ChatEvent } from '../../api/stream';
import { awaitRun } from '../../api/runs';
import { ApiError, RunEndedError } from '../../api/transport';
import { isAbortError, newAttemptId, type RunHandle } from '../../api/stream';
import { useRunRegistry, type Attempt } from '../../runs/RunRegistryProvider';

type Phase = 'checking' | 'ready' | 'sending' | 'waiting' | 'stopping' | 'blocked';
type Outcome = { done: boolean; error: string };
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** VN reads durable posts in sentence steps, so it waits for a turn to settle
 *  instead of rendering deltas that would keep changing those step boundaries.
 *  The registry still owns pending words, just as it does in ordinary play. */
export function useVNReply(cid: string, sid: string, onSettled: (target?: Attempt['readingTarget']) => Promise<void>) {
  const registry = useRunRegistry();
  const [text, setText] = useState(() => registry.parked(cid, sid) ?? '');
  const [phase, setPhase] = useState<Phase>('checking');
  const [error, setError] = useState('');
  const [proposal, setProposal] = useState(false);
  const pending = useRef<Attempt | undefined>(registry.pending(cid, sid));
  const discovered = useRef<RunHandle | null>(null);
  const operation = useRef<AbortController | null>(null);
  const locked = useRef(true);
  const needsRefresh = useRef(false);
  const readingTarget = useRef<Attempt['readingTarget']>(pending.current?.readingTarget);
  const settled = useRef(onSettled); settled.current = onSettled;

  function begin(next: Phase) {
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    locked.current = true;
    setPhase(next); setError('');
    return controller;
  }
  function current(controller: AbortController) {
    return operation.current === controller && !controller.signal.aborted;
  }
  function assertCurrent(controller: AbortController) {
    if (!current(controller)) throw new DOMException('Reader closed', 'AbortError');
  }
  function failed(controller: AbortController, why: unknown) {
    if (!current(controller) || isAbortError(why)) return;
    setError(message(why)); setPhase('blocked');
    // Unknown is not failed: another send remains locked until Check reply
    // has established what the server kept. The registry is left intact.
  }

  async function wait(controller: AbortController, run: RunHandle | null) {
    if (!run) return { success: false, error: '' };
    discovered.current = run;
    if (run.state === 'running') {
      setPhase('waiting');
      try {
        run = await awaitRun(cid, sid, run, controller.signal);
      } catch (why) {
        assertCurrent(controller);
        if (why instanceof RunEndedError) return { success: false, error: message(why) };
        // A reaped run is terminal, but its post's durability still has to be
        // asked below. A failed poll other than 404 proves nothing about it.
        if (why instanceof ApiError && why.status === 404) return { success: false, error: '' };
        throw why;
      }
      assertCurrent(controller);
    }
    return { success: run.state === 'landed', error: run.error?.detail ?? (run.state === 'cancelled' ? 'Reply stopped.' : '') };
  }

  async function reconcile(controller: AbortController, outcome?: Outcome, cancelled?: RunHandle | null) {
    const held = pending.current;
    const terminal = !!(outcome?.done || outcome?.error);
    let run = cancelled;
    if (run === undefined && !terminal) {
      run = (await api.findRun(cid, sid, held?.attempt, controller.signal)).run;
      assertCurrent(controller);
    }
    // A lost POST can still be in setup before it reserves a run. Fence that
    // attempt before treating an empty discovery as permission to send again.
    if (held && !terminal && run == null && cancelled === undefined) {
      run = (await api.cancelAttempt(cid, sid, held.attempt)).run;
      assertCurrent(controller);
    }
    const result = await wait(controller, run ?? null);
    assertCurrent(controller);
    if (held) {
      const durable = await api.attemptState(cid, sid, held.attempt);
      assertCurrent(controller);
      if (durable.run?.state === 'running') throw new Error('The reply is still running. Check reply to reconnect.');
      if (durable.retained || outcome?.done && !outcome.error || result.success) {
        registry.settle(cid, sid); if (held.text) setText('');
      } else {
        registry.recovered(cid, sid); if (held.text) setText(held.text);
      }
      pending.current = undefined;
    }
    if (held || run) needsRefresh.current = true;
    if (held) {
      // The attempt lookup answers only for this client's send. A newer turn
      // from another client can already own the scene while that older record
      // is still retained, so account for the scene's current work as well.
      const latest = (await api.findRun(cid, sid, undefined, controller.signal)).run;
      assertCurrent(controller);
      if (latest?.state === 'running') {
        const following = await wait(controller, latest);
        assertCurrent(controller);
        result.error ||= following.error;
      }
    }
    if (needsRefresh.current) {
      await settled.current(readingTarget.current);
      assertCurrent(controller);
      needsRefresh.current = false;
      readingTarget.current = undefined;
    }
    const { record: roll } = await api.getRollProposal(cid, sid);
    assertCurrent(controller);
    setProposal(!!roll && roll.status !== 'superseded' && roll.status !== 'narrated');
    setError(outcome?.error || result.error);
    discovered.current = null;
    locked.current = false; setPhase('ready');
  }

  async function check() {
    const controller = begin('checking');
    try { await reconcile(controller); }
    catch (why) { failed(controller, why); }
  }
  const checkRef = useRef(check); checkRef.current = check;
  useEffect(() => {
    void checkRef.current();
    return () => { operation.current?.abort(); operation.current = null; };
  }, []); // The parent reader is keyed by campaign and scene.

  async function execute(start: (emit: (event: ChatEvent) => void, signal: AbortSignal, attempt: string) => Promise<void>, content: string, target?: Attempt['readingTarget']) {
    if (locked.current || proposal) return;
    const controller = begin('sending');
    const held: Attempt = { cid, sid, text: content, attempt: newAttemptId(), runId: null,
      ...(!content ? { draft: text } : {}), ...(target ? { readingTarget: target } : {}) };
    readingTarget.current = target;
    pending.current = held;
    registry.begin(held);
    const outcome: Outcome = { done: false, error: '' };
    let connectionError = '';
    try {
      try {
        await start(event => {
          if (!current(controller)) return;
          if (event.run) { discovered.current = event.run; registry.attach(cid, sid, event.run.id); }
          if (event.done) outcome.done = true;
          if (event.error) outcome.error = event.error.detail;
        }, controller.signal, held.attempt);
      } catch (why) {
        assertCurrent(controller);
        connectionError = message(why);
      }
      assertCurrent(controller);
      await reconcile(controller, outcome);
      if (connectionError && current(controller)) setError(connectionError);
    } catch (why) { failed(controller, why); }
  }

  async function send() {
    const content = text.trim();
    if (!content) return;
    await execute((emit, signal, attempt) => api.chat(cid, sid, content, emit, undefined, signal, attempt), content);
  }
  async function run(start: (emit: (event: ChatEvent) => void, signal: AbortSignal, attempt: string) => Promise<void>, target?: Attempt['readingTarget']) {
    await execute(start, '', target);
  }

  async function stop() {
    const held = pending.current;
    const run = discovered.current;
    if (!held && !run) return;
    const controller = begin('stopping');
    try {
      const cancelled = held
        ? await api.cancelAttempt(cid, sid, held.attempt)
        : await api.cancelRun(cid, sid, run!.id);
      assertCurrent(controller);
      await reconcile(controller, undefined, cancelled.run);
    } catch (why) { failed(controller, why); }
  }

  return { text, setText, phase, error, proposal, send, run, stop, check,
    locked: phase !== 'ready', canStop: !!pending.current || !!discovered.current };
}
