import { render, screen } from '@testing-library/react';
import { readingSteps } from './readingSteps';
import { ReadingStepContent } from './ReadingStep';

function texts(content: string) {
  return readingSteps(content, 'a').steps.map(step => {
    const view = render(<ReadingStepContent step={step} />);
    const text = view.container.textContent;
    view.unmount();
    return text?.trim();
  });
}

test('paces long paragraphs by sentence and keeps two sentences together', () => {
  expect(texts('Alone.\n\nOne. Two.\n\nFirst. Second! Third?'))
    .toEqual(['Alone.', 'One. Two.', 'First.', 'Second!', 'Third?']);
});

test.each([['"', '"'], ['“', '”']])('keeps %s dialogue highlighted across sentence pages', (open, close) => {
  const { steps } = readingSteps(`${open}First. Second. Third.${close} Plain narration.`, 'a');
  const view = render(<ReadingStepContent step={steps[1]} />);
  expect(screen.getByText('Second.').closest('.quoted')).not.toBeNull();
  view.rerender(<ReadingStepContent step={steps[2]} />);
  expect(screen.getByText(`Third.${close}`).closest('.quoted')).not.toBeNull();
  view.rerender(<ReadingStepContent step={steps[3]} />);
  expect(view.container.querySelector('.quoted')).toBeNull();
});

test('closing dialogue inside emphasis leaves following narration plain', () => {
  const { steps } = readingSteps('"One **two"** plain. Third. Fourth.', 'a');
  const view = render(<ReadingStepContent step={steps[0]} />);
  expect(screen.getByText('two"').closest('strong')).not.toBeNull();
  expect(screen.getByText('two"').closest('.quoted')).not.toBeNull();
  expect(view.container.querySelector('p')?.lastChild?.textContent).toBe(' plain. ');
  expect(view.container.querySelector('p')?.lastChild?.nodeType).toBe(Node.TEXT_NODE);
});

test('quote state resets at paragraphs and ignores literal quote marks in code and comments', () => {
  const { steps } = readingSteps('"Read [the map](/map) and `a"b` <!-- " --> now.\n\nPlain next paragraph.', 'a');
  const view = render(<ReadingStepContent step={steps[0]} />);
  expect(screen.getByRole('link', { name: 'the map' })).toHaveAttribute('href', '/map');
  expect(screen.getByText('a"b').tagName).toBe('CODE');
  expect(screen.getByText('now.').closest('.quoted')).not.toBeNull();
  view.rerender(<ReadingStepContent step={steps[1]} />);
  expect(view.container.querySelector('.quoted')).toBeNull();
});

test('keeps emphasis and resolved links intact across sentence boundaries', () => {
  const { steps } = readingSteps('**First. Second. Third.** Read [the map][map].\n\n[map]: /map', 'a');
  const view = render(<ReadingStepContent step={steps[1]} />);
  expect(screen.getByText('Second.').tagName).toBe('STRONG');
  view.rerender(<ReadingStepContent step={steps[3]} />);
  expect(screen.getByRole('link', { name: 'the map' })).toHaveAttribute('href', '/map');
});

test('does not treat titles, decimal points, or code punctuation as sentence endings', () => {
  expect(texts('Dr. Mara paid 3.50. She used `a.b()` today. "Ready?"'))
    .toEqual(['Dr. Mara paid 3.50.', 'She used a.b() today.', '"Ready?"']);
});

test('splashes an image through the following paragraph, then restores the backdrop', () => {
  const { steps } = readingSteps('![Forest](/forest.png)\n\nFirst. Second. Third.\n\nHome.', 'a');
  expect(steps.map(s => s.splash?.url)).toEqual(['/forest.png', '/forest.png', '/forest.png', '/forest.png', undefined]);
  render(<ReadingStepContent step={steps[1]} />);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('encounters images in order even between two sentences and resolves references', () => {
  const { steps } = readingSteps('Before. ![First][pic] After.\n\n![Second](/second.png)\n\n[pic]: /first.png', 'a');
  expect(steps.map(s => s.splash?.url)).toEqual([undefined, '/first.png', '/first.png', '/second.png']);
  expect(texts('Before. ![First](/first.png) After.')).toEqual(['Before.', '', 'After.']);
});

test('trailing images carry to the next prose paragraph; code remains literal', () => {
  const first = readingSteps('![First](/first.png)\n\n![Second](/second.png)', 'a');
  const second = readingSteps('Next.\n\nLater.', 'b', first.trailingSplash);
  expect(second.steps.map(s => s.splash?.url)).toEqual(['/second.png', undefined]);
  expect(texts('```md\n![Not art](/no.png)\n\nOne. Two. Three.\n```')).toEqual(['![Not art](/no.png)\n\nOne. Two. Three.']);
});

test('hidden comments do not create empty reading steps and unsafe art is not loaded', () => {
  expect(texts('<!-- hidden -->\n\nVisible.')).toEqual(['Visible.']);
  const result = readingSteps('![Bad](javascript:alert)\n\nVisible.', 'a');
  expect(result.steps.every(s => !s.splash)).toBe(true);
});

test('nested reference images resolve in reference links using the first definition', () => {
  const result = readingSteps('[![Map][pic]][dest]\n\n[pic]: /map.png\n[pic]: /other.png\n[dest]: /large.png', 'a');
  expect(result.steps[0].splash?.url).toBe('/map.png');
});

test('ordered list passages retain their original item numbers', () => {
  const result = readingSteps('3. First item.\n4. Second item.\n5. Third item.', 'a');
  const view = render(<ReadingStepContent step={result.steps[1]} />);
  expect(view.container.querySelector('ol')).toHaveAttribute('start', '4');
  expect(screen.getByRole('listitem')).toHaveTextContent('Second item.');
});

test('an image at the end of prose expires at that paragraph boundary', () => {
  const result = readingSteps('Before. ![Forest](/forest.png)\n\nAfter.', 'a');
  expect(result.steps.map(s => s.splash?.url)).toEqual([undefined, '/forest.png', undefined]);
});
