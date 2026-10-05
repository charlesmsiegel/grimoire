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

test("reference images render while code and raw HTML do not expose image controls", () => {
  const url = "https://example.test/art.png?variant=1&size=2";
  const { container } = render(<GreetingMarkdown imageExtras={(src) => <button>{src}</button>}>
    {`![Art][art]\n\n[art]: <${url}>\n\n![Again](${url})\n\n`
      + "`![Inline](https://example.test/inline.png)`\n\n"
      + "```markdown\n![Code](https://example.test/code.png)\n```\n\n"
      + '<img src="https://example.test/html.png">\n\n'
      + "[Link](https://example.test/link.png)"}
  </GreetingMarkdown>);
  expect([...container.querySelectorAll("img")].map((img) => img.getAttribute("src"))).toEqual([url, url]);
  expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([url, url]);
});

test.each([
  ["- item\n\n    ```markdown\n\n    ![Code](https://example.test/code.png)\n\n    ```", false],
  ["> ```markdown\n> ![Code](https://example.test/code.png)\n> ```", false],
  ["<div>\n\n![Art](https://example.test/code.png)\n\n</div>", true],
] as const)("nested code and HTML containers: %s", (body, visible) => {
  const { container } = render(<GreetingMarkdown>{body}</GreetingMarkdown>);
  expect(container.querySelectorAll("img")).toHaveLength(visible ? 1 : 0);
});
