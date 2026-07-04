import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

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
  h1: SceneLabel, h2: SceneLabel, h3: SceneLabel,
  h4: SceneLabel, h5: SceneLabel, h6: SceneLabel,
};

export function GreetingMarkdown({ children, imageExtras }:
    { children: string; imageExtras?: (src: string) => ReactNode }) {
  const text = children.replace(/^#(.+?)#\s*$/gm, (_m, label) => `### ${label.trim()}`);
  // imageExtras hangs per-image UI (subject chips, pickers) under each image;
  // wrapped in spans because react-markdown renders images inside <p>.
  const withImages = imageExtras
    ? {
        ...components,
        img: ({ src, alt }: { src?: string; alt?: string }) => (
          <span className="img-block">
            <img src={src} alt={alt ?? ""} />
            <span className="img-extras">{imageExtras(src ?? "")}</span>
          </span>
        ),
      }
    : components;
  return (
    <div className="detail-rendered">
      <Markdown remarkPlugins={[remarkGfm, remarkBreaks]} components={withImages}>
        {text}
      </Markdown>
    </div>
  );
}
