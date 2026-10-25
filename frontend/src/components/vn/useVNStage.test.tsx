import { act, renderHook, waitFor } from '@testing-library/react';
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
