import "@testing-library/jest-dom";

// jsdom doesn't implement Element.scrollTo; ChatView's autoscroll effect calls it.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
