import "@testing-library/jest-dom";

// jsdom doesn't implement Element.scrollTo; CampaignView's autoscroll effect calls it.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

// jsdom's window.scrollTo exists but logs "Not implemented"; CharacterEditor
// calls it on navigation. Replace with a silent no-op for clean test output.
window.scrollTo = () => {};
