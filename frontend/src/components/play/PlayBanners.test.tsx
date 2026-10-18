import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "../../api/client";
import { PlayBanners, type PlayError } from "./PlayBanners";

type Props = React.ComponentProps<typeof PlayBanners>;

function show(over: Partial<Props> = {}) {
  const props: Props = {
    ready: true, problem: null, readyId: "not-ready", budget: null, budgetSeen: null,
    onBudgetSeen: vi.fn(), error: null, onRetry: vi.fn(), retryDisabled: false,
    onDismissError: vi.fn(), ...over,
  };
  render(<MemoryRouter><PlayBanners {...props} /></MemoryRouter>);
  return props;
}

const missingKey: PlayError = {
  text: "OpenRouter key not set", retryable: false, from: "scene",
  err: new ApiError(409, "OpenRouter key not set (OpenRouter, the Primary role)", "missing_key"),
};

test("no model ready is one banner, with the campaign's own reason and both ways to fix it", () => {
  show({ ready: false, problem: "OpenRouter key not set (OpenRouter, the Primary role)",
         error: missingKey });
  const banner = screen.getByText(/No model is ready to generate/);
  expect(banner).toHaveAttribute("id", "not-ready");
  expect(banner).toHaveTextContent("OpenRouter key not set (OpenRouter, the Primary role)");
  expect(screen.getByRole("link", { name: "Models page" })).toHaveAttribute("href", "/models");
  expect(screen.getByRole("link", { name: "Providers page" })).toHaveAttribute("href", "/providers");
  // The failed turn's own banner said the same thing again, with a Retry that
  // sent the same request into the same refusal.
  expect(document.querySelectorAll(".banner")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

test("a settings refusal while a model is ready names the page that fixes it, and no Retry", () => {
  show({ error: missingKey });
  expect(screen.getByRole("link", { name: /Providers page/ })).toHaveAttribute("href", "/providers");
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

test.each([
  { from: "absorb:s1", detail: "The review provider has no key" },
  { from: "absorb:s1", detail: "The scene provider has no key" },
  { from: "scene", detail: "A different provider cannot read images" },
  { from: undefined, detail: "The scene provider has no key" },
])("unrelated readiness failures remain visible ($from, $detail)", ({ from, detail }) => {
  show({ ready: false, problem: "The scene provider has no key",
    error: { text: detail, retryable: false, from,
      err: new ApiError(409, detail, "missing_key") } });
  expect(document.querySelectorAll(".banner")).toHaveLength(2);
  expect(screen.getByText(detail)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
});

test("a model that cannot do the job points at Models", () => {
  show({ error: { text: "cannot", retryable: false,
                  err: { detail: "cannot read images", kind: "incapable" } } });
  expect(screen.getByRole("link", { name: /Models page/ })).toHaveAttribute("href", "/models");
});

test("any failure can be dismissed, and a turn's can be retried", () => {
  const { onRetry, onDismissError } = show({
    error: { text: "boom", retryable: true, err: new ApiError(500, "boom") }, retryDisabled: false });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(onDismissError).toHaveBeenCalledTimes(1);
});

test("Retry waits while a turn runs", () => {
  show({ error: { text: "boom", retryable: true }, retryDisabled: true });
  expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  expect(screen.getByText("boom")).toBeInTheDocument();
});
