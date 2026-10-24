import Markdown from 'react-markdown';
import { commentPlugin } from '../../markdown/commentPlugin';
import { quotePlugin } from '../../markdown/quotePlugin';
import { markdownImageComponents } from '../../markdown/MarkdownImage';
import type { ReadingStep } from './readingSteps';

export function ReadingStepContent({ step }: { step: ReadingStep }) {
  // The parser already resolved references and retained formatting. Feed that
  // tree into the normal safe Markdown renderer instead of serializing slices.
  return <Markdown components={markdownImageComponents}
    remarkPlugins={[() => () => structuredClone(step.tree)]}
    rehypePlugins={[commentPlugin, quotePlugin]}>{''}</Markdown>;
}
