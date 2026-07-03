import { useEffect, useMemo, useRef } from "react";

const TAG_RE = /<[a-z!][^>]*>/i;

/** Untrusted card HTML (creator notes) rendered inside a sandboxed iframe:
 *  scripts never run, its CSS cannot leak, and fixed overlays stay confined
 *  to the frame. The frame stretches to fit its content. */
export function HtmlNote({ html, title }: { html: string; title: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);

  const doc = useMemo(() => {
    const cs = getComputedStyle(document.body);
    const pre = TAG_RE.test(html) ? "" : "white-space:pre-wrap;";
    return `<!doctype html><html><head><base target="_blank"><style>` +
      `body{margin:0;font-family:${cs.fontFamily};font-size:${cs.fontSize};` +
      `line-height:1.5;color:${cs.color};${pre}overflow-wrap:anywhere}` +
      `img{max-width:100%;height:auto}` +
      `</style></head><body>${html}</body></html>`;
  }, [html]);

  useEffect(() => () => roRef.current?.disconnect(), []);

  function fit() {
    const frame = ref.current;
    const root = frame?.contentDocument?.documentElement;
    if (!frame || !root) return;
    frame.style.height = `${root.scrollHeight}px`;
    roRef.current?.disconnect();
    if (typeof ResizeObserver !== "undefined") {
      roRef.current = new ResizeObserver(() => {
        frame.style.height = `${root.scrollHeight}px`;
      });
      roRef.current.observe(root);
    }
  }

  return <iframe ref={ref} className="html-note" title={title} srcDoc={doc}
                 sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                 onLoad={fit} />;
}
