import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function Probe() {
  const { name, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="name">{name}</span>
      <button onClick={() => setTheme("terminal")}>switch</button>
    </div>
  );
}

test("applies theme tokens and data-theme, and switches", () => {
  render(
    <ThemeProvider initial="occult">
      <Probe />
    </ThemeProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe("occult");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#caa45a");
  expect(document.documentElement.style.getPropertyValue("--quote")).toBe("#7fc8b0");

  fireEvent.click(screen.getByText("switch"));
  expect(screen.getByTestId("name").textContent).toBe("terminal");
  expect(document.documentElement.dataset.theme).toBe("terminal");
  expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#3fae57");
});

test("unknown theme falls back to default", () => {
  render(<ThemeProvider initial="does-not-exist"><Probe /></ThemeProvider>);
  expect(document.documentElement.dataset.theme).toBe("occult");
});
