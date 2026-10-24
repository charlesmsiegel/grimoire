import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api, type VNStage } from '../api/client';
import VisualNovelView from './VisualNovelView';

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
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
  expect(screen.getByRole('img', { name: 'Forest' })).toBeInTheDocument();
  fireEvent.error(screen.getByRole('img', { name: 'Forest' }));
  expect(document.querySelector('img[src="/base.png"]')).toBeVisible();
});

test('refresh clamps a saved step when the post becomes shorter', async () => {
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'One. Two. Three.' }] });
  mount(); await screen.findByText('One.');
  fireEvent.click(screen.getByRole('button', { name: 'Last' }));
  expect(screen.getByText('Three.')).toBeInTheDocument();
  vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [{ ...scene.posts[0], content: 'Edited.' }] });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
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
  const selection = window.getSelection()!;
  const range = document.createRange(); range.selectNodeContents(screen.getByRole('article', { name: 'Current post' })); selection.addRange(range);
  fireEvent.click(screen.getByRole('article', { name: 'Current post' }));
  expect(screen.queryByText('Wait.')).not.toBeInTheDocument();
  selection.removeAllRanges();
  fireEvent.click(screen.getByRole('button', { name: 'Presentation settings' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false');
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.getByText('One.')).toBeInTheDocument();
  } finally { vi.useRealTimers(); }
});
vi.mock('../api/client', () => ({ api: { getVNScene: vi.fn(), getVNPost: vi.fn(), saveVNPresentation: vi.fn() } }));
const scene = { scene_identity: 'scene-one', title: 'Saltmarch', posts: [ { post_id: 'a', content: '**First words**', role: 'assistant', speaker: 'Mara', excluded: false }, { post_id: 'b', content: 'Last words', role: 'assistant', speaker: null, excluded: false } ] };
const stage: VNStage = { scene_identity: 'scene-one', post_id: 'a', revision: '0', actors: [], backgrounds: [], background_url: null, location_name: null, notices: [], override: { actors: {}, background: null } };
function mount() { return render(<MemoryRouter initialEntries={['/campaigns/c/scenes/s/vn']}><Routes><Route path="/campaigns/:cid/scenes/:sid/vn" element={<VisualNovelView />} /></Routes></MemoryRouter>); }
beforeEach(() => { localStorage.clear(); vi.mocked(api.getVNScene).mockResolvedValue(structuredClone(scene)); vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id })); });
test('reads markdown, navigates stable posts and returns to exact play URL', async () => { mount(); expect(await screen.findByText('First words')).toBeInTheDocument(); expect(screen.getByRole('link', { name: 'Exit VN' })).toHaveAttribute('href', '/campaigns/c/scenes/s'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); expect(await screen.findByText('Last words')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled(); fireEvent.keyDown(window, { key: 'ArrowLeft' }); expect(await screen.findByText('First words')).toBeInTheDocument(); });
test('refresh preserves cursor and visibly recovers deleted posts', async () => { mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByText('Last words'); vi.mocked(api.getVNScene).mockResolvedValue({ ...scene, posts: [scene.posts[0]] }); fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); expect(await screen.findByText(/no longer available/i)).toBeInTheDocument(); expect(screen.getByText('First words')).toBeInTheDocument(); });
test('retains a settings draft after save failure and resets automatic presentation', async () => { vi.mocked(api.saveVNPresentation).mockRejectedValueOnce(new Error('Stale presentation')); mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Presentation settings' })); fireEvent.click(screen.getByRole('button', { name: 'Save presentation' })); expect(await screen.findByText('Stale presentation')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Save presentation' })).toBeEnabled(); vi.mocked(api.saveVNPresentation).mockResolvedValue(stage); fireEvent.click(screen.getByRole('button', { name: 'Reset to automatic' })); await waitFor(() => expect(api.saveVNPresentation).toHaveBeenLastCalledWith('c', 's', 'a', { scene_identity: 'scene-one', expected_revision: '0', actors: {}, background: null })); });
test('restores a stable cursor after reload and tolerates unavailable storage', async () => { localStorage.setItem('vn-cursor:scene-one', 'b'); const view = mount(); expect(await screen.findByText('Last words')).toBeInTheDocument(); view.unmount(); const storage = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Unavailable'); }); mount(); expect(await screen.findByText('First words')).toBeInTheDocument(); storage.mockRestore(); });
test('a delayed previous post stage never replaces the selected post', async () => { let resolve!: (s: typeof stage) => void; vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => id === 'a' ? new Promise(r => { resolve = r; }) : { ...stage, post_id: id, location_name: 'Saltmarch' }); mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByRole('region', { name: 'Stage: Saltmarch' }); resolve({ ...stage, location_name: 'Old location' }); await waitFor(() => expect(screen.queryByRole('region', { name: 'Stage: Old location' })).not.toBeInTheDocument()); });
test('autoplay stops at the last post and opening settings stops the timer', async () => { mount(); await screen.findByText('First words'); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' })); fireEvent.click(screen.getByRole('button', { name: 'Presentation settings' })); expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false'); fireEvent.click(screen.getByRole('button', { name: 'Close settings' })); fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' })); await import('@testing-library/react').then(({ act }) => act(async () => { await vi.advanceTimersByTimeAsync(5000); })); expect(screen.getByText('Last words')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Start autoplay' })).toHaveAttribute('aria-pressed', 'false'); vi.useRealTimers(); });
test('failed actor images fall through every candidate to readable initials', async () => { vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: [{ ref: 'mara', version_id: 'v1', name: 'Mara', speaking: true, automatic_outfit: null, outfits: [], candidates: ['sprite.png', 'avatar.png'].map(url => ({ url, presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: 'bottom-center' } })) }] }); mount(); const image = await screen.findByRole('img', { name: 'Mara' }); fireEvent.error(image); expect(screen.getByRole('img', { name: 'Mara' })).toHaveAttribute('src', 'avatar.png'); fireEvent.error(screen.getByRole('img', { name: 'Mara' })); expect(screen.getByLabelText('Mara portrait unavailable')).toHaveTextContent('M'); });
test('a save completed after closing settings cannot replace a newer post stage', async () => { let resolve!: (s: typeof stage) => void; vi.mocked(api.saveVNPresentation).mockImplementationOnce(() => new Promise(r => { resolve = r; })); vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id, location_name: id === 'b' ? 'Saltmarch' : null })); mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Presentation settings' })); fireEvent.click(screen.getByRole('button', { name: 'Save presentation' })); fireEvent.click(screen.getByRole('button', { name: 'Close settings' })); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByRole('region', { name: 'Stage: Saltmarch' }); const { act } = await import('@testing-library/react'); await act(async () => resolve({ ...stage, location_name: 'Old location' })); expect(screen.getByRole('region', { name: 'Stage: Saltmarch' })).toBeInTheDocument(); });
test('automatic expressions belong only to the historical automatic outfit', async () => { vi.mocked(api.getVNPost).mockResolvedValue({ ...stage, actors: [{ ref: 'mara', version_id: 'v1', name: 'Mara', speaking: true, automatic_outfit: 'default', candidates: [], outfits: [{ id: 'default', label: 'Default', expressions: ['neutral'] }, { id: 'armor', label: 'Armor', expressions: ['joy'] }] }] }); mount(); await screen.findByText('First words'); fireEvent.click(screen.getByRole('button', { name: 'Presentation settings' })); const expression = screen.getByRole('combobox', { name: 'Expression' }); expect(expression).toHaveTextContent('neutral'); expect(expression).not.toHaveTextContent('joy'); fireEvent.change(screen.getByRole('combobox', { name: 'Outfit' }), { target: { value: 'armor' } }); expect(expression).toHaveTextContent('joy'); expect(expression).not.toHaveTextContent('neutral'); });
test('a different background loads visibly after the previous image failed', async () => { vi.mocked(api.getVNPost).mockImplementation(async (_c, _s, id) => ({ ...stage, post_id: id, background_url: id === 'a' ? '/old-background.png' : '/new-background.png' })); mount(); await screen.findByText('First words'); const oldImage = document.querySelector<HTMLImageElement>('img[src="/old-background.png"]')!; fireEvent.error(oldImage); expect(oldImage).not.toBeVisible(); fireEvent.click(screen.getByRole('button', { name: 'Next' })); await screen.findByText('Last words'); const newImage = document.querySelector<HTMLImageElement>('img[src="/new-background.png"]')!; expect(newImage).toBeVisible(); });
