import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TokenBadge } from "../TokenBadge";

describe("TokenBadge", () => {
  it("renders an approximate token count with separators", () => {
    render(<TokenBadge text={"a".repeat(4000)} />);
    // 4000/4 = 1000 (fallback before encoder loads)
    expect(screen.getByText(/~1,000 tokens/)).toBeInTheDocument();
  });

  it("renders ~0 tokens for empty text", () => {
    render(<TokenBadge text="" />);
    expect(screen.getByText(/~0 tokens/)).toBeInTheDocument();
  });
});
