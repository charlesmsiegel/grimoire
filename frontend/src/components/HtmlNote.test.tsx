import { fireEvent, render } from "@testing-library/react";
import { HtmlNote } from "./HtmlNote";

// jsdom has no layout, so fit()'s measurements are faked: the first
// scrollHeight read is the zero-viewport probe (static content), later reads
// simulate what the content does once the frame has real height.
function mountWithHeights(heights: number[]) {
  const { container } = render(<HtmlNote html="<p>notes</p>" title="Creator notes" />);
  const frame = container.querySelector("iframe") as HTMLIFrameElement;
  const root = frame.contentDocument!.documentElement;
  let call = 0;
  Object.defineProperty(root, "scrollHeight", {
    configurable: true,
    get: () => heights[Math.min(call++, heights.length - 1)],
  });
  fireEvent.load(frame);
  return { frame, root };
}

test("static content sizes the frame to fit exactly", () => {
  const { frame, root } = mountWithHeights([600, 600]);
  expect(frame.style.height).toBe("600px");
  expect(root.style.overflow).toBe("");
});

test("viewport-tracking content is clipped, never an internal scroller", () => {
  // Content that re-expands past the granted height (100vh sections, fixed
  // overlays) must be clipped at its static height: an internally scrolling
  // near-fullscreen iframe swallows wheel events and pins the page.
  const { frame, root } = mountWithHeights([600, 1500]);
  expect(frame.style.height).toBe("600px");
  expect(root.style.overflow).toBe("hidden");
  expect(frame.contentDocument!.body.style.overflow).toBe("hidden");
});
