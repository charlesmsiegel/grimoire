import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type SearchHit, type SearchMode, type SearchResult } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";

/** How the kinds are grouped in the filter column, in the order they are
 *  offered. Content before facts, because a reader who types a name is looking
 *  for the record; a reader hunting a fact usually knows they are. The labels
 *  are the ones the rest of the app uses for the same records. */
const GROUPS: { group: string; kinds: { key: string; label: string }[] }[] = [
  { group: "Who", kinds: [
    { key: "characters", label: "Characters" },
    { key: "pcs", label: "PCs" },
    { key: "creatures", label: "Creatures" },
    { key: "groups", label: "Groups" },
  ] },
  { group: "Where & what", kinds: [
    { key: "locations", label: "Locations" },
    { key: "items", label: "Items" },
  ] },
  { group: "Writing", kinds: [
    { key: "lore", label: "Lore" },
    { key: "greetings", label: "Greetings" },
    { key: "scenes", label: "Scenes" },
    // Not "Worlds"/"Campaigns": those are the SCOPE rows at the top of this
    // column, and two rows reading the same word would be two different
    // filters wearing one name. These are the meta files — a world's premise
    // and a campaign's pitch — which are records like any other.
    { key: "world", label: "World overviews" },
    { key: "campaign", label: "Campaign pitches" },
  ] },
  { group: "What play established", kinds: [
    { key: "chronicle", label: "Chronicle" },
    { key: "timeline", label: "Timeline" },
    { key: "plot", label: "Threads" },
    { key: "facts", label: "Standing facts" },
    { key: "relationships", label: "Relationships" },
    { key: "state", label: "Character state" },
    { key: "dossier", label: "Dossiers" },
  ] },
];

const LABELS: Record<string, string> = Object.fromEntries(
  GROUPS.flatMap((g) => g.kinds.map((k) => [k.key, k.label])),
);

/** The kinds that live inside a world/campaign's record index, and the section
 *  of that page each one opens. Everything else has its own destination (a
 *  scene, the ledger, the campaign itself) and is handled in `hitTo`. */
const INDEX_SECTIONS = new Set([
  "characters", "pcs", "creatures", "groups", "locations", "items", "lore", "greetings",
]);

/** Facts have no page of their own — they are rows on the campaign's ledger,
 *  which is where following one has to land. */
const LEDGER_KINDS = new Set(["chronicle", "timeline", "plot", "facts", "relationships"]);

/** Where clicking a hit goes.
 *
 *  A world record and a campaign's fork of it are two different records with
 *  the same id, so the scope decides the page as much as the kind does: the
 *  campaign's copy opens in that campaign's world view, never in the world it
 *  forked from. `state` and `dossier` are filed under a character and open the
 *  character, since that is the record they describe. */
export function hitTo(hit: SearchHit): string {
  const base = hit.scope === "world" ? `/worlds/${hit.root}` : `/campaigns/${hit.root}/world`;
  if (hit.kind === "world") return `/worlds/${hit.root}`;
  if (hit.kind === "campaign") return `/campaigns/${hit.root}`;
  if (hit.kind === "scenes") return `/campaigns/${hit.root}/scenes/${hit.id}`;
  if (LEDGER_KINDS.has(hit.kind)) return `/campaigns/${hit.root}/ledger`;
  const section = hit.kind === "state" || hit.kind === "dossier" ? "characters" : hit.kind;
  if (!INDEX_SECTIONS.has(section)) return base;
  const params = new URLSearchParams({ section, id: hit.id });
  // A card version only means anything for a character, and only when the hit
  // named one: an empty `v` would ask the editor to open a version called "".
  if (section === "characters" && hit.sub) params.set("v", hit.sub);
  return `${base}?${params}`;
}

/** The snippet with every matched term wrapped in a `<mark>`.
 *
 *  Terms come from the server, which is also what decided which of them had to
 *  be present — a second splitting rule on this side would highlight a
 *  different set of words than the one that produced the hit the first time
 *  either learned a new operator. Matching is longest-term-first so an
 *  overlapping pair ("salt", "salt pact") marks the phrase rather than leaving
 *  half of it bare.
 *
 *  One case the server can match and this cannot: it compares with Python's
 *  `casefold`, which maps "ß" to "ss", and JavaScript has no equivalent —
 *  `toLowerCase` leaves "ß" alone. So a record found by searching "strasse"
 *  is listed with nothing marked in it. A missing highlight on a hit that is
 *  correctly there is the right way for that difference to land. */
export function markTerms(text: string, terms: string[]): ReactNode[] {
  const wanted = [...terms].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!wanted.length) return [text];
  const out: ReactNode[] = [];
  const low = text.toLowerCase();
  let at = 0;
  let key = 0;
  while (at < text.length) {
    let hitAt = -1;
    let hitLen = 0;
    for (const term of wanted) {
      const found = low.indexOf(term, at);
      // Earliest wins; on a tie the longer one does, which is what `wanted`'s
      // ordering settles for two terms starting at the same character.
      if (found >= 0 && (hitAt < 0 || found < hitAt)) { hitAt = found; hitLen = term.length; }
    }
    if (hitAt < 0) { out.push(text.slice(at)); break; }
    if (hitAt > at) out.push(text.slice(at, hitAt));
    out.push(<mark key={key++}>{text.slice(hitAt, hitAt + hitLen)}</mark>);
    at = hitAt + hitLen;
  }
  return out;
}

const EMPTY: SearchResult = {
  q: "", terms: [], total: 0, facets: {}, scopes: {}, truncated: false, hits: [],
};

/** The two rankings, and what each one is for in a reader's words.
 *
 *  Not a "search harder" switch: keywords find the record that says the word,
 *  meaning finds the record that is about it and never uses it. Which is why
 *  both stay on offer rather than one superseding the other. */
const MODES: { key: SearchMode; label: string; hint: string }[] = [
  { key: "keyword", label: "Keywords", hint: "Every term has to appear" },
  // The hint carries the "press Enter" because meaning mode deliberately does
  // not search as you type — see the settle effect. A control that behaves
  // differently from its neighbour has to say so where the choice is made.
  { key: "semantic", label: "Meaning",
    hint: "Close in sense, not in wording · press Enter to search" },
];

/** Kinds whose `sub` names something a reader can tell apart at a glance: a
 *  card version, a persona version. Everything else's `sub` is machinery — a
 *  timeline line number, a relationship's side — and belongs nowhere near a
 *  result row. Without this, a character found by its tagline or its display
 *  name returns one identical-looking row per version. */
const VERSIONED = new Set(["characters", "pcs"]);

function isMode(value: string): value is SearchMode {
  return MODES.some((m) => m.key === value);
}

/** How long typing has to stop before the sweep runs. Every query walks the
 *  whole store, so a request per keystroke would have four of them in flight
 *  for a five-letter word — and the answer to the first four is never shown. */
const SETTLE_MS = 250;

/** The library's Ctrl-F (#33): one box over every world and campaign, content
 *  and facts alike.
 *
 *  The query lives in the URL rather than in state, so a result page is a link
 *  — shareable, bookmarkable, and reachable with the back button after
 *  following a hit, which is the single most common thing to want here. */
export default function SearchView() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const q = params.get("q") ?? "";
  const scope = params.get("scope") ?? "";
  const kind = params.get("kind") ?? "";
  // A mode nobody offers reads as the default rather than as an error: the
  // URL is hand-editable and shareable, and a typo in it should still search.
  const rawMode = params.get("mode") ?? "";
  const mode: SearchMode = isMode(rawMode) ? rawMode : "keyword";

  // What is in the box, which is not the same thing as what has been searched
  // for: the box leads the URL by up to `SETTLE_MS`, and the URL leads the
  // results by a round trip.
  const [draft, setDraft] = useState(q);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [failed, setFailed] = useState(false);
  // Submitting an unchanged query leaves the URL identical, so nothing in the
  // fetch effect's deps moves and no request goes out. That is fine for
  // keywords, where a second identical sweep would return the identical page —
  // and wrong for meaning, where the coverage line tells the reader that
  // searching again reads more of the library. This counter is what makes
  // "ask again" mean something.
  const [asked, setAsked] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // The URL is the source of truth, so a hit followed and then backed out of
  // restores the box as well as the results.
  useEffect(() => { setDraft(q); }, [q]);

  /** Rewrite the query string, always resetting nothing but what changed.
   *  `replace` for the typing path so the history stack holds the searches
   *  someone deliberately ran, not every prefix of them. */
  const setQuery = useCallback((next: Record<string, string>, replace: boolean) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      for (const [key, value] of Object.entries(next)) {
        if (value) out.set(key, value); else out.delete(key);
      }
      return out;
    }, { replace });
  }, [setParams]);

  // Keyword search runs on settled typing; meaning search waits to be asked.
  //
  // The difference is money, not latency. A keyword sweep is local disk, so a
  // wasted one costs nothing anybody pays for. Every semantic query embeds the
  // query against a metered endpoint and warms up to `WARM_LIMIT` passages
  // behind it — so search-as-you-type bills the reader for four answers they
  // never see on the way to spelling a five-letter word, and the responses to
  // three of them are dropped on arrival by design.
  useEffect(() => {
    if (mode === "semantic" || draft === q) return;
    const timer = window.setTimeout(() => setQuery({ q: draft }, true), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [draft, q, mode, setQuery]);

  // A mode change drops what the other mode answered, rather than leaving it
  // on screen under the new mode's active row. The other filters resolve
  // within a debounce; a semantic query can take a round trip and a warm run,
  // which is long enough for one ranking to read as the other's answer.
  useEffect(() => { setResult(null); }, [mode]);

  useEffect(() => {
    if (!q.trim()) { setResult(null); setFailed(false); return; }
    // Superseded responses are dropped rather than raced: a slow sweep for
    // "sal" must not land on top of the answer for "salt".
    let live = true;
    setFailed(false);
    api.search(q, { scope, kinds: kind ? [kind] : [], mode })
      .then((r) => { if (live) setResult(r); })
      .catch(() => { if (live) { setResult(null); setFailed(true); } });
    return () => { live = false; };
  }, [q, scope, kind, mode, asked]);

  const facets = result?.facets ?? {};
  const scopes = result?.scopes ?? {};

  /** Only the kinds this query actually found — plus whichever one is
   *  currently filtering, however few it found.
   *
   *  A column listing all eighteen every time would be a vocabulary lesson;
   *  listing the ones with hits makes the shape of the answer readable before
   *  any of it is read. But dropping the ACTIVE row when its count reaches zero
   *  is a trap: change the query with a kind filter still on and the filter
   *  vanishes from the column while still being applied, so the page reads
   *  "Nothing matches" with nothing on screen saying why. The active row stays,
   *  showing its 0, and is the control that clears itself. */
  const groups = useMemo(
    () => GROUPS
      .map((g) => ({ group: g.group, kinds: g.kinds.filter((k) => facets[k.key] || k.key === kind) }))
      .filter((g) => g.kinds.length > 0),
    [facets, kind],
  );

  const hits = result?.hits ?? [];
  const shown = result ?? EMPTY;

  const column = (
    <>
      <div className="search-ident">
        <div className="eyebrow">Across the whole library</div>
        <h2 className="search-ident-name">Search</h2>
      </div>
      <ColumnSection label="How">
        {MODES.map((m) => (
          <button key={m.key}
                  className={"column-row" + (mode === m.key ? " active" : "")}
                  onClick={() => setQuery({ mode: m.key === "keyword" ? "" : m.key }, false)}>
            <span className="column-row-label">{m.label}</span>
          </button>
        ))}
        <p className="field-hint search-mode-hint">
          {MODES.find((m) => m.key === mode)?.hint}
        </p>
      </ColumnSection>
      <ColumnSection label="Where" count={result ? shown.total : undefined}>
        {[{ key: "", label: "Everywhere" },
          { key: "world", label: "Worlds" },
          { key: "campaign", label: "Campaigns" }].map((s) => (
          <button key={s.key || "all"}
                  className={"column-row" + (scope === s.key ? " active" : "")}
                  onClick={() => setQuery({ scope: s.key }, false)}>
            <span className="column-row-label">{s.label}</span>
            <span className="column-row-count">
              {s.key ? (scopes[s.key] ?? 0) : (result ? shown.total : "—")}
            </span>
          </button>
        ))}
      </ColumnSection>
      {groups.map((g) => (
        <ColumnSection key={g.group} label={g.group}>
          {g.kinds.map((k) => (
            <button key={k.key}
                    className={"column-row" + (kind === k.key ? " active" : "")}
                    // Clicking the active kind clears it: the row is the
                    // filter's only control, so it has to be able to undo
                    // itself without a second one beside it.
                    onClick={() => setQuery({ kind: kind === k.key ? "" : k.key }, false)}>
              <span className="column-row-label">{k.label}</span>
              <span className="column-row-count">{facets[k.key] ?? 0}</span>
            </button>
          ))}
        </ColumnSection>
      ))}
    </>
  );

  const footer = kind || scope ? (
    <button className="column-link" onClick={() => setQuery({ scope: "", kind: "" }, false)}>
      Clear filters <span aria-hidden>×</span>
    </button>
  ) : undefined;

  // What the reader needs to know about the answer itself rather than about
  // the records in it: that meaning-search was asked for and could not run, or
  // that it ran against a library it has not finished reading. Both are stated
  // rather than hidden — a page ranked on a tenth of the corpus that says
  // nothing about it is a page that quietly means less than it looks like.
  const answered = result?.mode ?? "keyword";
  const fellBack = result !== null && result.requested_mode === "semantic"
    && answered !== "semantic";
  // Coerced, not left as `corpus && …`: that expression is a NUMBER, and React
  // renders a 0 where it skips a false. An empty library printed a bare zero
  // into the page.
  const partial = !!(answered === "semantic" && result?.corpus
    && (result.indexed ?? 0) < result.corpus);

  return (
    <PageShell column={column} footer={footer} columnLabel="Search filters">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow" role="status" aria-live="polite">
              {result
                ? `${shown.total} ${shown.total === 1 ? "result" : "results"}`
                  + (shown.truncated ? ` · showing the first ${hits.length}` : "")
                : "Content and facts · every world, every campaign"}
            </div>
            <h1 className="screen-title">{q.trim() ? `“${q.trim()}”` : "Search"}</h1>
          </div>
        </div>

        <form className="search-box" role="search"
              onSubmit={(e) => {
                e.preventDefault();
                setQuery({ q: draft }, false);
                setAsked((n) => n + 1);
              }}>
          <input ref={inputRef} type="search" value={draft} aria-label="Search the library"
                 placeholder="a name, a phrase, &quot;an exact quote&quot;"
                 onChange={(e) => setDraft(e.target.value)} />
        </form>

        {!q.trim() && (
          <p className="empty-state">
            <span className="empty-what">
              Every world and campaign in the library, searched at once — lore and
              locations, character cards, greetings, scene transcripts, and everything
              play has established: the chronicle, the timeline, threads, standing facts,
              relationships and dossiers.
            </span>{" "}
            {mode === "semantic"
              ? "Ranked by what a passage means rather than by the words in it, so a "
                + "description of a thing finds it without naming it."
              : "Terms are ANDed; wrap a phrase in quotes to match it whole."}
          </p>
        )}

        {fellBack && (
          <p className="empty-state">
            <span className="empty-what">Answered with keywords.</span>{" "}
            {result?.note} <Link to="/config">Configuration →</Link>
          </p>
        )}

        {partial && (
          <p className="ledger-lead">
            Indexed {result?.indexed} of {result?.corpus} passages so far. Searching
            again reads more of the library; until then this ranks what has been read.
          </p>
        )}

        {failed && (
          <p className="empty-state">
            <span className="empty-what">That search could not be run.</span>{" "}
            Check the filters, or try again.
          </p>
        )}

        {/* Meaning mode does not search as you type, so the box can sit ahead
            of the results indefinitely rather than for one debounce. Saying so
            is the difference between "waiting on me" and "stale". */}
        {mode === "semantic" && draft.trim() && draft !== q && (
          <p className="column-empty">Press Enter to search for “{draft.trim()}”.</p>
        )}

        {q.trim() && !failed && result === null && (
          <p className="column-empty">Reading the library…</p>
        )}

        {result !== null && hits.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">Nothing matches “{q.trim()}”.</span>{" "}
            {kind || scope
              ? <button className="link-button"
                        onClick={() => setQuery({ scope: "", kind: "" }, false)}>
                  Search everywhere instead →
                </button>
              : mode === "semantic"
                ? "Nothing in the library reads as close enough to that."
                : "Every term has to appear; a quoted phrase has to appear whole."}
          </p>
        )}

        {hits.length > 0 && (
          <ul className="search-results">
            {hits.map((hit) => (
              <li key={`${hit.scope}/${hit.root}/${hit.kind}/${hit.id}/${hit.sub}`}>
                <button className="search-hit" onClick={() => navigate(hitTo(hit))}>
                  <span className="search-hit-head">
                    <span className="search-hit-name">{markTerms(hit.name, shown.terms)}</span>
                    <span className="search-hit-where">
                      {LABELS[hit.kind] ?? hit.kind}
                      {VERSIONED.has(hit.kind) && hit.sub ? ` · ${hit.sub}` : ""}
                      {" · "}{hit.root_name}
                      {hit.scope === "campaign" ? " · campaign" : ""}
                    </span>
                  </span>
                  {/* A record with no body snippets as its own name, which is
                      a row saying the same thing twice. */}
                  {hit.snippet && hit.snippet !== hit.name && (
                    <span className="search-hit-snippet">
                      {markTerms(hit.snippet, shown.terms)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}

        {shown.truncated && (
          <p className="ledger-lead">
            {shown.total} records match; the {hits.length} best are shown. Narrow it with a
            kind or a scope, or add a term.
          </p>
        )}

        <p className="ledger-lead">
          A record a campaign has not changed is still its world's file, so it is listed
          once — under that world. A campaign row is a record that campaign has made its
          own. <Link to="/library">The library →</Link>
        </p>
      </div>
    </PageShell>
  );
}
