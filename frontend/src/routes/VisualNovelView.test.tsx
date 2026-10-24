import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api, type VNStage } from '../api/client';
import VisualNovelView from './VisualNovelView';
import { awaitRun } from '../api/runs';
import { RunRegistryProvider } from '../runs/RunRegistryProvider';
import { useVNReply } from '../components/vn/useVNReply';

test('reopening during a reroll resumes the first replacement passage, not the scene beginning', async () => {
  const first = renderHook(() => useVNReply('c', 's', async () => {}), { wrapper: RunRegistryProvider });
  await waitFor(() => expect(first.result.current.locked).toBe(false));
  let running!: Promise<void>;
  act(() => { running = first.result.current.run(async (_emit, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Closed', 'AbortError')));
  }), { sceneIdentity: 'scene-one', postId: 'b', index: 1 }); });
  localStorage.setItem('vn-cursor:scene-one', JSON.stringify({ postId: 'b', step: 2 }));
  first.unmount(); await running;
  vi.mocked(api.findRun).mockResolvedValue({ run: { id: 'reroll', state: 'landed' } } as never);
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [scene.posts[0], {
    ...scene.posts[1], post_id: 'replacement', content: 'Fresh first sentence. Fresh middle sentence. Fresh final sentence.',
  }] });
  mount();
  await screen.findByText('Fresh first sentence.');
  expect(screen.queryByText('First words')).not.toBeInTheDocument();
  expect(screen.queryByText('Fresh final sentence.')).not.toBeInTheDocument();
});

test('Enter advances but preserves native controls, repeats, and composition', async () => {
  mount(); await screen.findByText('First words');
  const next = screen.getByRole('button', { name: 'Next' });
  next.focus();
  expect(fireEvent.keyDown(next, { key: 'Enter' })).toBe(true);
  expect(screen.getByText('First words')).toBeVisible();
  next.blur();
  fireEvent.keyDown(window, { key: 'Enter', repeat: true });
  fireEvent.keyDown(window, { key: 'Enter', isComposing: true });
  expect(screen.getByText('First words')).toBeVisible();
  fireEvent.keyDown(window, { key: 'Enter' });
  expect(screen.getByText('Last words')).toBeVisible();
});

test('reply Enter sends once, Shift remains multiline, and blank/IME/repeated Enter never sends', async () => {
  mount(); await screen.findByText('First words');
  fireEvent.keyDown(window, { key: 'End' });
  const input = screen.getByRole('textbox', { name: 'Your reply' });
  fireEvent.keyDown(input, { key: 'Enter' });
  fireEvent.change(input, { target: { value: 'Keep going' } });
  expect(fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })).toBe(true);
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  fireEvent.keyDown(input, { key: 'Enter', repeat: true });
  expect(api.chat).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(1));
});

test('wheel navigates in both directions but leaves zoom, controls, and scrollable prose alone', async () => {
  mount(); await screen.findByText('First words');
  const stage = screen.getByRole('region', { name: 'Stage: unknown location' });
  fireEvent.wheel(stage, { deltaY: 100, ctrlKey: true });
  fireEvent.wheel(stage, { deltaY: 100, deltaX: 200 });
  expect(screen.getByText('First words')).toBeVisible();
  fireEvent.wheel(stage, { deltaY: 100 });
  expect(screen.getByText('Last words')).toBeVisible();
  fireEvent.wheel(screen.getByRole('textbox', { name: 'Your reply' }), { deltaY: -100 });
  expect(screen.getByText('Last words')).toBeVisible();
  await new Promise(resolve => setTimeout(resolve, 250));
  fireEvent.wheel(stage, { deltaY: -3, deltaMode: 1 });
  expect(screen.getByText('First words')).toBeVisible();
  const prose = screen.getByText('First words').parentElement!;
  prose.style.overflowY = 'auto';
  Object.defineProperty(prose, 'scrollHeight', { value: 400 });
  Object.defineProperty(prose, 'clientHeight', { value: 100 });
  await new Promise(resolve => setTimeout(resolve, 250));
  expect(fireEvent.wheel(prose, { deltaY: 100 })).toBe(true);
  expect(screen.getByText('First words')).toBeVisible();
});

test('H and stage clicks restore without advancing; Escape menu blocks background navigation', async () => {
  mount(); await screen.findByText('First words');
  const stage = screen.getByRole('region', { name: 'Stage: unknown location' });
  fireEvent.keyDown(window, { key: 'h' });
  expect(screen.queryByRole('article', { name: 'Current post' })).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: 'Enter' });
  fireEvent.click(stage);
  expect(screen.getByText('First words')).toBeVisible();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.getByRole('dialog', { name: 'VN menu' })).toBeVisible();
  fireEvent.wheel(stage, { deltaY: 100 });
  fireEvent.click(stage);
  expect(screen.getByText('First words')).toBeVisible();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(stage);
  expect(screen.getByText('Last words')).toBeVisible();
});

test('clicks through a single post and restores the within-post bookmark', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'First. Second. Third.\n\nTogether. Still together.' }] });
  const view = mount();
  await screen.findByText('First.');
  expect(screen.queryByText('Second.')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByText('Second.')).toBeInTheDocument();
  view.unmount(); mount();
  await screen.findByText('Second.');
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.getByText('Third.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('article', { name: 'Current post' }));
  expect(screen.getByText('Together. Still together.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  menuAction('Previous');
  expect(screen.getByText('Third.')).toBeInTheDocument();
});

test('image splashes expire after the paragraph and restore on Previous', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: '![Forest](/forest.png)\n\nFirst. Second. Third.\n\nHome.' }] });
  vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, background_url: '/base.png' });
  mount();
  expect(await screen.findByRole('img', { name: 'Forest' })).toHaveAttribute('src', '/forest.png');
  for (let i = 0; i < 4; i++) fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByText('Home.')).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Forest' })).not.toBeInTheDocument();
  menuAction('Previous');
  expect(screen.getByRole('img', { name: 'Forest' })).toBeInTheDocument();
  fireEvent.error(screen.getByRole('img', { name: 'Forest' }));
  expect(document.querySelector('img[src="/base.png"]')).toBeVisible();
});

test('refresh clamps a saved step when the post becomes shorter', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'One. Two. Three.' }] });
  mount(); await screen.findByText('One.');
  menuAction('Last');
  expect(screen.getByText('Three.')).toBeInTheDocument();
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'Edited.' }] });
  menuAction('Refresh');
  expect(await screen.findByText('Edited.')).toBeInTheDocument();
});

test.each([true, false])('a bookmark resolves a trailing image against both post backgrounds (same=%s)', async same => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [
    { ...scene.posts[0], content: '![Forest](/forest.png)' },
    { ...scene.posts[1], content: 'Next paragraph.\n\nLater paragraph.' },
  ] });
  localStorage.setItem('vn-cursor:scene-one', 'b');
  let resolveSource!: (s: VNStage) => void;
  vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => id === 'a'
    ? new Promise(resolve => { resolveSource = resolve; })
    : { ...stage, post_id: id, background_url: '/base.png' });
  mount(); await screen.findByText('Next paragraph.');
  expect(screen.queryByRole('img', { name: 'Forest' })).not.toBeInTheDocument();
  await act(async () => resolveSource({ ...stage, background_url: same ? '/base.png' : '/different.png' }));
  if (same) expect(screen.getByRole('img', { name: 'Forest' })).toBeVisible();
  else expect(screen.queryByRole('img', { name: 'Forest' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByText('Later paragraph.')).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Forest' })).not.toBeInTheDocument();
});

test('links, selected text, and settings do not advance dialogue', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'Read [the map](#map). Wait. Leave.' }] });
  mount(); await screen.findByRole('link', { name: 'the map' });
  fireEvent.click(screen.getByRole('link', { name: 'the map' }));
  expect(screen.queryByText('Wait.')).not.toBeInTheDocument();
  const selection = window.getSelection()!; selection.removeAllRanges();
  const range = document.createRange(); range.selectNodeContents(screen.getByRole('article', { name: 'Current post' })); selection.addRange(range);
  fireEvent.click(screen.getByRole('article', { name: 'Current post' }));
  expect(screen.queryByText('Wait.')).not.toBeInTheDocument();
  selection.removeAllRanges();
  menuAction('Presentation settings');
  fireEvent.keyDown(window, { key: 'ArrowRight' });
  expect(screen.queryByText('Wait.')).not.toBeInTheDocument();
});

test('autoplay advances sentences within the final post and manual navigation stops it', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'One. Two. Three.' }] });
  mount(); await screen.findByText('One.');
  vi.useFakeTimers();
  try {
    fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText('Two.')).toBeInTheDocument();
    menuAction('Previous');
    expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false');
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.getByText('One.')).toBeInTheDocument();
  } finally { vi.useRealTimers(); }
});
vi.mock('../api/client', () => ({ api: { getVNScene: vi.fn(), getVNPost: vi.fn(), saveVNPresentation: vi.fn(), chat: vi.fn(), findRun: vi.fn(), attemptState: vi.fn(), cancelAttempt: vi.fn(), cancelRun: vi.fn(), getRollProposal: vi.fn(), getScene: vi.fn(), listScenes: vi.fn() } }));
vi.mock('../api/runs', () => ({ awaitRun: vi.fn() }));
const scene = { scene_identity: 'scene-one', title: 'Saltmarch', posts: [ { post_id: 'a', content: '**First words**', role: 'assistant', speaker: 'Mara', excluded: false }, { post_id: 'b', content: 'Last words', role: 'assistant', speaker: null, excluded: false } ] };
const stage: VNStage = { scene_identity: 'scene-one', post_id: 'a', revision: '0', actors: [], backgrounds: [], background_url: null, location_name: null, notices: [], override: { actors: {}, background: null } };
function menuAction(name: string) { fireEvent.click(screen.getByRole('button', { name: 'Menu' })); fireEvent.click(screen.getByRole('button', { name })); }
function mount() { return render(<RunRegistryProvider><MemoryRouter initialEntries={['/campaigns/c/scenes/s/vn']}><Routes><Route path="/campaigns/:cid/scenes/:sid/vn" element={<VisualNovelView />} /></Routes></MemoryRouter></RunRegistryProvider>); }
beforeEach(() => { localStorage.clear(); vi.mocked(api.getVNScene).mockResolvedValue(structuredClone(scene)); vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id })); });
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: 's', title: 'Saltmarch' }, messages: scene.posts.map(p => ({ post_id: p.post_id, content: p.content, role: 'assistant' as const, ...(p.speaker ? { speaker: p.speaker } : {}) })) });
  vi.mocked(api.listScenes).mockResolvedValue([{ id: 's', title: 'Saltmarch', model: '', created: '', updated: '', date: '' }]);
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: true, run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: null });
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _text, emit) => { emit({ done: true }); });
});

const cast: VNStage['actors'] = [
  { ref: 'characters:mara', name: 'Mara', version_id: 'v1', speaking: true, automatic_outfit: null, outfits: [], candidates: [{ url: '/mara.png', presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: 'bottom-center' } }] },
  { ref: 'pcs:winifred', name: 'Winifred', version_id: 'v1', speaking: false, automatic_outfit: null, outfits: [], candidates: [{ url: '/winifred.png', presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: 'bottom-center' } }] },
];

test('illustrations have accessible descriptions without dialogue captions and hide all sprites until expiry or failure', async () => {
  localStorage.setItem('vn-show-pc-sprites', 'true');
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: '![Forest](/forest.png)\n\nLook around.\n\nTime to go.' }] });
  vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: cast });
  mount();
  await screen.findByRole('img', { name: 'Forest' });
  expect(within(screen.getByRole('article', { name: 'Current post' })).queryByText('Forest')).not.toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Mara' })).not.toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Winifred' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByText('Look around.')).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Mara' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByRole('img', { name: 'Mara' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
  menuAction('Previous');
  fireEvent.error(screen.getByRole('img', { name: 'Forest' }));
  expect(screen.getByRole('img', { name: 'Mara' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
});

test('PC sprites default off and the reader toggle persists through posts and reload without changing post overrides', async () => {
  vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id, actors: cast }));
  const view = mount(); await screen.findByText('First words');
  expect(screen.getByRole('img', { name: 'Mara' })).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Winifred' })).not.toBeInTheDocument();
  menuAction('Presentation settings');
  expect(screen.getByRole('checkbox', { name: 'Show PC sprites' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Show PC sprites' }));
  expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
  vi.mocked(api.saveVNPresentation).mockResolvedValue({ ...stage, actors: cast });
  fireEvent.click(screen.getByRole('button', { name: 'Reset to automatic' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Reset to automatic' })).toBeEnabled());
  expect(screen.getByRole('checkbox', { name: 'Show PC sprites' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Close settings' }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Last words');
  expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
  view.unmount(); mount(); await screen.findByText('Last words');
  expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
  menuAction('Presentation settings');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Show PC sprites' }));
  expect(screen.queryByRole('img', { name: 'Winifred' })).not.toBeInTheDocument();
});

test('enabling PC sprites respects a post-specific hidden actor', async () => {
  vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: cast.map(actor => ({ ...actor, hidden: actor.ref.startsWith('pcs:') })) });
  mount(); await screen.findByText('First words');
  menuAction('Presentation settings');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Show PC sprites' }));
  expect(screen.queryByRole('img', { name: 'Winifred' })).not.toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Mara' })).toBeInTheDocument();
});

test('PC visibility remains usable when browser storage is unavailable', async () => {
  const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Unavailable'); });
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Unavailable'); });
  try {
    vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: cast });
    mount(); await screen.findByText('First words');
    expect(screen.queryByRole('img', { name: 'Winifred' })).not.toBeInTheDocument();
    menuAction('Presentation settings');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show PC sprites' }));
    expect(screen.getByRole('img', { name: 'Winifred' })).toBeInTheDocument();
  } finally { read.mockRestore(); write.mockRestore(); }
});
test('reads markdown, navigates stable posts and returns to exact play URL', async () => { mount(); expect(await screen.findByText('First words')).toBeInTheDocument(); expect(screen.getByRole('link', { name: 'Exit VN' })).toHaveAttribute('href', '/campaigns/c/scenes/s'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); expect(await screen.findByText('Last words')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled(); fireEvent.keyDown(window, { key: 'ArrowLeft' }); expect(await screen.findByText('First words')).toBeInTheDocument(); });
test('refresh preserves cursor and visibly recovers deleted posts', async () => { mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByText('Last words'); vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [scene.posts[0]] }); menuAction('Refresh'); expect(await screen.findByText(/no longer available/i)).toBeInTheDocument(); expect(screen.getByText('First words')).toBeInTheDocument(); });
test('retains a settings draft after save failure and resets automatic presentation', async () => { vi.mocked(api.saveVNPresentation).mockRejectedValueOnce(new Error('Stale presentation')); mount(); await screen.findByText('First words'); menuAction('Presentation settings'); fireEvent.click(screen.getByRole('button', { name: 'Save presentation' })); expect(await screen.findByText('Stale presentation')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Save presentation' })).toBeEnabled(); vi.mocked(api.saveVNPresentation).mockResolvedValue(stage); fireEvent.click(screen.getByRole('button', { name: 'Reset to automatic' })); await waitFor(() => expect(api.saveVNPresentation).toHaveBeenLastCalledWith('c', 's', 'a', { scene_identity: 'scene-one', expected_revision: '0', actors: {}, background: null })); });
test('restores a stable cursor after reload and tolerates unavailable storage', async () => { localStorage.setItem('vn-cursor:scene-one', 'b'); const view = mount(); expect(await screen.findByText('Last words')).toBeInTheDocument(); view.unmount(); const storage = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Unavailable'); }); mount(); expect(await screen.findByText('First words')).toBeInTheDocument(); storage.mockRestore(); });
test('a delayed previous post stage never replaces the selected post', async () => { let resolve!: (s: typeof stage) => void; vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => id === 'a' ? new Promise(r => { resolve = r; }) : { ...stage, post_id: id, location_name: 'Saltmarch' }); mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByRole('region', { name: 'Stage: Saltmarch' }); resolve({ ...stage, location_name: 'Old location' }); await waitFor(() => expect(screen.queryByRole('region', { name: 'Stage: Old location' })).not.toBeInTheDocument()); });
test('autoplay stops at the last post and opening settings stops the timer', async () => { mount(); await screen.findByText('First words'); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' })); menuAction('Presentation settings'); expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false'); fireEvent.click(screen.getByRole('button', { name: 'Close settings' })); fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' })); await import('@testing-library/react').then(({ act }) => act(async () => { await vi.advanceTimersByTimeAsync(5000); })); expect(screen.getByText('Last words')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false'); vi.useRealTimers(); });
test('failed actor images fall through every candidate to readable initials', async () => { vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: [{ ref: 'mara', version_id: 'v1', name: 'Mara', speaking: true, automatic_outfit: null, outfits: [], candidates: ['sprite.png', 'avatar.png'].map(url => ({ url, presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: 'bottom-center' } })) }] }); mount(); const image = await screen.findByRole('img', { name: 'Mara' }); fireEvent.error(image); expect(screen.getByRole('img', { name: 'Mara' })).toHaveAttribute('src', 'avatar.png'); fireEvent.error(screen.getByRole('img', { name: 'Mara' })); expect(screen.getByLabelText('Mara portrait unavailable')).toHaveTextContent('M'); });
test('a save completed after closing settings cannot replace a newer post stage', async () => { let resolve!: (s: typeof stage) => void; vi.mocked(api.saveVNPresentation).mockImplementationOnce(() => new Promise(r => { resolve = r; })); vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id, location_name: id === 'b' ? 'Saltmarch' : null })); mount(); await screen.findByText('First words'); menuAction('Presentation settings'); fireEvent.click(screen.getByRole('button', { name: 'Save presentation' })); fireEvent.click(screen.getByRole('button', { name: 'Close settings' })); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByRole('region', { name: 'Stage: Saltmarch' }); const { act } = await import('@testing-library/react'); await act(async () => resolve({ ...stage, location_name: 'Old location' })); expect(screen.getByRole('region', { name: 'Stage: Saltmarch' })).toBeInTheDocument(); });
test('automatic expressions belong only to the historical automatic outfit', async () => { vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: [{ ref: 'mara', version_id: 'v1', name: 'Mara', speaking: true, automatic_outfit: 'default', candidates: [], outfits: [{ id: 'default', label: 'Default', expressions: ['neutral'] }, { id: 'armor', label: 'Armor', expressions: ['joy'] }] }] }); mount(); await screen.findByText('First words'); menuAction('Presentation settings'); const expression = screen.getByRole('combobox', { name: 'Expression' }); expect(expression).toHaveTextContent('neutral'); expect(expression).not.toHaveTextContent('joy'); fireEvent.change(screen.getByRole('combobox', { name: 'Outfit' }), { target: { value: 'armor' } }); expect(expression).toHaveTextContent('joy'); expect(expression).not.toHaveTextContent('neutral'); });
test('a different background loads visibly after the previous image failed', async () => { vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id, background_url: id === 'a' ? '/old-background.png' : '/new-background.png' })); mount(); await screen.findByText('First words'); const oldImage = document.querySelector<HTMLImageElement>('img[src="/old-background.png"]')!; fireEvent.error(oldImage); expect(oldImage).not.toBeVisible(); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByText('Last words'); const newImage = document.querySelector<HTMLImageElement>('img[src="/new-background.png"]')!; expect(newImage).toBeVisible(); });

test('the final passage offers a reply, preserves its draft through history and leaves typing keys alone', async () => {
  mount(); await screen.findByText('First words');
  expect(screen.queryByRole('textbox', { name: 'Your reply' })).not.toBeInTheDocument();
  menuAction('Last');
  const input = await screen.findByRole('textbox', { name: 'Your reply' });
  fireEvent.change(input, { target: { value: 'I look around.\nThen wait.' } });
  input.focus(); fireEvent.keyDown(input, { key: 'Home' });
  expect(screen.getByText('Last words')).toBeInTheDocument();
  menuAction('Previous');
  expect(screen.queryByRole('textbox', { name: 'Your reply' })).not.toBeInTheDocument();
  menuAction('Last');
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('I look around.\nThen wait.');
});

test('sending from a single greeting stays in VN and starts the new NPC reply at its first sentence', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [scene.posts[0]] });
  let finish!: () => void;
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _text, emit) => {
    await new Promise<void>(resolve => { finish = resolve; });
    vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [scene.posts[0],
      { ...scene.posts[0], post_id: 'player', role: 'user', content: 'Hello.' },
      { ...scene.posts[0], post_id: 'reply', content: 'One. Two. Three.' }] });
    emit({ done: true });
  });
  mount();
  const input = await screen.findByRole('textbox', { name: 'Your reply' });
  fireEvent.change(input, { target: { value: 'Hello.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  expect(screen.getByRole('button', { name: 'Send reply' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  expect(api.chat).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.chat).mock.calls[0].slice(0, 3)).toEqual(['c', 's', 'Hello.']);
  expect(vi.mocked(api.chat).mock.calls[0][6]).toEqual(expect.any(String));
  await act(async () => finish());
  expect(await screen.findByText('One.')).toBeInTheDocument();
  expect(screen.queryByText('Three.')).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Your reply' })).not.toBeInTheDocument();
  menuAction('Last');
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('');
});

test.each([false, true])('a failed reply restores only an undurable prompt (retained=%s)', async retained => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _text, emit) => { emit({ error: { detail: 'Provider unavailable', kind: 'provider', post_returned: !retained } }); });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained, run: null });
  mount();
  const input = await screen.findByRole('textbox', { name: 'Your reply' });
  fireEvent.change(input, { target: { value: 'My words.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  await screen.findByText('Provider unavailable');
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Your reply' })).toBeEnabled());
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue(retained ? '' : 'My words.');
});

test('an uncertain send stays locked until Check reply can fence and reconcile the attempt', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  mount();
  const input = await screen.findByRole('textbox', { name: 'Your reply' });
  vi.mocked(api.chat).mockRejectedValue(new Error('Connection lost'));
  vi.mocked(api.findRun).mockRejectedValue(new Error('Still offline'));
  fireEvent.change(input, { target: { value: 'Do not lose this.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  const check = await screen.findByRole('button', { name: 'Check reply' });
  expect(screen.getByRole('button', { name: 'Send reply' })).toBeDisabled();
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: false, run: null });
  fireEvent.click(check);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send reply' })).toBeEnabled());
  expect(api.cancelAttempt).toHaveBeenCalledWith('c', 's', expect.any(String));
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('Do not lose this.');
});

test('Stop cancels server work and restores an unretained prompt even if the stream never closes', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  vi.mocked(api.chat).mockImplementation(() => new Promise(() => {}));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: false, run: null });
  mount();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Your reply' }), { target: { value: 'Wait.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Stop reply' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send reply' })).toBeEnabled());
  expect(api.cancelAttempt).toHaveBeenCalledWith('c', 's', expect.any(String));
  expect(vi.mocked(api.chat).mock.calls[0][5]?.aborted).toBe(true);
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('Wait.');
});

test('remount recovers pending text and a pending roll offers normal play instead of another send', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  vi.mocked(api.chat).mockImplementation(() => new Promise(() => {}));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: 'attempt', retained: false, run: null });
  const view = mount();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Your reply' }), { target: { value: 'Remember me.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  view.unmount(); mount();
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('Remember me.'));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _text, emit) => { emit({ done: true }); });
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: { id: 'roll', status: 'proposed', payload: { id: 'roll', problems: [] }, resolution: null } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  expect(await screen.findByRole('link', { name: 'Resolve roll in play' })).toHaveAttribute('href', '/campaigns/c/scenes/s');
  expect(screen.getByRole('button', { name: 'Send reply' })).toBeDisabled();
});

test('an existing server turn blocks sending until it settles', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  const run = { id: 'run', attempt_id: 'other', state: 'running' as const, next_index: 0 };
  vi.mocked(api.findRun).mockResolvedValue({ run });
  let finish!: () => void;
  vi.mocked(awaitRun).mockImplementation(async () => {
    await new Promise<void>(resolve => { finish = resolve; });
    return { ...run, state: 'landed' };
  });
  mount();
  expect(await screen.findByRole('textbox', { name: 'Your reply' })).toBeDisabled();
  await act(async () => finish());
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Your reply' })).toBeEnabled());
});

test('Check reply retries a failed transcript refresh without resending a completed turn', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  mount();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Your reply' }), { target: { value: 'Hello.' } });
  vi.mocked(api.getVNScene).mockRejectedValueOnce(new Error('Read interrupted'));
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  const check = await screen.findByRole('button', { name: 'Check reply' });
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'One. Two. Three.' }] });
  fireEvent.click(check);
  expect(await screen.findByText('One.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Check reply' })).not.toBeInTheDocument();
  expect(api.chat).toHaveBeenCalledTimes(1);
});

test('a cleanly cut stream waits for its detached turn instead of unlocking Send', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  mount();
  const input = await screen.findByRole('textbox', { name: 'Your reply' });
  const run = { id: 'run', attempt_id: 'attempt', state: 'running' as const, next_index: 0 };
  vi.mocked(api.chat).mockResolvedValue(undefined);
  vi.mocked(api.findRun).mockResolvedValue({ run });
  let finish!: () => void;
  vi.mocked(awaitRun).mockImplementation(async () => {
    await new Promise<void>(resolve => { finish = resolve; });
    vi.mocked(api.findRun).mockResolvedValue({ run: { ...run, state: 'landed' } });
    return { ...run, state: 'landed' };
  });
  fireEvent.change(input, { target: { value: 'Wait for it.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  await waitFor(() => expect(awaitRun).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: 'Send reply' })).toBeDisabled();
  await act(async () => finish());
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Your reply' })).toBeEnabled());
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toHaveValue('');
});

test('recovering an older attempt still waits for a newer scene turn from another client', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [] });
  vi.mocked(api.chat).mockImplementation(() => new Promise(() => {}));
  const view = mount();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Your reply' }), { target: { value: 'Earlier turn.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reply' }));
  view.unmount();
  const older = { id: 'older', attempt_id: 'old', state: 'landed' as const, next_index: 1 };
  const newer = { id: 'newer', attempt_id: 'new', state: 'running' as const, next_index: 0 };
  vi.mocked(api.findRun).mockImplementation(async (_c, _s, attempt) => ({ run: attempt ? older : newer }));
  let finish!: () => void;
  vi.mocked(awaitRun).mockImplementation(async () => {
    await new Promise<void>(resolve => { finish = resolve; });
    return { ...newer, state: 'landed' };
  });
  mount();
  await waitFor(() => expect(awaitRun).toHaveBeenCalledWith('c', 's', newer, expect.any(AbortSignal)));
  expect(screen.getByRole('textbox', { name: 'Your reply' })).toBeDisabled();
  await act(async () => finish());
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Your reply' })).toBeEnabled());
});
