import type { Definition, Nodes, PhrasingContent, Root, RootContent } from 'mdast';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { defaultUrlTransform } from 'react-markdown';
import { withoutComments } from '../../markdown/commentPlugin';
import { remarkQuotePlugin } from '../../markdown/quotePlugin';

export type Splash = { url: string; alt: string; postId: string; id: string };
export type ReadingStep = { tree: Root; splash?: Splash };

// Reading position, not elapsed time or render order, owns the picture. Each
// step is a slice of parsed prose with its inherited wrappers and backdrop;
// walking backward therefore needs no inverse events. A future pacing option
// changes the ranges below without changing navigation or stored narrative.
const parser = unified().use(remarkParse).use(remarkGfm).use(remarkQuotePlugin);
const TITLES = /\b(?:Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr|vs|etc|e\.g|i\.e)\.$/i;
type SegmenterClass = new (locale: string, options: { granularity: 'sentence' }) => {
  segment(text: string): Iterable<{ index: number; segment: string }>;
};
// Older Android WebViews may lack Segmenter. Keep that case readable, while
// using the platform's Unicode sentence rules wherever they are available.
const Segmenter = (Intl as typeof Intl & { Segmenter?: SegmenterClass }).Segmenter;
const segmenter = Segmenter ? new Segmenter('en', { granularity: 'sentence' }) : null;

function ranges(text: string): [number, number][] {
  const segments = segmenter ? [...segmenter.segment(text)]
    : [...text.matchAll(/[\s\S]*?[.!?]+["'”’)]*(?:\s+|$)|[\s\S]+$/g)]
      .map(m => ({ index: m.index, segment: m[0] }));
  const out: [number, number][] = [];
  for (const { index, segment } of segments) {
    const previous = out[out.length - 1];
    if (previous && TITLES.test(text.slice(previous[0], previous[1]).trim())) previous[1] = index + segment.length;
    else out.push([index, index + segment.length]);
  }
  return out.length ? out : [[0, text.length]];
}

function visible(node: Nodes, mask = false): string {
  if ('children' in node) return node.children.map(n => visible(n, mask)).join('');
  if (node.type === 'image') return '\uFFFC';
  if (node.type === 'break') return '\n';
  if (node.type === 'html') return withoutComments(node.value);
  if ('value' in node) return mask && node.type === 'inlineCode' ? 'x'.repeat(node.value.length) : node.value;
  return '';
}

/** Clip by rendered text offsets rather than Markdown offsets: a strong node
 * spanning three sentences remains a strong node on all three pages. */
function clip(node: Nodes, start: number, end: number): Nodes | null {
  const size = visible(node).length;
  if (end <= 0 || start >= size) return null;
  if ('children' in node) {
    let offset = 0;
    const children = node.children.flatMap(child => {
      const result = clip(child, start - offset, end - offset);
      offset += visible(child).length;
      return result ? [result] : [];
    });
    return { ...node, children } as Nodes;
  }
  if (node.type === 'text' || node.type === 'html') {
    return { ...node, value: visible(node).slice(Math.max(0, start), end) };
  }
  return node;
}

function resolve(node: Nodes, definitions: Map<string, Definition>): Nodes {
  if (node.type === 'imageReference' || node.type === 'linkReference') {
    const definition = definitions.get(node.identifier.toUpperCase());
    if (definition) return node.type === 'imageReference'
      ? { type: 'image', url: definition.url, alt: node.alt, title: definition.title }
      : { type: 'link', url: definition.url, title: definition.title, children: node.children.map(n => resolve(n, definitions) as PhrasingContent) };
  }
  if ('children' in node) return { ...node, children: node.children.map(n => resolve(n, definitions)) } as Nodes;
  return node;
}

export function readingSteps(content: string, postId: string, inherited?: Splash): { steps: ReadingStep[]; trailingSplash: Splash | undefined } {
  const parsed = parser.parse(content);
  const definitions = new Map<string, Definition>();
  function collect(node: Nodes) {
    if (node.type === 'definition' && !definitions.has(node.identifier.toUpperCase())) definitions.set(node.identifier.toUpperCase(), node);
    if ('children' in node) node.children.forEach(collect);
  }
  collect(parsed);
  const tree = parser.runSync(resolve(parsed, definitions)) as Root;
  const steps: ReadingStep[] = [];
  let splash = inherited;
  let imageIndex = 0;
  const supporting = tree.children.filter(n => n.type === 'definition' || n.type === 'footnoteDefinition');
  function emit(nodes: RootContent[]) {
    steps.push({ tree: { type: 'root', children: [...nodes, ...supporting] }, ...(splash ? { splash } : {}) });
  }
  function block(node: Nodes, wrap: (n: Nodes) => RootContent = n => n as RootContent) {
    if (node.type === 'definition' || node.type === 'footnoteDefinition') return;
    if (node.type === 'blockquote' || node.type === 'list' || node.type === 'listItem') {
      node.children.forEach((child, i) => block(child, n => wrap({ ...node,
        ...(node.type === 'list' && node.ordered ? { start: (node.start ?? 1) + i } : {}), children: [n] } as Nodes)));
      return;
    }
    if (node.type !== 'paragraph') {
      if (visible(node).trim() || node.type === 'thematicBreak') emit([wrap(node)]);
      return;
    }
    const text = visible(node);
    const mask = visible(node, true);
    const images: { at: number; node: Nodes }[] = [];
    let offset = 0;
    function findImages(n: Nodes) {
      if ('children' in n) n.children.forEach(findImages);
      else { if (n.type === 'image') images.push({ at: offset, node: n }); offset += visible(n).length; }
    }
    findImages(node);
    const spans: [number, number][] = [];
    let start = 0;
    for (const image of images) { spans.push([start, image.at]); start = image.at + 1; }
    spans.push([start, text.length]);
    const sentences = spans.map(([from, to]) => ranges(mask.slice(from, to))
      .map(([a, b]): [number, number] => [a + from, b + from])
      .filter(([a, b]) => text.slice(a, b).trim()));
    const count = sentences.reduce((total, s) => total + s.length, 0);
    spans.forEach(([from, to], i) => {
      const chunks = count <= 2 ? [[from, to]] : sentences[i];
      for (const [a, b] of chunks) {
        if (!text.slice(a, b).trim()) continue;
        const sliced = clip(node, a, b);
        if (sliced) emit([wrap(sliced)]);
      }
      const image = images[i]?.node;
      if (image?.type === 'image') {
        const url = defaultUrlTransform(image.url);
        // Match the transcript renderer's safe URL transform; rejected images
        // must not become a fetch merely because they moved to the backdrop.
        if (url) {
          splash = { url, alt: image.alt || 'Scene illustration', postId, id: `${postId}:${imageIndex++}` };
          emit([]);
        }
      }
    });
    // Only image-only paragraphs introduce art for the next prose paragraph.
    // A final inline image gets its own encounter step, then expires here.
    if (count) splash = undefined;
  }
  tree.children.forEach(node => block(node));
  if (!steps.length) emit([]);
  return { steps, trailingSplash: splash };
}
