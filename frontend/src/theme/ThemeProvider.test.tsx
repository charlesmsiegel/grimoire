import { render, screen, fireEvent, act } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function Probe() {
  const { mode, name, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="name">{name}</span>
      <button onClick={() => setTheme("dark")}>dark</button>
      <button onClick={() => setTheme("system")}>system</button>
    </div>
  );
}

/** jsdom ships no `matchMedia`. Installing one that answers `matches` from a
 *  mutable cell — and remembers its listeners — is what lets a test flip the
 *  OS preference the way the OS does, rather than only at mount. */
function stubMatchMedia(dark: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const mql = {
    matches: dark,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => { listeners.add(fn); },
    removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => { listeners.delete(fn); },
  };
  (window as unknown as { matchMedia: unknown }).matchMedia = () => mql;
  return {
    set(next: boolean) {
      mql.matches = next;
      act(() => {
        for (const fn of listeners) fn({ matches: next } as MediaQueryListEvent);
      });
    },
  };
}

afterEach(() => {
  delete (window as unknown as { matchMedia?: unknown }).matchMedia;
});

test("applies the chosen mode's tokens, and switches", () => {
  stubMatchMedia(false);
  render(
    <ThemeProvider initial="light">
      <Probe />
    </ThemeProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(document.documentElement.dataset.themeMode).toBe("light");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#0d6c70");

  fireEvent.click(screen.getByText("dark"));
  expect(screen.getByTestId("name").textContent).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#6fe0da");
});

test("a theme name from the three-theme era maps to a mode", () => {
  stubMatchMedia(false);
  render(<ThemeProvider initial="astral"><Probe /></ThemeProvider>);
  expect(screen.getByTestId("mode").textContent).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("an unknown stored theme follows the system", () => {
  stubMatchMedia(true);
  render(<ThemeProvider initial="does-not-exist"><Probe /></ThemeProvider>);
  expect(screen.getByTestId("mode").textContent).toBe("system");
  expect(screen.getByTestId("name").textContent).toBe("dark");
});

test("system re-resolves when the OS preference flips", () => {
  const os = stubMatchMedia(false);
  render(<ThemeProvider initial="system"><Probe /></ThemeProvider>);
  expect(screen.getByTestId("name").textContent).toBe("light");

  os.set(true);
  expect(screen.getByTestId("name").textContent).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  // The *choice* is still system — the control must not jump to Dark.
  expect(screen.getByTestId("mode").textContent).toBe("system");
});

test("an explicit mode ignores the OS preference", () => {
  const os = stubMatchMedia(false);
  render(<ThemeProvider initial="dark"><Probe /></ThemeProvider>);
  expect(screen.getByTestId("name").textContent).toBe("dark");
  os.set(true);
  expect(screen.getByTestId("name").textContent).toBe("dark");
});

test("renders without matchMedia at all", () => {
  // The Android WebView is old enough in the field to be worth not trusting.
  render(<ThemeProvider initial="system"><Probe /></ThemeProvider>);
  expect(screen.getByTestId("name").textContent).toBe("light");
});
