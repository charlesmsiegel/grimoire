import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GreetingMarkdown } from "../components/GreetingMarkdown";
import { StreamingMarkdown } from "../components/play/StreamingMarkdown";
import { MarkdownImage } from "./MarkdownImage";

const source = "/api/worlds/realm/image-collections/0123456789abcdef0123456789abcdef/image";
const members = ["a", "b", "c"].map((c) => `/api/worlds/realm/images/collection-image-${c.repeat(64)}?v=1`);
function response(list = members) {
  return { ok: true, json: async () => ({ format: 1, id: "0123456789abcdef0123456789abcdef", members: list }) };
}

beforeEach(() => {
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response()));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("random start, wrapping, reroll, and parent rerenders retain the selected image", async () => {
  const { rerender } = render(<MarkdownImage src={source} alt="Scene" />);
  const image = await screen.findByAltText("Scene");
  expect(image).toHaveAttribute("src", members[1]);
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(image).toHaveAttribute("src", members[2]);
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(image).toHaveAttribute("src", members[0]);
  fireEvent.click(screen.getByRole("button", { name: "Previous image" }));
  expect(image).toHaveAttribute("src", members[2]);
  fireEvent.click(screen.getByRole("button", { name: "Reroll image" }));
  expect(image).toHaveAttribute("src", members[1]);
  rerender(<MarkdownImage src={source} alt="New description" />);
  expect(screen.getByAltText("New description")).toHaveAttribute("src", members[1]);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("greeting extras stay stable and receive the selected member URL", async () => {
  const extras = (url: string) => <span data-testid="extra">{url}</span>;
  const text = `![Scene](${source})`;
  const { rerender } = render(<GreetingMarkdown imageExtras={extras}>{text}</GreetingMarkdown>);
  await screen.findByAltText("Scene");
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  rerender(<GreetingMarkdown imageExtras={extras}>{text + " more text"}</GreetingMarkdown>);
  expect(screen.getByAltText("Scene")).toHaveAttribute("src", members[2]);
  expect(screen.getByTestId("extra")).toHaveTextContent(members[2]);
});

test("streaming closure of an image block preserves its selection", async () => {
  const text = `![Scene](${source})`;
  const { rerender } = render(<StreamingMarkdown text={text} />);
  await screen.findByAltText("Scene");
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  rerender(<StreamingMarkdown text={text + "\n\nMara waited."} />);
  expect(screen.getByAltText("Scene")).toHaveAttribute("src", members[2]);
});

test("missing members are skipped and a single remaining image has no controls", async () => {
  render(<MarkdownImage src={source} alt="Scene" />);
  const image = await screen.findByAltText("Scene");
  fireEvent.error(image);
  expect(image).toHaveAttribute("src", members[0]);
  fireEvent.error(image);
  expect(image).toHaveAttribute("src", members[2]);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("manifest failure falls back only to the local image endpoint", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  render(<MarkdownImage src={source} alt="Scene" />);
  const image = await screen.findByAltText("Scene");
  expect(image).toHaveAttribute("src", source);
  fireEvent.error(image);
  expect(screen.getByText("Image collection unavailable")).toBeInTheDocument();
});

test("a remote member in a manifest is rejected rather than fetched", async () => {
  vi.mocked(fetch).mockResolvedValue(response(["https://example.test/image.png"]) as Response);
  render(<MarkdownImage src={source} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", source);
});

const cid = "0123456789abcdef0123456789abcdef";
const member2 = (n: number, query = "?v=" + "f".repeat(64)) =>
  `/api/worlds/realm/image-collections/${cid}/members/${n}${query}`;
function body(doc: unknown) { return { ok: true, json: async () => doc } as Response; }

test("accepts a format 2 collection of member URLs", async () => {
  const list = [member2(0), member2(1), member2(3)];
  vi.mocked(fetch).mockResolvedValue(body({ format: 2, members: list }));
  render(<MarkdownImage src={source} alt="Scene" />);
  const image = await screen.findByAltText("Scene");
  expect(image).toHaveAttribute("src", list[1]);
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(image).toHaveAttribute("src", list[2]);
  expect(screen.getByText("Image 3 of 3")).toBeInTheDocument();
});

test("accepts a format 2 member URL without a version query", async () => {
  vi.mocked(fetch).mockResolvedValue(body({ format: 2, members: [member2(0, "")] }));
  render(<MarkdownImage src={source} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", member2(0, ""));
});

test.each([
  ["another world", `/api/worlds/other/image-collections/${cid}/members/0?v=1`],
  ["another collection", `/api/worlds/realm/image-collections/${"b".repeat(32)}/members/0?v=1`],
  ["a library URL", members[0]],
  ["a non-numeric index", member2(0).replace("/0?", "/x?")],
  ["a nested path", member2(0).replace("/0?", "/0/1?")],
  ["a remote host", "https://example.test" + member2(0)],
])("refuses a format 2 member URL from %s", async (_name, url) => {
  vi.mocked(fetch).mockResolvedValue(body({ format: 2, members: [url] }));
  render(<MarkdownImage src={source} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", source);
});

test("refuses a format 1 collection carrying member URLs", async () => {
  vi.mocked(fetch).mockResolvedValue(body({ format: 1, id: cid, members: [member2(0)] }));
  render(<MarkdownImage src={source} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", source);
});

test("refuses an unknown format", async () => {
  vi.mocked(fetch).mockResolvedValue(body({ format: 3, members: [member2(0)] }));
  render(<MarkdownImage src={source} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", source);
});

test("ordinary images need no manifest request", () => {
  render(<MarkdownImage src="/ordinary.png" alt="Ordinary" />);
  expect(screen.getByAltText("Ordinary")).toHaveAttribute("src", "/ordinary.png");
  expect(fetch).not.toHaveBeenCalled();
});

test("an old fetch cannot change a new source", async () => {
  let resolveFirst!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }));
  const { rerender } = render(<MarkdownImage src={source} alt="Scene" />);
  const nextSource = source.replace("0123456789abcdef0123456789abcdef", "a".repeat(32));
  vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ format: 1, id: "a".repeat(32), members }) } as Response);
  rerender(<MarkdownImage src={nextSource} alt="Scene" />);
  expect(await screen.findByAltText("Scene")).toHaveAttribute("src", members[1]);
  resolveFirst(response([members[0]]) as Response);
  await waitFor(() => expect(screen.getByAltText("Scene")).toHaveAttribute("src", members[1]));
});


test("two occurrences keep separate selections", async () => {
  render(<><div data-testid="first"><MarkdownImage src={source} alt="First" /></div>
    <div data-testid="second"><MarkdownImage src={source} alt="Second" /></div></>);
  const first = await screen.findByAltText("First");
  const second = await screen.findByAltText("Second");
  expect(first).toHaveAttribute("src", members[1]);
  expect(second).toHaveAttribute("src", members[1]);
  fireEvent.click(within(screen.getByTestId("first")).getByRole("button", { name: "Next image" }));
  expect(first).toHaveAttribute("src", members[2]);
  expect(second).toHaveAttribute("src", members[1]);
});
