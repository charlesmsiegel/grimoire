import "@testing-library/jest-dom";

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
