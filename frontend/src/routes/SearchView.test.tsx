import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import SearchView, { hitTo, markTerms } from "./SearchView";
import { characterHref, charactersHref } from "../components/character/shared";
import type { EntityScope } from "../api/types";

vi.mock("../api/client", () => ({ api: { search: vi.fn() } }));
import { api } from "../api/client";

const W: EntityScope = { kind: "world", id: "realm" };

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
  expect(await screen.findByRole("link", { name: /the salt pact/i })).toBeInTheDocument();
  expect(api.search).toHaveBeenCalledWith(
    "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal));
  // The box is seeded from the URL, so the page is a link rather than a state
  // someone has to retype into.
  expect(screen.getByRole("searchbox", { name: /search the library/i })).toHaveValue("salt");
});

test("the matched terms are marked in the name and the snippet", async () => {
  (api.search as any).mockResolvedValue(result([hit()]));
  show();
  await screen.findByRole("link", { name: /the salt pact/i });
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
  expect(api.search).toHaveBeenCalledWith(
    "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal));
});

test("a hit says which world or campaign holds it", async () => {
  (api.search as any).mockResolvedValue(result([
    hit(),
    hit({ scope: "campaign", root: "the-long-run", root_name: "The Long Run" }),
  ]));
  show();
  const rows = await screen.findAllByRole("link", { name: /the salt pact/i });
  expect(within(rows[0]).getByText(/Lore · Realm/)).toBeInTheDocument();
  expect(within(rows[1]).getByText(/Lore · The Long Run · campaign/)).toBeInTheDocument();
});

test("following a hit opens the record it names", async () => {
  show();
  fireEvent.click(await screen.findByRole("link", { name: /the salt pact/i }));
  await waitFor(() => expect(screen.getByTestId("where"))
    .toHaveTextContent("/worlds/realm/lore/the-salt-pact"));
});

test("the kind column filters, and clicking the live filter clears it", async () => {
  (api.search as any).mockResolvedValue(result([
    hit(), hit({ kind: "scenes", scope: "campaign", root: "the-long-run", id: "001", name: "Salt" }),
  ]));
  show();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("button", { name: /^Lore/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "", kinds: ["lore"], mode: "keyword" }, expect.any(AbortSignal)));
  fireEvent.click(column.getByRole("button", { name: /^Lore/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal)));
});

test("the scope column narrows to worlds or campaigns", async () => {
  show();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("button", { name: /^Campaigns/ }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "campaign", kinds: [], mode: "keyword" }, expect.any(AbortSignal)));
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
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal)));
});

test("a failed search degrades to a message rather than a stuck spinner", async () => {
  (api.search as any).mockRejectedValue(new Error("nope"));
  show();
  expect(await screen.findByText(/could not be run/i)).toBeInTheDocument();
});

test("hitTo sends every kind of hit somewhere it can actually be read", () => {
  expect(hitTo(hit() as any)).toBe("/worlds/realm/lore/the-salt-pact");
  // A campaign's fork opens in that campaign's world view, never in the world
  // it forked from: they are two records with one id.
  expect(hitTo(hit({ scope: "campaign", root: "run" }) as any))
    .toBe("/campaigns/run/world/lore/the-salt-pact");
  expect(hitTo(hit({ kind: "characters", id: "seraphine", sub: "veiled" }) as any))
    .toBe("/worlds/realm/characters/seraphine?v=veiled");
  expect(hitTo(hit({ kind: "characters", id: "seraphine", sub: "" }) as any))
    .toBe("/worlds/realm/characters/seraphine");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "scenes", id: "001" }) as any))
    .toBe("/campaigns/run/scenes/001");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "plot" }) as any))
    .toBe("/campaigns/run/ledger");
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "campaign" }) as any))
    .toBe("/campaigns/run");
  // A dossier is filed under a character, so it opens the character.
  expect(hitTo(hit({ scope: "campaign", root: "run", kind: "dossier", id: "seraphine" }) as any))
    .toBe("/campaigns/run/world/characters/seraphine");
});

test("nothing in the app mints a ?section= link any more", () => {
  for (const href of [hitTo(hit() as any), charactersHref(W), characterHref(W, "mira")]) {
    expect(href).not.toContain("section=");
  }
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
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal)));
});

test("the result count is announced, not just shown", async () => {
  // It is the one thing on this page that changes without the reader moving
  // focus -- typing leaves focus in the box and the answer arrives elsewhere.
  show();
  await screen.findByRole("link", { name: /the salt pact/i });
  expect(screen.getByRole("status")).toHaveTextContent(/1 result/i);
});


test("a slow answer for an old query never lands on top of a newer one", async () => {
  // Type "sal", then "salt". The sweep for "sal" is still out when "salt"
  // settles, so the page waits for it rather than starting a second sweep
  // beside it -- and when it lands, its hits are dropped rather than shown
  // under the newer query's heading, and "salt" is asked for straight away.
  let releaseStale = (_: unknown) => {};
  (api.search as any).mockImplementationOnce(
    () => new Promise((res) => { releaseStale = res; }));
  (api.search as any).mockResolvedValue(
    result([hit({ id: "the-tide-table", name: "The Tide Table" })]));

  show("/search?q=sal");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByRole("searchbox", { name: /search the library/i }),
                   { target: { value: "salt" } });
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/q=salt$/));
  expect(api.search).toHaveBeenCalledTimes(1);

  releaseStale(result([hit()]));                       // the stale answer, late
  await screen.findByRole("link", { name: /the tide table/i });
  expect(api.search).toHaveBeenCalledTimes(2);
  expect(api.search).toHaveBeenLastCalledWith(
    "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal));
  expect(screen.queryByRole("link", { name: /the salt pact/i })).not.toBeInTheDocument();
});

test("typing on through a slow sweep asks once more, for the last query only", async () => {
  // Four settles while one sweep is out. Each used to start a sweep of its
  // own beside the others, all walking the whole store at once, with the one
  // the reader wanted finishing last. Now the page sends at most two: the one
  // already out, and the last thing typed.
  let release = (_: unknown) => {};
  (api.search as any).mockImplementationOnce(
    () => new Promise((res) => { release = res; }));
  (api.search as any).mockResolvedValue(result([hit({ id: "salt-marsh", name: "Salt Marsh" })],
                                                { q: "salt marsh", terms: ["salt", "marsh"] }));

  show("/search");
  const box = screen.getByRole("searchbox", { name: /search the library/i });
  for (const [typed, inUrl] of [["sa", "sa"], ["sal", "sal"], ["salt", "salt"],
                                ["salt marsh", "salt\\+marsh"]]) {
    fireEvent.change(box, { target: { value: typed } });
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(new RegExp(`q=${inUrl}$`)));
  }
  expect(api.search).toHaveBeenCalledTimes(1);

  release(result([hit({ id: "sandbar", name: "Sandbar" })], { q: "sa", terms: ["sa"] }));
  expect(await screen.findByRole("link", { name: /salt marsh/i })).toBeInTheDocument();
  expect(api.search).toHaveBeenCalledTimes(2);
  expect(api.search).toHaveBeenLastCalledWith(
    "salt marsh", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal));
  expect(screen.queryByRole("link", { name: /sandbar/i })).not.toBeInTheDocument();
});

test("typing away and back while a sweep is out keeps its answer", async () => {
  // "salt", then "salty", then "salt" again before the first sweep lands: the
  // answer on its way is the answer to the question now being asked.
  let release = (_: unknown) => {};
  (api.search as any).mockImplementationOnce(
    () => new Promise((res) => { release = res; }));

  show("/search?q=salt");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  const box = screen.getByRole("searchbox", { name: /search the library/i });
  fireEvent.change(box, { target: { value: "salty" } });
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/q=salty$/));
  fireEvent.change(box, { target: { value: "salt" } });
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/q=salt$/));

  release(result([hit()]));
  expect(await screen.findByRole("link", { name: /the salt pact/i })).toBeInTheDocument();
  expect(api.search).toHaveBeenCalledTimes(1);
});

test("a superseded search that fails is traded for the wanted one, not reported", async () => {
  let fail = (_: unknown) => {};
  (api.search as any).mockImplementationOnce(
    () => new Promise((_res, rej) => { fail = rej; }));

  show("/search?q=sal");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByRole("searchbox", { name: /search the library/i }),
                   { target: { value: "salt" } });
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/q=salt$/));

  fail(new Error("the sweep for sal fell over"));
  expect(await screen.findByRole("link", { name: /the salt pact/i })).toBeInTheDocument();
  expect(screen.queryByText(/could not be run/i)).not.toBeInTheDocument();
});

test("leaving the page aborts the search it left running", async () => {
  (api.search as any).mockImplementation(() => new Promise(() => {}));
  const { unmount } = show();
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  const signal = (api.search as any).mock.calls[0][2] as AbortSignal;
  expect(signal.aborted).toBe(false);
  unmount();
  expect(signal.aborted).toBe(true);
});

// ---- mode: keywords or meaning (#34) --------------------------------------

test("the mode is part of the query and lives in the URL like every other filter", async () => {
  show();
  await screen.findByRole("link", { name: /the salt pact/i });
  fireEvent.click(screen.getByRole("button", { name: /meaning/i }));
  await waitFor(() =>
    expect(api.search).toHaveBeenLastCalledWith(
      "salt", { scope: "", kinds: [], mode: "semantic" }, expect.any(AbortSignal)));
  expect(screen.getByTestId("where").textContent).toContain("mode=semantic");
});

test("switching modes does not queue behind the other mode's search", async () => {
  // A meaning search waits on an embeddings endpoint, not on the CPU the
  // queue is there to share, so a reader who gives up on one and switches to
  // keywords is answered now rather than once the endpoint lets go.
  (api.search as any).mockImplementationOnce(() => new Promise(() => {}));
  show("/search?q=salt&mode=semantic");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  const stuck = (api.search as any).mock.calls[0][2] as AbortSignal;

  fireEvent.click(screen.getByRole("button", { name: /^keywords/i }));
  expect(await screen.findByRole("link", { name: /the salt pact/i })).toBeInTheDocument();
  expect(api.search).toHaveBeenCalledTimes(2);
  expect(api.search).toHaveBeenLastCalledWith(
    "salt", { scope: "", kinds: [], mode: "keyword" }, expect.any(AbortSignal));
  expect(stuck.aborted).toBe(true);
});

test("an answer that fell back to keywords says so, and why", async () => {
  (api.search as any).mockResolvedValue(result([hit()], {
    mode: "keyword", requested_mode: "semantic",
    note: "Semantic search needs an embeddings connection and model.",
  }));
  show("/search?q=salt&mode=semantic");
  expect(await screen.findByText(/needs an embeddings connection/i)).toBeInTheDocument();
  // And the results are still there — a degraded answer is an answer.
  expect(screen.getByRole("link", { name: /the salt pact/i })).toBeInTheDocument();
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
  await screen.findByRole("link", { name: /the salt pact/i });
  expect(screen.queryByText(/of 100 passages/i)).not.toBeInTheDocument();
});

test("an empty library in semantic mode does not print a stray zero", async () => {
  // `result.corpus && …` is a number, and React renders a 0 rather than
  // skipping it. A library with nothing in it yet would have printed one.
  (api.search as any).mockResolvedValue(result([], {
    mode: "semantic", requested_mode: "semantic", note: "", terms: [],
    indexed: 0, corpus: 0,
  }));
  const { container } = show("/search?q=salt&mode=semantic");
  await screen.findByText(/nothing matches/i);
  // The stray renders as a bare text node among the page's blocks, which is
  // what this looks for — "0 results" is a legitimate string inside one.
  const stray = [...(container.querySelector(".page-wide")?.childNodes ?? [])]
    .filter((n) => n.nodeType === Node.TEXT_NODE && n.textContent?.trim());
  expect(stray.map((n) => n.textContent)).toEqual([]);
});

test("an empty semantic result does not explain the keyword rules", async () => {
  (api.search as any).mockResolvedValue(result([], {
    mode: "semantic", requested_mode: "semantic", note: "", terms: [],
    indexed: 10, corpus: 10,
  }));
  show("/search?q=salt&mode=semantic");
  await screen.findByText(/nothing matches/i);
  expect(screen.queryByText(/every term has to appear/i)).not.toBeInTheDocument();
});

test("meaning mode waits to be asked rather than searching as you type", async () => {
  // Every semantic query is a paid call to an embeddings endpoint. Firing one
  // per settled keystroke bills the reader for four answers they never see.
  show("/search?mode=semantic");
  const box = screen.getByRole("searchbox", { name: /search the library/i });
  fireEvent.change(box, { target: { value: "salt" } });
  await new Promise((r) => setTimeout(r, 400));
  expect(api.search).not.toHaveBeenCalled();
  fireEvent.submit(box.closest("form")!);
  await waitFor(() => expect(api.search).toHaveBeenCalledWith(
    "salt", { scope: "", kinds: [], mode: "semantic" }, expect.any(AbortSignal)));
});

test("a character hit names the version it matched, so two versions are two rows", async () => {
  // A tagline or a display name matches every version of a character, so the
  // rows are otherwise identical: same name, same kind, same world.
  (api.search as any).mockResolvedValue(result([
    hit({ kind: "characters", id: "seraphine", sub: "default", name: "Seraphine" }),
    hit({ kind: "characters", id: "seraphine", sub: "veiled", name: "Seraphine" }),
  ]));
  show();
  await screen.findAllByRole("link", { name: /seraphine/i });
  expect(screen.getByText(/veiled/)).toBeInTheDocument();
  expect(screen.getByText(/default/)).toBeInTheDocument();
});

test("meaning mode says the box is ahead of the results rather than looking stale", async () => {
  show("/search?q=salt&mode=semantic");
  await screen.findByRole("link", { name: /the salt pact/i });
  fireEvent.change(screen.getByRole("searchbox", { name: /search the library/i }),
                   { target: { value: "brine" } });
  expect(await screen.findByText(/press enter to search for “brine”/i)).toBeInTheDocument();
});

test("switching mode drops the other mode's results rather than showing them under it", async () => {
  // A semantic query can take a round trip and a warm run. Leaving the keyword
  // page on screen under an active "Meaning" row for that long presents one
  // ranking as the other's answer.
  show();
  await screen.findByRole("link", { name: /the salt pact/i });
  (api.search as any).mockReturnValue(new Promise(() => {}));   // never resolves
  fireEvent.click(screen.getByRole("button", { name: /meaning/i }));
  await waitFor(() =>
    expect(screen.queryByRole("link", { name: /the salt pact/i })).not.toBeInTheDocument());
  expect(screen.getByText(/reading the library/i)).toBeInTheDocument();
});

test("asking again re-runs the search, which is what the coverage line promises", async () => {
  // The partial-coverage line tells the reader "searching again reads more of
  // the library". Submitting an unchanged query left the URL untouched, so the
  // effect never refired and nothing was read — the page instructed an action
  // that did nothing.
  (api.search as any).mockResolvedValue(result([hit()], {
    mode: "semantic", requested_mode: "semantic", note: "", terms: [],
    indexed: 40, corpus: 100,
  }));
  show("/search?q=salt&mode=semantic");
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
  fireEvent.submit(screen.getByRole("searchbox", { name: /search the library/i })
    .closest("form")!);
  await waitFor(() => expect(api.search).toHaveBeenCalledTimes(2));
});
