import { render, screen } from "@testing-library/react";
import { CapabilityBadges } from "./CapabilityBadges";

test("shows yes, no and unverified, with a failed test's error in the title", () => {
  render(<CapabilityBadges capabilities={{
    generate: { value: "yes", source: "catalog" },
    vision: { value: "unknown", source: "test", error: "image input is not supported" },
    embed: { value: "no", source: "adapter" },
  }} />);

  expect(screen.getByText("generate: yes")).toBeInTheDocument();
  expect(screen.getByText("embed: no")).toBeInTheDocument();
  // `unknown` reads as unverified, never as a no: it hides nothing.
  const vision = screen.getByText("vision: unverified");
  expect(vision).toHaveAttribute("title", expect.stringContaining(
    "image input is not supported"));
  // A capability the server did not answer for is unverified too, not absent.
  expect(screen.getByText("decide: unverified")).toBeInTheDocument();
});
