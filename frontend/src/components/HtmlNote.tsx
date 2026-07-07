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

  // Size the frame to its content. Called on load and, via the ResizeObserver
  // below, when the content later resizes (e.g. an image finishes loading).
  function measure() {
    const frame = ref.current;
    const root = frame?.contentDocument?.documentElement;
    if (!frame || !root) return;
    // Measure at a zero-height viewport so viewport-relative (vh) content
    // collapses and can't feed the measurement back into itself. All writes
    // share one JS turn, so the probe state is never painted.
    frame.style.height = "0px";
    const staticH = root.scrollHeight;
    frame.style.height = `${staticH}px`;
    // Content that tracks the viewport (100vh sections, fixed overlays)
    // re-expands past any height we set. Clip it at the static height: the
    // real content already fits (measured with vh collapsed), and a frame
    // that scrolls internally would swallow wheel events and pin the page.
    if (root.scrollHeight > staticH + 1) {
      root.style.overflow = "hidden";
      const body = frame.contentDocument?.body;
      if (body) body.style.overflow = "hidden";
    }
  }

  // Fit once, then observe the content for later resizes. The observer is
  // (re)created only here, per document load — NOT inside measure(). Recreating
  // it on every resize (as this once did) makes each fresh observe() emit an
  // initial callback, an unbroken per-frame fit loop whose transient 0px height
  // writes pin the parent page's wheel scroll in Firefox — you can't scroll
  // past the note. Chromium tolerated the loop, hiding the bug.
  function onLoad() {
    const root = ref.current?.contentDocument?.documentElement;
    if (!root) return;
    measure();
    roRef.current?.disconnect();
    if (typeof ResizeObserver !== "undefined") {
      roRef.current = new ResizeObserver(() => measure());
      roRef.current.observe(root);
    }
  }

  return <iframe ref={ref} className="html-note" title={title} srcDoc={doc}
                 sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                 onLoad={onLoad} />;
}
