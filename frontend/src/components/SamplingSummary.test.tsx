import { render, screen } from "@testing-library/react";
import { SamplingSummary } from "./SamplingSummary";

const BASE = { preset_id: "warm", preset_name: "Warm", scope: "campaign" as const,
               kind: "openai_compatible", applied: { temperature: 0.7 }, dropped: [],
               verified: true };

test("names the preset, where it came from, and what is sent", () => {
  render(<SamplingSummary report={BASE} />);
  expect(screen.getByText("Sampler: Warm (from this campaign's routing)")).toBeInTheDocument();
  expect(screen.getByText("temperature 0.7")).toBeInTheDocument();
});

test("every dropped parameter is listed with its reason", () => {
  render(<SamplingSummary report={{ ...BASE, dropped: [
    { param: "min_p", reason: "not part of the OpenAI API" },
    { param: "top_k", reason: "not part of the OpenAI API" }] }} />);
  expect(screen.getByText("Not sent: min_p")).toBeInTheDocument();
  expect(screen.getByText("Not sent: top_k")).toBeInTheDocument();
});

test("a cleared route and no preset at all read differently", () => {
  const { rerender } = render(
    <SamplingSummary report={{ ...BASE, preset_id: "", preset_name: "", applied: {},
                               scope: "global" }} />);
  expect(screen.getByText("Sampler: No preset (cleared by the global routing)")).toBeInTheDocument();
  rerender(<SamplingSummary report={{ ...BASE, preset_id: "", preset_name: "", applied: {},
                                      scope: "none" }} />);
  expect(screen.getByText("Sampler: No preset — provider defaults")).toBeInTheDocument();
});

test("an unverified OpenRouter split says it cannot check", () => {
  render(<SamplingSummary report={{ ...BASE, kind: "openrouter", verified: false }} />);
  expect(screen.getByText(/Unverified/)).toBeInTheDocument();
});

test("no report renders nothing", () => {
  const { container } = render(<SamplingSummary report={null} />);
  expect(container).toBeEmptyDOMElement();
});
