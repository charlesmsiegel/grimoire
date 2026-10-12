import { useEffect } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { ArtNavigationProvider, useArtNavigation } from "../components/art/ArtNavigationProvider";
import { StreamingMarkdown } from "../components/play/StreamingMarkdown";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import Markdown from "react-markdown";
import { MarkdownArtOffset, MarkdownArtProvider } from "./MarkdownArtProvider";
import { markdownImageComponents } from "./MarkdownImage";
const source = "/api/worlds/Realm/image-collections/0123456789abcdef0123456789abcdef/image";
const members = [0, 1, 3].map((index) => source.replace(/\/image$/, "/members/" + index) + "?v=bytes");
function View({ text, contextKey = "Realm" }: { text: string; contextKey?: string }) {
  return <MarkdownArtProvider enabled contextKey={contextKey}><Markdown components={markdownImageComponents}>{text}</Markdown></MarkdownArtProvider>;
}
beforeEach(() => {
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ format: 2, members }) }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test("inline collection viewer starts at the current member and stepping never rerolls inline selection", async () => {
  const view = render(<View text={"![Saltmarch](" + source + ")"} />);
  const inline = await screen.findByAltText("Saltmarch");
  expect(inline).toHaveAttribute("src", members[1]);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[1]);
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Next image" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[2]);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(view.container.querySelector("img")).toHaveAttribute("src", members[1]);
  expect(fetch).toHaveBeenCalledOnce();
});
test("two occurrences of the same collection retain independent inline selections", async () => {
  const view = render(<View text={"![Mara](" + source + ") ![Seraphine](" + source + ")"} />);
  await screen.findByAltText("Mara");
  const second = await screen.findByAltText("Seraphine");
  fireEvent.click(view.container.querySelectorAll('button[aria-label="Next image"]')[0]);
  const firstSrc = view.container.querySelector('img[alt="Mara"]')?.getAttribute("src");
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Mara" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", firstSrc);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(second).toHaveAttribute("src", members[1]);
});
test("available members keep canonical indices after another member fails", async () => {
  render(<View text={"![Saltmarch](" + source + ")"} />);
  fireEvent.error(await screen.findByAltText("Saltmarch"));
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Next image" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[2]);
  expect(within(screen.getByRole("dialog")).getByText("Saltmarch / Image 4")).toBeInTheDocument();
  expect(screen.getByText("2 / 2")).toBeInTheDocument();
});
test("failed original stays selected with Retry rather than substituting another member", async () => {
  render(<View text={"![Saltmarch](" + source + ")"} />);
  await screen.findByAltText("Saltmarch");
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  fireEvent.error(within(screen.getByRole("dialog")).getByAltText("Saltmarch"));
  expect(screen.getByText("Original unavailable")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[1]);
  expect(screen.getByRole("button", { name: "Retry original" })).toBeEnabled();
});
test("manifest failure enlarges only the rendered fallback", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  render(<View text={"![Saltmarch](" + source + ")"} />);
  await screen.findByAltText("Saltmarch");
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", source);
  expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Next image" })).not.toBeInTheDocument();
});
test("remote art uses its browser URL with no local download, model, import, or proxy", () => {
  render(<View text="![Saltmarch](https://example.test/map.png)" />);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", "https://example.test/map.png");
  expect(screen.queryByRole("link", { name: "Download" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Open record|Manage|Reuse|Describe/ })).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
test("linked images have a separate enlargement control outside their link", () => {
  const view = render(<View text="[![Saltmarch](https://example.test/map.png)](https://example.test/story)" />);
  const enlarge = screen.getByRole("button", { name: "Enlarge Saltmarch" });
  expect(enlarge.closest("a")).toBeNull();
  expect(view.container.querySelector("a")).toHaveAttribute("href", "https://example.test/story");
  expect(view.container.querySelector("a img")).toHaveAttribute("src", "https://example.test/map.png");
  fireEvent.click(enlarge);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});
test("context change or true source disappearance closes the owned viewer", async () => {
  const view = render(<View text="![Saltmarch](https://example.test/map.png)" />);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<View contextKey="Mara" text="![Saltmarch](https://example.test/map.png)" />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<View contextKey="Mara" text="No image here." />);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("collection metadata rerender keeps the concrete selected member in an open viewer", async () => {
  const view = render(<View text={"![Saltmarch](" + source + ")"} />);
  await screen.findByAltText("Saltmarch");
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<View text={"![Realm](" + source + ")"} />);
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[1]);
});
test("linked collections place both selection and enlarge controls outside the anchor", async () => {
  const view = render(<View text={"[![Saltmarch](" + source + ")](https://example.test/story)"} />);
  await screen.findByAltText("Saltmarch");
  for (const button of view.container.querySelectorAll("button")) expect(button.closest("a")).toBeNull();
  expect(screen.getByRole("button", { name: "Enlarge Saltmarch" })).toBeInTheDocument();
});

test.each([
  ["Setext heading", "\n---"],
  ["late reference definition", "\n\n[quay]: /maps/saltmarch"],
])("stream %s preserves collection occurrence, selection, viewer, and manifest read", async (_name, suffix) => {
  const first = "Mara waited.\n\n![Saltmarch](" + source + ")";
  const view = render(<StreamingMarkdown text={first} />);
  await screen.findByAltText("Saltmarch");
  fireEvent.click(view.container.querySelector('button[aria-label="Next image"]')!);
  expect(view.container.querySelector("img")).toHaveAttribute("src", members[2]);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  vi.mocked(Math.random).mockReturnValue(0);
  view.rerender(<StreamingMarkdown text={first + suffix} />);
  await waitFor(() => expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[2]);
  expect(view.container.querySelector("img")).toHaveAttribute("src", members[2]);
  expect(fetch).toHaveBeenCalledOnce();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.getByRole("button", { name: "Enlarge Saltmarch" })).toHaveFocus();
});

test("hidden art handles before an occurrence do not change its identity when block parsing collapses", async () => {
  const first = "[[art:locations:Saltmarch:avatar]]\n\nMara waited.\n\n![Saltmarch](" + source + ")";
  const view = render(<StreamingMarkdown text={first} />);
  await screen.findByAltText("Saltmarch");
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<StreamingMarkdown text={first + "\n\n[quay]: /maps/saltmarch"} />);
  await waitFor(() => expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[1]);
  expect(fetch).toHaveBeenCalledOnce();
});

test("independent comparison parses preserve their own collection state under one owner", async () => {
  const text = "![Saltmarch](" + source + ")";
  function Comparison({ first }: { first: string }) {
    return <MarkdownArtProvider enabled contextKey="comparison">
      <div data-testid="world"><MarkdownArtOffset offset={0} documentKey="world/body">
        <Markdown components={markdownImageComponents}>{first}</Markdown>
      </MarkdownArtOffset></div>
      <div data-testid="campaign"><MarkdownArtOffset offset={0} documentKey="campaign/body">
        <Markdown components={markdownImageComponents}>{text}</Markdown>
      </MarkdownArtOffset></div>
    </MarkdownArtProvider>;
  }
  const view = render(<Comparison first={text} />);
  const world = within(screen.getByTestId("world")), campaign = within(screen.getByTestId("campaign"));
  await world.findByAltText("Saltmarch"); await campaign.findByAltText("Saltmarch");
  fireEvent.click(world.getByRole("button", { name: "Next image" }));
  fireEvent.click(world.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<Comparison first={text + "\n---"} />);
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", members[2]);
  expect(world.getByAltText("Saltmarch")).toHaveAttribute("src", members[2]);
  expect(campaign.getByAltText("Saltmarch")).toHaveAttribute("src", members[1]);
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(world.getByRole("button", { name: "Enlarge Saltmarch" })).toHaveFocus();
});

test("replacement focus belongs to the selected occurrence's provider", async () => {
  const text = "![Saltmarch](https://example.test/map.png)";
  function Twins({ second }: { second: string }) { return <><View text={text} /><View text={second} /></>; }
  const view = render(<Twins second={text} />);
  fireEvent.click(screen.getAllByRole("button", { name: "Enlarge Saltmarch" })[1]);
  view.rerender(<Twins second={text + "\n---"} />);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.getAllByRole("button", { name: "Enlarge Saltmarch" })[1]).toHaveFocus();
});

test("removing an inline occurrence closes its viewer with a visible explanation", async () => {
  const view = render(<View text="![Saltmarch](https://example.test/map.png)" />);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Saltmarch" }));
  view.rerender(<View text="Mara waited." />);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("status")).toHaveTextContent("This image is no longer available.");
  expect(screen.getByRole("region", { name: "Art document" })).toHaveFocus();
});

test.each([true, false])("Markdown viewer follows accepted Back and preserves refused Back (allowed: %s)", async allowed => {
  const NativeRequest = globalThis.Request;
  class RouteRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      const { signal, ...rest } = init ?? {};
      super(input, rest);
      if (signal) Object.defineProperty(this, "signal", { value: signal });
    }
  }
  vi.stubGlobal("Request", RouteRequest);
  let allowTransition = true;
  function Surface() {
    const navigation = useArtNavigation();
    useEffect(() => navigation.registerDraftGuard(() => allowTransition), [navigation]);
    return <View contextKey="Realm/Mara/default" text="![Mara](https://example.test/art.png)" />;
  }
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Surface /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/Realm/pcs/Mara", "/worlds/Realm/pcs/Mara?v=default"], initialIndex: 1 });
  render(<RouterProvider router={router} />);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge Mara" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  allowTransition = allowed;
  await act(async () => { await router.navigate(-1); });
  if (allowed) expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
  else expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  router.dispose();
});


test("an exhausted manifest never displays or enlarges a random fallback", async () => {
  const view = render(<View text={"![Saltmarch](" + source + ")"} />);
  await screen.findByAltText("Saltmarch");
  for (let index = 0; index < members.length; index += 1) {
    const image = view.container.querySelector("img")!;
    expect(members).toContain(image.getAttribute("src"));
    fireEvent.error(image);
  }
  expect(screen.getByText("Image collection unavailable")).toBeInTheDocument();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("button", { name: "Enlarge Saltmarch" })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledOnce();
});
