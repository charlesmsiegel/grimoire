import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import SearchView, { hitTo, markTerms } from "./SearchView";

vi.mock("../api/client", () => ({ api: { search: vi.fn() } }));
import { api } from "../api/client";

const hit = (over: Partial<Record<string, unknown>> = {}) => ({
  scope: "world", root: "realm", root_name: "Realm", kind: "lore",
  id: "the-salt-pact", sub: "", name: "The Salt Pact",
  snippet: "Debts written in salt are owed to the sea.", score: 11,
  ...over,
});

const result = (hits: ReturnType<typeof hit>[], over: Record<string, unknown> = {}) => ({
  q: "salt", terms: ["salt"], total: hits.length,
  facets: Object.fromEntries(hits.map((h) => [h.kind, hits.filter((x) => x.kind === h.kind).length])),
  scopes: Object.fromEntries(hits.map((h) => [h.scope, hits.filter((x) => x.scope === h.scope).length])),
  truncated: false, hits, ...over,
});

/** Where the router ended up, so a followed hit can be asserted on without
 *  mounting the page it leads to. */
function Where() {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

function show(entry = "/search?q=salt") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/search" element={<><SearchView /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.search as any).mockResolvedValue(result([hit()]));
});

test("a query in the URL is searched for and its hits are listed", async () => {
  show();
  // Queried by role, not by text: the matched term is wrapped in a <mark>, so
  // the name is three nodes rather than one.
  expect(await screen.findByRole("button", { name: /the salt pact/i })).toBeInTheDocument();
  expect(api.search).toHaveBeenCalledWith("salt", { scope: "", kinds: [], mode: "keyword" });
  // The box is seeded from the URL, so the page is a link rather than a state
  // someone has to retype into.
  expect(screen.getByRole("searchbox", { name: /search the library/i })).toHaveValue("salt");
});

test("the matched terms are marked in the name and the snippet", async () => {
  (api.search as any).mockResolvedValue(result([hit()]));
  show();
  await screen.findByRole("button", { name: /the salt pact/i });
  const marks = document.querySelectorAll(".search-hit mark");
  expect(marks.length).toBe(2);          // once in the name, once in the snippet
  expect([...marks].every((m) => m.textContent?.toLowerCase() === "salt")).toBe(true);
});

test("an empty query asks nothing and says what the page covers", async () => {
  show("/search");
  expect(await screen.findByText(/every world and campaign in the library/i)).toBeInTheDocument();
  expect(api.search).not.toHaveBeenCalled();
});

test("typing settles before it searches, and only the settled query is asked", async () => {
  show("/search");
  const box = screen.getByRole("searchbox", { name: /search the library/i });
  fireEvent.change(box, { target: { value: "sal" } });
  fireEvent.change(box, { target: { value: "salt" } });
  // Nothing has gone out yet: every query walks the whole store, so a request
  // per keystroke would put four in flight for a five-letter word.
  expect(api.search).not.toHaveBeenCalled();
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  expect(api.search).toHaveBeenCalledWith("salt", { scope: "", kinds: [], mode: "keyword" });
});

test("a hit says which world or campaign holds it", async () => {
  (api.search as any).mockResolvedValue(result([
    hit(),
    hit({ scope: "campaign", root: "the-long-run", root_name: "The Long Run" }),
  ]));
  show();
  const rows = await screen.findAllByRole("button", { name: /the salt pact/i });
  expect(within(rows[0]).getByText(/Lore · Realm/)).toBeInTheDocument();
  expect(within(rows[1]).getByText(/Lore · The Long Run · campaign/)).toBeInTheDocument();
});

test("following a hit opens the record it names", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: /the salt pact/i }));
  await waitFor(() => expect(screen.getByTestId("where"))
    .toHaveTextContent("/worlds/realm?section=lore&id=the-salt-pact"));
});

test("the kind column filters, and clicking the live filter clears it", async () => {
  (api.search as any).mockResolvedValue(result([
    hit(), hit({ kind: "scenes", scope: "campaign", root: "the-long-run", id: "001", name: "Salt" }),
  ]));
  show();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("button", { name: /^Lore/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "", kinds: ["lore"], mode: "keyword" }));
  fireEvent.click(column.getByRole("button", { name: /^Lore/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "", kinds: [], mode: "keyword" }));
});

test("the scope column narrows to worlds or campaigns", async () => {
  show();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("button", { name: /^Campaigns/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "campaign", kinds: [], mode: "keyword" }));
});

test("only the kinds this query found are offered as filters", async () => {
  show();
  const column = within(await screen.findByRole("complementary"));
  await waitFor(() => expect(column.queryByRole("button", { name: /^Lore/ })).toBeInTheDocument());
  expect(column.queryByRole("button", { name: /^Dossiers/ })).not.toBeInTheDocument();
});

test("nothing matching says so, and offers the way out of the filter", async () => {
  (api.search as any).mockResolvedValue(result([], { total: 0, facets: {}, scopes: {} }));
  show("/search?q=salt&kind=lore");
  expect(await screen.findByText(/nothing matches/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /search everywhere instead/i }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "", kinds: [], mode: "keyword" }));
});

test("a failed search degrades to a message rather than a stuck spinner", async () => {
  (api.search as any).mockRejectedValue(new Error("nope"));
  show();
  expect(await screen.findByText(/could not be run/i)).toBeInTheDocument();
});

test("hitTo sends every kind of hit somewhere it can actually be read", () => {
  expect(hitTo(hit() as any)).toBe("/worlds/realm?section=lore&id=the-salt-pact");
  // A campaign's fork opens in that campaign's world view, never in the world
  // it forked from: they are two records with one id.
  expect(hitTo(hit({ scope: "campaign", root: "run" }) as any))
    .toBe("/campaigns/run/world?section=lore&id=the-salt-pact");
  expect(hitTo(hit({ kind: "characters", id: "seraphine", sub: "veiled" }) as any))
    .toBe("/worlds/realm?section=characters&id=seraphine&v=veiled");
  expect(hitTo(hit({ kind: "characters", id: "seraphine", sub: "" }) as any))
    .toBe("/worlds/realm?section=characters&id=seraphine");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "scenes", id: "001" }) as any))
    .toBe("/campaigns/run/scenes/001");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "plot" }) as any))
    .toBe("/campaigns/run/ledger");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "campaign" }) as any))
    .toBe("/campaigns/run");
  // A dossier is filed under a character, so it opens the character.
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "dossier", id: "seraphine" }) as any))
    .toBe("/campaigns/run/world?section=characters&id=seraphine");
});

test("markTerms prefers the longer of two overlapping terms", () => {
  const parts = markTerms("the salt pact endures", ["salt", "salt pact"]);
  const marked = parts.filter((p) => typeof p === "object") as any[];
  expect(marked).toHaveLength(1);
  expect(marked[0].props.children).toBe("salt pact");
});

test("markTerms leaves text alone when there is nothing to mark", () => {
  expect(markTerms("plain", [])).toEqual(["plain"]);
});


test("the kind still filtering stays in the column when its count drops to 0", async () => {
  // Otherwise: change the query with a kind filter on, the row that applied it
  // vanishes from the column, and the page reads "Nothing matches" with
  // nothing on screen saying a filter is still in force.
  (api.search as any).mockResolvedValue(result([], { total: 0, facets: {}, scopes: {} }));
  show("/search?q=salt&kind=lore");
  const column = within(await screen.findByRole("complementary"));
  await waitFor(() => expect(column.getByRole("button", { name: /^Lore/ })).toHaveClass("active"));
  fireEvent.click(column.getByRole("button", { name: /^Lore/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "", kinds: [], mode: "keyword" }));
});

test("the result count is announced, not just shown", async () => {
  // It is the one thing on this page that changes without the reader moving
  // focus -- typing leaves focus in the box and the answer arrives elsewhere.
  show();
  await screen.findByRole("button", { name: /the salt pact/i });
  expect(screen.getByRole("status")).toHaveTextContent(/1 result/i);
});


test("a slow answer for an old query never lands on top of a newer one", async () => {
  // Type "sal", then "salt". If the sweep for "sal" settles last -- it walks
  // the whole store, so it easily can -- the page would show its hits under
  // the newer query's heading, with no way to tell.
  let releaseStale = (_: unknown) => {};
  (api.search as any).mockImplementationOnce(
    () => new Promise((res) => { releaseStale = res; }));
  (api.search as any).mockResolvedValue(
    result([hit({ id: "the-tide-table", name: "The Tide Table" })]));

  show("/search?q=sal");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByRole("searchbox", { name: /search the library/i }),
                   { target: { value: "salt" } });
  await screen.findByRole("button", { name: /the tide table/i });

  releaseStale(result([hit()]));                       // the stale answer, late
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button", { name: /the salt pact/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /the tide table/i })).toBeInTheDocument();
});

// ---- mode: keywords or meaning (#34) --------------------------------------

test("the mode is part of the query and lives in the URL like every other filter", async () => {
  show();
  await screen.findByRole("button", { name: /the salt pact/i });
  fireEvent.click(screen.getByRole("button", { name: /meaning/i }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith("salt", { scope: "", kinds: [], mode: "semantic" }));
  expect(screen.getByTestId("where").textContent).toContain("mode=semantic");
});

test("an answer that fell back to keywords says so, and why", async () => {
  (api.search as any).mockResolvedValue(result([hit()], {
    mode: "keyword", requested_mode: "semantic",
    note: "Semantic search needs an embeddings connection and model.",
  }));
  show("/search?q=salt&mode=semantic");
  expect(await screen.findByText(/needs an embeddings connection/i)).toBeInTheDocument();
  // And the results are still there — a degraded answer is an answer.
  expect(screen.getByRole("button", { name: /the salt pact/i })).toBeInTheDocument();
});

test("a semantic answer says how much of the library has been indexed", async () => {
  (api.search as any).mockResolvedValue(result([hit()], {
    mode: "semantic", requested_mode: "semantic", note: "", terms: [],
    indexed: 40, corpus: 100,
  }));
  show("/search?q=salt&mode=semantic");
  expect(await screen.findByText(/40 of 100/i)).toBeInTheDocument();
});

test("a fully indexed semantic answer does not nag about indexing", async () => {
  (api.search as any).mockResolvedValue(result([hit()], {
    mode: "semantic", requested_mode: "semantic", note: "", terms: [],
    indexed: 100, corpus: 100,
  }));
  show("/search?q=salt&mode=semantic");
  await screen.findByRole("button", { name: /the salt pact/i });
  expect(screen.queryByText(/of 100 passages/i)).not.toBeInTheDocument();
});
