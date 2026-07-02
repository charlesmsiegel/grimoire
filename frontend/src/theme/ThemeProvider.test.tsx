import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function Probe() {
  const { name, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="name">{name}</span>
      <button onClick={() => setTheme("manuscript")}>switch</button>
    </div>
  );
}

test("applies theme tokens and data-theme, and switches", () => {
  render(
    <ThemeProvider initial="codex">
      <Probe />
    </ThemeProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe("codex");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#c0392b");
  expect(document.documentElement.style.getPropertyValue("--quote")).toBe("#c0392b");

  fireEvent.click(screen.getByText("switch"));
  expect(screen.getByTestId("name").textContent).toBe("manuscript");
  expect(document.documentElement.dataset.theme).toBe("manuscript");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#c8a44d");
});

test("unknown theme falls back to default", () => {
  render(<ThemeProvider initial="does-not-exist"><Probe /></ThemeProvider>);
  expect(document.documentElement.dataset.theme).toBe("codex");
});
