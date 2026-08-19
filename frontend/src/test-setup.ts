import "@testing-library/jest-dom";
import { act, configure, getConfig } from "@testing-library/react";

// jsdom doesn't implement Element.scrollTo; CampaignView's autoscroll effect calls it.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

// jsdom's window.scrollTo exists but logs "Not implemented"; CharacterEditor
// calls it on navigation. Replace with a silent no-op for clean test output.
window.scrollTo = () => {};

// jsdom implements no window.matchMedia at all. CampaignView asks it whether the
// viewport is narrow enough for the inspector to become an overlay. Reports "no
// match", so tests see the ordinary wide layout unless one overrides this
// itself — jsdom has no real viewport to answer from either way.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},      // deprecated, but jsdom consumers still probe for it
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// ---- Every `await` here means "the page has gone quiet", not "one query passed" (#351).
//
// A page under test settles in stages, each one its own macrotask: the campaign
// read lands, its answer starts the scene-list read, that starts the resolver's
// redirect, and *that* starts a dozen parallel reads whose answers each commit
// separately. React Testing Library's async utilities drain exactly ONE
// macrotask after the queried condition holds (`asyncWrapper`, in
// @testing-library/react's `pure.js`), so the FIRST `await` in a test returns
// somewhere in the middle of that chain.
//
// On an idle machine the rest lands inside the next poll interval anyway, so
// every test reads as though `await screen.findByText("hi")` meant "the page is
// ready". Under `make check-web` on a shared runner — vitest puts a jsdom
// worker on every core and they compete for it — it does not, and the
// statement after that `await` runs against a page that is still building
// itself. Measured on a 4-CPU container running the suite with file
// parallelism, the same three shapes, always in a different test (#351):
//
//   - a control that is DISABLED until its data arrives. `fireEvent.click` on a
//     disabled button dispatches nothing and reports nothing, so the request it
//     would have made never happens and the test waits out the whole ceiling
//     for it ("End scene" is the one that showed up most).
//   - a control that is not RENDERED until its data arrives: the transcript
//     gutter's Edit/🗑 are behind `transcriptIsActive`, which only becomes true
//     when the scene's own read lands — after the posts it rendered.
//   - a value read straight off an element that is on screen but not yet
//     filled (`expect(await screen.findByLabelText(…)).toHaveValue("terse")`).
//
// None of them is a hang, which is why raising the ceiling does not help: 4 of 6
// runs still failed with `asyncUtilTimeout` at 5s. What the tests are missing is
// not time, it is a definition of "settled" — so that is what the wait means
// now. Quiet is measured off the rendered DOM rather than off any one file's
// mocks, because the same shape has now been seen in two suites that share no
// fixtures (`CampaignView`, `ResponsePresetPicker`).
//
// Two consecutive unchanged ticks, not one: a stage that lands a value without
// moving the DOM (a fetch identity, a token) still starts the next stage, and
// one tick would stop on it. `act` so React's commits are flushed rather than
// merely scheduled, and a hard cap so a component that genuinely never settles
// fails on the test's own timeout instead of spinning here.
const SETTLE_TICKS = 25;

async function settle() {
  let quiet = 0;
  let last = "";
  for (let i = 0; i < SETTLE_TICKS && quiet < 2; i++) {
    const now = document.body.innerHTML;
    quiet = now === last ? quiet + 1 : 0;
    last = now;
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

// Wraps RTL's own wrapper rather than replacing it: that one owns the act
// environment for the duration of the wait, and dropping it would put every
// `waitFor` back to warning about updates it deliberately allows.
const rtlAsyncWrapper = getConfig().asyncWrapper;
configure({
  asyncWrapper: async (cb) => {
    const result = await rtlAsyncWrapper(cb);
    await settle();
    return result;
  },
});
