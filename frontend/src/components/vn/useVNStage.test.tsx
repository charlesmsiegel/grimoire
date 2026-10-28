import { act, renderHook, waitFor } from '@testing-library/react';
import { useLayoutEffect, useRef } from 'react';
import { api, type VNStage } from '../../api/client';
import { awaitRun } from '../../api/runs';
import { useVNStage } from './useVNStage';

vi.mock('../../api/client', () => ({ api: { getVNPost: vi.fn(), getTrackerRuns: vi.fn(), getTracker: vi.fn(), refreshVNExpressions: vi.fn() } }));
vi.mock('../../api/runs', () => ({ awaitRun: vi.fn() }));
const scene = { scene_identity: 'scene-one', title: 'Saltmarch', posts: [] };
const stage: VNStage = { scene_identity: 'scene-one', post_id: 'a', revision: '0', actors: [], backgrounds: [], background_url: null, location_name: null, notices: [], override: { actors: {}, background: null } };
const run = { id: 'expressions', kind: 'tracker-expressions', cls: 'background' as const, scene_identity: 'scene-one', state: 'landed' as const, attempt_id: null, next_index: 0 };
beforeEach(() => {
  vi.mocked(api.getVNPost).mockReset().mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id }));
  vi.mocked(api.getTrackerRuns).mockReset().mockResolvedValue({ runs: [] });
  vi.mocked(api.getTracker).mockReset().mockResolvedValue({ enabled: true } as never);
  vi.mocked(api.refreshVNExpressions).mockReset().mockResolvedValue({ run });
  vi.mocked(awaitRun).mockReset().mockResolvedValue({ ...run, result: { selected: 1, skipped: 0, failed: 0 } });
});

test('discovers work completed between polls and reloads only the stage', async () => {
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.stage).toEqual(stage));
  vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, revision: '1' });
  vi.mocked(api.getTrackerRuns).mockResolvedValue({ runs: [run] });
  await waitFor(() => expect(result.current.stage?.revision).toBe('1'), { timeout: 2500 });
  expect(api.refreshVNExpressions).not.toHaveBeenCalled();
});

test('a background read cannot overwrite a saved presentation', async () => {
  let complete!: (value: VNStage) => void;
  vi.mocked(api.getTrackerRuns).mockResolvedValue({ runs: [run] });
  vi.mocked(api.getVNPost).mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(api.getVNPost).toHaveBeenCalled());
  act(() => result.current.saved({ ...stage, revision: 'saved' }));
  await act(async () => complete({ ...stage, revision: 'obsolete' }));
  expect(result.current.stage?.revision).toBe('saved');
});

test('navigation invalidates a delayed background stage', async () => {
  let complete!: (value: VNStage) => void;
  vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => id === 'a'
    ? new Promise(resolve => { complete = resolve; }) : { ...stage, post_id: id });
  const { result, rerender } = renderHook(({ post }) => useVNStage('c', 's', scene, post), { initialProps: { post: 'a' } });
  await waitFor(() => expect(api.getVNPost).toHaveBeenCalled());
  rerender({ post: 'b' });
  await waitFor(() => expect(result.current.stage?.post_id).toBe('b'));
  await act(async () => complete(stage));
  expect(result.current.stage?.post_id).toBe('b');
});

test('explicit expression refresh awaits completion and exposes results', async () => {
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.enabled).toBe(true));
  await act(async () => result.current.refreshExpressions());
  expect(api.refreshVNExpressions).toHaveBeenCalledWith('c', 's', 'a');
  expect(awaitRun).toHaveBeenCalledWith('c', 's', run, expect.any(AbortSignal));
  expect(result.current.notice).toMatch(/1 selected/);
  expect(result.current.busy).toBe(false);
});

test('disabled tracker prevents paid refresh and server errors remain visible', async () => {
  vi.mocked(api.getTracker).mockResolvedValue({ enabled: false } as never);
  const { result, unmount } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.stage).toEqual(stage));
  await act(async () => result.current.refreshExpressions());
  expect(api.refreshVNExpressions).not.toHaveBeenCalled();
  unmount();
  vi.mocked(api.getTracker).mockResolvedValue({ enabled: true } as never);
  vi.mocked(api.refreshVNExpressions).mockRejectedValue(new Error('Generate tracker state first.'));
  const next = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(next.result.current.enabled).toBe(true));
  await act(async () => next.result.current.refreshExpressions());
  expect(next.result.current.error).toBe('Generate tracker state first.');
});


test('an obsolete refresh reports that its selections were not applied', async () => {
  vi.mocked(awaitRun).mockResolvedValue({ ...run, result: { selected: 2, applied: false, outcome: 'obsolete' } });
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.enabled).toBe(true));
  await act(async () => result.current.refreshExpressions());
  expect(result.current.notice).toMatch(/not applied/);
  expect(result.current.notice).not.toMatch(/2 selected/);
});

test('a scene refresh invalidates delayed reads even when the stable post is unchanged', async () => {
  let complete!: (value: VNStage) => void;
  vi.mocked(api.getVNPost).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  const { result, rerender } = renderHook(({ paused }) => useVNStage('c', 's', scene, 'a', paused), { initialProps: { paused: false } });
  await waitFor(() => expect(api.getVNPost).toHaveBeenCalled());
  rerender({ paused: true });
  await act(async () => complete({ ...stage, revision: 'obsolete' }));
  expect(result.current.stage).toBeNull();
  rerender({ paused: false });
  await waitFor(() => expect(result.current.stage?.revision).toBe('0'));
});


test('partial refresh reports actor failure details from the wire error map', async () => {
  vi.mocked(awaitRun).mockResolvedValue({ ...run, result: { selected: 1, skipped: 0, failed: 1, errors: { 'characters:mara': 'Decision connection unavailable' } } });
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.enabled).toBe(true));
  await act(async () => result.current.refreshExpressions());
  expect(result.current.notice).toMatch(/1 selected.*1 failed/);
  expect(result.current.notice).toContain('Decision connection unavailable');
});

test('a successful background stage read clears an earlier read failure', async () => {
  vi.mocked(api.getVNPost).mockRejectedValueOnce(new Error('Temporary stage failure'));
  const { result } = renderHook(() => useVNStage('c', 's', scene, 'a'));
  await waitFor(() => expect(result.current.error).toBe('Temporary stage failure'));
  vi.mocked(api.getTrackerRuns).mockResolvedValue({ runs: [run] });
  await waitFor(() => expect(result.current.stage).toEqual(stage), { timeout: 2500 });
  expect(result.current.error).toBe('');
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
type Tracker = Awaited<ReturnType<typeof api.getTracker>>;

describe('tracker discovery lifecycle', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  test('retries a failed capability read without reloading and stops after success while discovering runs', async () => {
    const initial = deferred<Tracker>();
    const retry = deferred<Tracker>();
    vi.mocked(api.getTracker).mockReturnValueOnce(initial.promise).mockReturnValueOnce(retry.promise);
    const { result, unmount } = renderHook(() => useVNStage('c', 's', scene, 'a'));
    await act(async () => result.current.refreshExpressions());
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    // Rejection and the next lifecycle tick must not suppress run discovery.
    await act(async () => initial.reject(new Error('Temporary tracker failure')));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(api.getTracker).toHaveBeenCalledTimes(2);
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(2);
    await act(async () => result.current.refreshExpressions());
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    await act(async () => retry.resolve({ enabled: true } as Tracker));
    expect(result.current.enabled).toBe(true);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    await act(async () => result.current.refreshExpressions());
    expect(api.refreshVNExpressions).toHaveBeenCalledExactlyOnceWith('c', 's', 'a');
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(api.getTracker).toHaveBeenCalledTimes(2);
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(5);
    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(5);
  });

  test('a successful disabled answer is authoritative and never retries or permits paid work', async () => {
    vi.mocked(api.getTracker).mockResolvedValue({ enabled: false } as Tracker);
    const { result, unmount } = renderHook(() => useVNStage('c', 's', scene, 'a'));
    await act(async () => {});
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await act(async () => result.current.refreshExpressions());
    expect(result.current.enabled).toBe(false);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    expect(api.getTracker).toHaveBeenCalledTimes(1);
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(4);
    unmount();
  });

  test('the first new-scene consumer and retained callback cannot spend old scene enablement', async () => {
    const pending = deferred<Tracker>();
    const nextScene = { ...scene, scene_identity: 'scene-two' };
    const { result, rerender, unmount } = renderHook(({ sid, consume }) => {
      const value = useVNStage('c', sid, sid === 's' ? scene : nextScene, sid === 's' ? 'a' : 'b');
      const consumed = useRef('');
      useLayoutEffect(() => {
        if (consume && consumed.current !== sid) {
          consumed.current = sid;
          void value.refreshExpressions();
        }
      }, [sid, consume, value]); // The first consumer runs before passive lifecycle resets.
      return value;
    }, { initialProps: { sid: 's', consume: false } });
    await act(async () => {});
    expect(result.current.enabled).toBe(true);
    const retiredRefresh = result.current.refreshExpressions;
    vi.mocked(api.getTracker).mockReturnValue(pending.promise);
    await act(async () => rerender({ sid: 'next', consume: true }));
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    await act(async () => retiredRefresh());
    expect(result.current.enabled).toBe(false);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    await act(async () => pending.resolve({ enabled: true } as Tracker));
    await act(async () => result.current.refreshExpressions());
    expect(api.refreshVNExpressions).toHaveBeenCalledExactlyOnceWith('c', 'next', 'b');
    await act(async () => retiredRefresh());
    expect(api.refreshVNExpressions).toHaveBeenCalledTimes(1);
    unmount();
  });

  test('a retained enabled action cannot target a new route reusing the same snapshot or an unmounted view', async () => {
    const pending = deferred<Tracker>();
    const { result, rerender, unmount } = renderHook(({ cid, sid }) => useVNStage(cid, sid, scene, 'a'), { initialProps: { cid: 'c', sid: 's' } });
    await act(async () => {});
    expect(result.current.enabled).toBe(true);
    const retiredRefresh = result.current.refreshExpressions;
    vi.mocked(api.getTracker).mockReturnValue(pending.promise);
    rerender({ cid: 'next-c', sid: 'next-s' });
    await act(async () => retiredRefresh());
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    await act(async () => pending.resolve({ enabled: true } as Tracker));
    const unmountedRefresh = result.current.refreshExpressions;
    unmount();
    await act(async () => unmountedRefresh());
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
  });

  test('stalled capability discovery leaves run polls live without duplicating its pending read', async () => {
    const pending = deferred<Tracker>();
    vi.mocked(api.getTracker).mockReturnValue(pending.promise);
    const { result, unmount } = renderHook(() => useVNStage('c', 's', scene, 'a'));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await act(async () => result.current.refreshExpressions());
    expect(api.getTracker).toHaveBeenCalledTimes(1);
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(4);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    unmount();
    await act(async () => pending.resolve({ enabled: true } as Tracker));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(4);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
  });

  test('retired and unmounted capability responses cannot authorize current actions', async () => {
    const retired = deferred<Tracker>();
    const current = deferred<Tracker>();
    vi.mocked(api.getTracker).mockReturnValueOnce(retired.promise).mockReturnValueOnce(current.promise);
    const { result, rerender, unmount } = renderHook(({ sid }) => useVNStage('c', sid, scene, 'a'), { initialProps: { sid: 's' } });
    const retiredRefresh = result.current.refreshExpressions;
    rerender({ sid: 'next' });
    await act(async () => retired.resolve({ enabled: true } as Tracker));
    await act(async () => result.current.refreshExpressions());
    await act(async () => retiredRefresh());
    expect(result.current.enabled).toBe(false);
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    const unmountedRefresh = result.current.refreshExpressions;
    unmount();
    await act(async () => current.resolve({ enabled: true } as Tracker));
    await act(async () => unmountedRefresh());
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(api.refreshVNExpressions).not.toHaveBeenCalled();
    expect(api.getTrackerRuns).toHaveBeenCalledTimes(2);
    expect(api.getTracker).toHaveBeenCalledTimes(2);
  });
});
