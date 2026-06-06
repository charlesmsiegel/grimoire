// Vitest setup: pulls in jest-dom's matchers (toHaveTextContent, etc.).
import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement matchMedia; ThemeProvider reads it at mount.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
