import { render, screen } from "@testing-library/react";
import { GreetingMarkdown } from "./GreetingMarkdown";

test("imageExtras renders per image with its src; absent by default", () => {
  const body = "a ![x](/api/img/one) b ![y](/api/img/two)";
  const { container, rerender } = render(<GreetingMarkdown>{body}</GreetingMarkdown>);
  expect(container.querySelectorAll("img").length).toBe(2);
  expect(container.querySelector(".img-extras")).toBeNull();

  rerender(
    <GreetingMarkdown imageExtras={(src) => <button>tag {src.split("/").pop()}</button>}>
      {body}
    </GreetingMarkdown>,
  );
  expect(screen.getByRole("button", { name: "tag one" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "tag two" })).toBeInTheDocument();
});
