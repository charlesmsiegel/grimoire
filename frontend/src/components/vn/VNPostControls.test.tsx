import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { api, type Message } from '../../api/client';
import { VNPostControls } from './VNPostControls';
import type { useVNReply } from './useVNReply';

vi.mock('../../api/client', () => ({ api: {
  getScene: vi.fn(), listScenes: vi.fn(), getCampaignInference: vi.fn(), findRun: vi.fn(), getRollProposal: vi.fn(),
  editMessage: vi.fn(), retconMessage: vi.fn(), regenerate: vi.fn(), regenerateResponse: vi.fn(),
  refreshRollingSummary: vi.fn(), askSceneBreak: vi.fn(),
} }));
vi.mock('../RerollRoute', () => ({
  default: () => <p>Route picker</p>, NO_REROLL_ROUTE: { provider: '', model: '', preset: '' },
  canGenerateOnRoute: (ready: boolean) => ready, rerollOverrides: () => ({}),
}));
vi.mock('../PostImagePicker', () => ({ PostImagePicker: ({ onInsert, onClose }: { onInsert: (text: string) => void; onClose: () => void }) =>
  <div><button onClick={() => onInsert('![Forest](/forest.png)')}>Choose forest</button><button onClick={onClose}>Cancel image</button></div> }));
let messages: Message[];
const changed = vi.fn(async () => true);
const rerolled = vi.fn();
const run = vi.fn(async (start) => start(() => {}, new AbortController().signal, 'attempt'));
function mount() {
  return render(<MemoryRouter><VNPostControls cid="c" sid="s" postId="a" revision={{}} stage={null}
    reply={{ locked: false, proposal: false, run } as unknown as ReturnType<typeof useVNReply>}
    onChanged={changed} onReroll={rerolled} /></MemoryRouter>);
}
beforeEach(() => {
  vi.clearAllMocks();
  changed.mockResolvedValue(true);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  messages = [{ role: 'user', content: 'Hello', post_id: 'u' }, { role: 'assistant', content: 'Raw words', shown: 'Displayed words', post_id: 'a' }];
  vi.mocked(api.getScene).mockImplementation(async () => ({ meta: { id: 's', title: 'Saltmarch' }, messages: structuredClone(messages) }));
  vi.mocked(api.listScenes).mockResolvedValue([{ id: 's', title: 'Saltmarch', model: '', created: '', updated: '', date: '' }]);
  vi.mocked(api.getCampaignInference).mockResolvedValue({ routes: [{ key: 'scene', problem: null }] } as never);
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: null });
  vi.mocked(api.editMessage).mockResolvedValue({} as never);
  vi.mocked(api.retconMessage).mockResolvedValue({ refused: [], failed: [], later: [] } as never);
  vi.mocked(api.refreshRollingSummary).mockResolvedValue({} as never);
  vi.mocked(api.askSceneBreak).mockResolvedValue({} as never);
});

test.each(['Save', 'Retcon'])('%s refreshes the reader when optional boundary read rejects', async action => {
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  await screen.findByRole('textbox', { name: 'Post text' });
  const write = action === 'Save' ? api.editMessage : api.retconMessage;
  vi.mocked(write).mockImplementationOnce(async () => {
    vi.mocked(api.getScene).mockRejectedValue(new Error('Optional boundary unavailable'));
    return { refused: [], failed: [], later: [] } as never;
  });
  changed.mockResolvedValue(false);
  fireEvent.click(screen.getByRole('button', { name: action }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('status')).toHaveTextContent('Post saved.');
  expect(screen.getByRole('alert')).toHaveTextContent('Post saved, but the reader could not refresh. Use Refresh in the menu.');
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test.each(['Save', 'Retcon'])('%s refreshes without waiting for a pending optional boundary', async action => {
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  await screen.findByRole('textbox', { name: 'Post text' });
  const write = action === 'Save' ? api.editMessage : api.retconMessage;
  vi.mocked(write).mockImplementationOnce(async () => {
    vi.mocked(api.getScene).mockImplementation(() => new Promise(() => {}));
    return { refused: [], failed: [], later: [] } as never;
  });
  fireEvent.click(screen.getByRole('button', { name: action }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('status')).toHaveTextContent('Post saved.');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test('successful retcon preserves refused, failed reversal and later scene notices', async () => {
  vi.mocked(api.retconMessage).mockResolvedValue({ refused: [{ label: 'Mara' }], failed: ['Tracker reversal'], later: ['later'] } as never);
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  await screen.findByRole('textbox', { name: 'Post text' });
  fireEvent.click(screen.getByRole('button', { name: 'Retcon' }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('status')).toHaveTextContent('Post saved. Could not restore: Mara. Some reversals failed: Tracker reversal. Later scenes may need review in normal play.');
});

test('unmount prevents a detached boundary response from dispatching suggestions', async () => {
  let finishBoundary!: (page: Awaited<ReturnType<typeof api.getScene>>) => void;
  const { unmount } = mount();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  await screen.findByRole('textbox', { name: 'Post text' });
  vi.mocked(api.editMessage).mockImplementationOnce(async () => {
    vi.mocked(api.getScene).mockImplementation(() => new Promise(resolve => { finishBoundary = resolve; }));
    return {} as never;
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByRole('status');
  unmount();
  await act(async () => finishBoundary({ meta: { id: 's', title: 'Saltmarch' }, messages }));
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test.each(['Save', 'Retcon'])('%s write failure preserves the draft and reports the primary error', async action => {
  vi.mocked(action === 'Save' ? api.editMessage : api.retconMessage).mockRejectedValueOnce(new Error('Write refused'));
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const editor = await screen.findByRole('textbox', { name: 'Post text' });
  fireEvent.change(editor, { target: { value: 'My draft' } });
  fireEvent.click(screen.getByRole('button', { name: action }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Write refused');
  expect(editor).toHaveValue('My draft');
  expect(changed).not.toHaveBeenCalled();
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test('edit saves raw prose at its fresh stable index and requests bounded follow-up', async () => {
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const editor = await screen.findByRole('textbox', { name: 'Post text' });
  expect(editor).toHaveValue('Raw words');
  fireEvent.change(editor, { target: { value: 'Edited words' } });
  messages.unshift({ role: 'assistant', content: 'Earlier', post_id: 'earlier' });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith('c', 's', 2, 'Edited words'));
  expect(api.refreshRollingSummary).toHaveBeenCalledWith('c', 's', false, 3);
});

test('changed or removed post refuses save and preserves the draft', async () => {
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const editor = await screen.findByRole('textbox', { name: 'Post text' });
  fireEvent.change(editor, { target: { value: 'My draft' } });
  messages[1].content = 'Changed elsewhere';
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByRole('alert');
  expect(api.editMessage).not.toHaveBeenCalled();
  expect(editor).toHaveValue('My draft');
});

test('image insertion opens a raw editor for review and never writes immediately', async () => {
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Insert image' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Insert image' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Choose forest' }));
  expect(await screen.findByRole('textbox', { name: 'Post text' })).toHaveValue('Raw words\n\n![Forest](/forest.png)');
  expect(api.editMessage).not.toHaveBeenCalled();
});

test('reroll asks for guidance, then targets the displayed eligible response', async () => {
  messages[1].response_id = 'response'; messages[1].response_can_reroll = true;
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Reroll' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Reroll' }));
  const guidance = await screen.findByRole('textbox', { name: 'Reroll guidance' });
  expect(run).not.toHaveBeenCalled();
  fireEvent.change(guidance, { target: { value: 'A quieter response' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reroll with guidance' }));
  await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledWith('c', 's', 'response', expect.any(Function),
    { guidance: 'A quieter response' }, expect.any(AbortSignal), 'attempt'));
  expect(api.regenerate).not.toHaveBeenCalled();
});

test('older legacy posts cannot reroll the trailing response', async () => {
  messages.push({ role: 'assistant', content: 'Later response', post_id: 'later' });
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled());
  expect(screen.getByRole('button', { name: 'Reroll' })).toBeDisabled();
});

test('closed scenes disable post writes', async () => {
  vi.mocked(api.listScenes).mockResolvedValue([{ id: 's', closed_by: { sid: 'other', title: 'Other' } }] as never);
  mount(); await waitFor(() => expect(api.getScene).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Reroll' })).toBeDisabled();
});
