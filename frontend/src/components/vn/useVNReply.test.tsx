import { act, renderHook, waitFor } from '@testing-library/react';
import { api } from '../../api/client';
import { RunRegistryProvider } from '../../runs/RunRegistryProvider';
import { useVNReply } from './useVNReply';
import { awaitRun } from '../../api/runs';

vi.mock('../../api/client', () => ({ api: { findRun: vi.fn(), getRollProposal: vi.fn(), attemptState: vi.fn(), cancelAttempt: vi.fn() } }));
vi.mock('../../api/runs', () => ({ awaitRun: vi.fn() }));
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
});

test.each([true, false])('reroll reconciliation retains the unsent reply draft (landed=%s)', async landed => {
  const settled = vi.fn(async () => {});
  const { result } = renderHook(() => useVNReply('c', 's', settled), { wrapper: RunRegistryProvider });
  await waitFor(() => expect(result.current.locked).toBe(false));
  act(() => result.current.setText('An unsent reply'));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: landed, run: null });
  await act(async () => { await result.current.run(async emit => { emit(landed ? { done: true } : { error: { detail: 'Model failed', kind: 'provider' } }); }); });
  expect(result.current.text).toBe('An unsent reply');
  expect(result.current.locked).toBe(false);
  expect(settled).toHaveBeenCalledOnce();
});

test.each([true, false])('a reroll preserves its unsent draft across provider remount (landed=%s)', async landed => {
  const settled = async () => {};
  const first = renderHook(() => useVNReply('c', 's', settled), { wrapper: RunRegistryProvider });
  await waitFor(() => expect(first.result.current.locked).toBe(false));
  act(() => first.result.current.setText('Words worth keeping'));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: landed, run: null });
  await act(async () => { await first.result.current.run(async emit => emit(landed ? { done: true } : { error: { detail: 'Failed', kind: 'provider' } })); });
  first.unmount();
  const second = renderHook(() => useVNReply('c', 's', settled), { wrapper: RunRegistryProvider });
  await waitFor(() => expect(second.result.current.locked).toBe(false));
  expect(second.result.current.text).toBe('Words worth keeping');
});

test('a detached reroll keeps generation locked until its discovered run settles', async () => {
  const settled = vi.fn(async () => {});
  const { result } = renderHook(() => useVNReply('c', 's', settled), { wrapper: RunRegistryProvider });
  await waitFor(() => expect(result.current.locked).toBe(false));
  const running = { id: 'run', state: 'running' } as const;
  vi.mocked(api.findRun).mockResolvedValueOnce({ run: running } as never);
  let finish!: (value: never) => void;
  vi.mocked(awaitRun).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: false, run: null });
  let work!: Promise<void>;
  act(() => { work = result.current.run(async () => { throw new Error('Connection lost'); }); });
  await waitFor(() => expect(result.current.phase).toBe('waiting'));
  const duplicate = vi.fn();
  await act(async () => { await result.current.run(duplicate); });
  expect(duplicate).not.toHaveBeenCalled();
  await act(async () => { finish({ ...running, state: 'landed' } as never); await work; });
  expect(result.current.locked).toBe(false);
  expect(settled).toHaveBeenCalledOnce();
});
