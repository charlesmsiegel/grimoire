import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

interface MarkdownProps {
  children: string;
  className?: string;
}

// Memoized: parsing markdown (react-markdown + remark plugins) is the dominant
// cost when a scene has many posts. Ancestors like PlayView re-render on every
// keystroke in the compose box; without memo each re-render re-parses every
// post's prose, which made typing laggy. Props are primitive strings, so the
// default shallow comparison is exact.
export const Markdown = memo(function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={className ? `markdown ${className}` : "markdown"}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{children}</ReactMarkdown>
    </div>
  );
});
