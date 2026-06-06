import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// Trivial smoke test: confirms Vitest + jsdom + React Testing Library
// are wired correctly. Richer per-component tests can land alongside.
function Greeting({ name }: { name: string }) {
  return <h1>Hello, {name}!</h1>;
}

describe("frontend smoke", () => {
  it("renders a greeting", () => {
    render(<Greeting name="Ironhold" />);
    expect(screen.getByRole("heading")).toHaveTextContent("Hello, Ironhold!");
  });
});
