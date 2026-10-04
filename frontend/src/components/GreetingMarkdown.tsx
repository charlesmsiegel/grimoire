import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { ImageExtrasContext, MarkdownImage } from "../markdown/MarkdownImage";

// Renderer for greeting-style card text (first_mes, alternate greetings, world
// greetings). Chub-style cards open greetings with `#Scene Label#` — not valid
// markdown (no space after #), so it would render as literal text, hashes and
// all. Convert that convention to a real heading, then render every heading as
// a small scene label rather than page-sized h1/h2 text. remark-breaks keeps
// single newlines as line breaks: this is chat text, not prose markdown.
function SceneLabel({ children }: { children?: ReactNode }) {
  const kids = Array.isArray(children) ? [...children] : children != null ? [children] : [];
  const last = kids[kids.length - 1];
  if (typeof last === "string") kids[kids.length - 1] = last.replace(/#+\s*$/, "").trimEnd();
  return <div className="scene-label">{kids}</div>;
}

const components = {
  img: MarkdownImage,
  h1: SceneLabel, h2: SceneLabel, h3: SceneLabel,
  h4: SceneLabel, h5: SceneLabel, h6: SceneLabel,
};

export function GreetingMarkdown({ children, imageExtras }:
    { children: string; imageExtras?: (src: string) => ReactNode }) {
  const text = children.replace(/^#(.+?)#\s*$/gm, (_m, label) => `### ${label.trim()}`);
  // Context keeps the image component identity stable when extras change.
  return (
    <div className="detail-rendered">
      <ImageExtrasContext.Provider value={imageExtras}>
      <Markdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
        {text}
      </Markdown>
      </ImageExtrasContext.Provider>
    </div>
  );
}
