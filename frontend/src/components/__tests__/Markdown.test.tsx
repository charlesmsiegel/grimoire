import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// Count how many times the underlying markdown parser is invoked. Re-parsing
// markdown on every keystroke (because an ancestor re-rendered) is what made
// the Play text box laggy; memoizing Markdown keeps this at one parse per
// distinct body.
let parseCount = 0;
vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => {
    parseCount += 1;
    return <div data-testid="md">{children}</div>;
  },
}));

import { Markdown } from "../Markdown";

afterEach(() => {
  parseCount = 0;
  vi.restoreAllMocks();
});

describe("Markdown memoization", () => {
  it("does not re-parse when an ancestor re-renders with identical props", () => {
    function Parent() {
      const [n, setN] = useState(0);
      return (
        <div>
          <button onClick={() => setN((v) => v + 1)}>bump {n}</button>
          <Markdown>Hello **world**</Markdown>
        </div>
      );
    }

    render(<Parent />);
    expect(parseCount).toBe(1);

    // Simulate the ancestor re-rendering on every keystroke. The Markdown
    // body is unchanged, so it must not re-parse.
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByRole("button"));

    expect(parseCount).toBe(1);
  });

  it("re-parses when the body actually changes", () => {
    const { rerender } = render(<Markdown>first</Markdown>);
    expect(parseCount).toBe(1);
    rerender(<Markdown>second</Markdown>);
    expect(parseCount).toBe(2);
  });
});
