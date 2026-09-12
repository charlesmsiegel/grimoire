import { fireEvent, render, screen } from "@testing-library/react";
import { Thinking, SavedThinking } from "./Thinking";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { getResponse: vi.fn() } }));

test("thinking is collapsed, updates while open, and treats markup as text", () => {
  const { container, rerender } = render(<Thinking content="Planning <script>bad()</script>" />);
  expect(container.querySelector("script")).toBeNull();
  const details = container.querySelector("details")!;
  expect(details).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("Thinking"));
  expect(details).toHaveAttribute("open");
  rerender(<Thinking content="More planning" />);
  expect(details).toHaveAttribute("open");
  expect(screen.getByText(/More planning/)).toHaveTextContent("<thinking>");
  expect(screen.getByText(/More planning/)).toHaveTextContent("</thinking>");
  expect(container.querySelector("script")).toBeNull();
});

test("saved thinking is fetched on expansion and selects the displayed variant", async () => {
  vi.mocked(api.getResponse).mockResolvedValue({ variants: [
    {id:"old", reasoning:"Earlier thought"}, {id:"new", reasoning:"Current thought"}
  ] } as any);
  render(<SavedThinking cid="c" sid="s" responseId="r" variantId="old" />);
  expect(api.getResponse).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Thinking"));
  expect(await screen.findByText(/Earlier thought/)).toBeInTheDocument();
  expect(screen.queryByText(/Current thought/)).not.toBeInTheDocument();
});
