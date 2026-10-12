import { MarkdownArtProvider } from "../markdown/MarkdownArtProvider";
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

test("greetings explicitly opt out even inside an enabled Markdown Art context", () => {
  render(<MarkdownArtProvider enabled contextKey="Realm">
    <GreetingMarkdown imageExtras={() => <button>Tag subjects</button>}>{"![Saltmarch](https://example.test/map.png)"}</GreetingMarkdown>
  </MarkdownArtProvider>);
  expect(screen.getByRole("button", { name: "Tag subjects" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Enlarge Saltmarch" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
});

test("greeting opt-out preserves linked image markup and has no enlargement control", () => {
  const view = render(<GreetingMarkdown>{"[![Saltmarch](https://example.test/map.png)](https://example.test/story)"}</GreetingMarkdown>);
  expect(view.container.querySelector("a img")).toHaveAttribute("src", "https://example.test/map.png");
  expect(screen.queryByRole("button", { name: "Enlarge Saltmarch" })).not.toBeInTheDocument();
});
