import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, type ErrorSummary, type LogLevel, type LogPage, type LogRow,
  type PerfBucket, type Stats,
} from "../api/client";
import { errorText } from "../api/errors";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";

type SectionKey = "performance" | "errors" | "log";

const SECTIONS: { key: SectionKey; label: string; eyebrow: string }[] = [
  { key: "performance", label: "Performance",
    eyebrow: "LATENCY PERCENTILES · ERROR RATES · TRENDS" },
  { key: "errors", label: "Errors", eyebrow: "WHAT FAILED, PER MODULE" },
  { key: "log", label: "Debug log", eyebrow: "EVERY RECORDED LINE · FILTERABLE · LIVE" },
];

/** The windows the day control offers. Not a free number field: every value
 *  here is one the ledger and the log can both answer, and the backend clamps
 *  anything past 366 anyway. */
const WINDOWS = [1, 7, 30, 90];

/** Rows the log page asks for. Comfortably under the server's own ceiling, and
 *  the count `truncated` is measured against when the window held more. */
const LOG_LIMIT = 200;

/** Rows the live tail keeps on screen before dropping the oldest.
 *
 *  A tail left open on a busy library grows without bound otherwise, and a
 *  panel holding fifty thousand DOM nodes is a page that stops scrolling. The
 *  file is the record; this is a window onto it. */
const TAIL_KEEP = 500;

/** How long the text filter waits before it is applied. Short enough not to be
 *  felt, long enough that typing a word is one reconnect of the live tail
 *  rather than one per letter. */
const QUERY_SETTLE_MS = 250;

/** ms as something a human reads at a glance. Sub-second stays in
 *  milliseconds, because that is the resolution the number has; past a second
 *  the digits stop meaning anything and one decimal of seconds is the honest
 *  precision. */
function duration(ms: number): string {
  // "0ms", not a dash. A dash reads as "nothing was measured", and zero here
  // is a measurement: a sub-millisecond call, or a row whose duration the
  // ledger could not parse. The `calls` count beside every one of these is
  // what says whether there was anything to measure at all.
  if (ms < 1000) return `${Math.max(0, ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function percent(rate: number): string {
  if (!rate) return "0%";
  return rate < 0.001 ? "<0.1%" : `${(rate * 100).toFixed(1)}%`;
}

/** The clock half of a timestamp, which is the half that distinguishes two log
 *  rows; the date is in the window control above them. */
function clock(ts: string): string {
  return ts.slice(11, 23) || ts;
}

/** A distribution table. One component for `by_task`, `by_model` and `by_day`,
 *  because they are the same five columns over the same shape and three
 *  copies is three places for those columns to drift apart. */
function BucketTable(
  { buckets, heading, label }: { buckets: PerfBucket[]; heading: string; label: string },
) {
  if (buckets.length === 0) return null;
  return (
    <section className="stats-block">
      <h2 className="section-label">{heading}</h2>
      <div className="ledger-table-wrap">
        <table className="ledger-table stats-table">
          <thead>
            <tr>
              <th scope="col">{label}</th>
              <th scope="col">Calls</th>
              <th scope="col">Failed</th>
              <th scope="col">p50</th>
              <th scope="col">p90</th>
              <th scope="col">p99</th>
              <th scope="col">Slowest</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => (
              <tr key={b.key}>
                <td className="stats-key">
                  {b.key}
                  {/* A percentile over a sample is still a percentile, but a
                      reader comparing two rows deserves to know which one was
                      measured over everything. */}
                  {b.sampled && <span className="field-hint"> sampled</span>}
                </td>
                <td>{b.calls}</td>
                <td className={b.errors ? "stats-bad" : undefined}>
                  {b.errors ? `${b.errors} · ${percent(b.error_rate)}` : "—"}
                </td>
                <td>{duration(b.p50)}</td>
                <td>{duration(b.p90)}</td>
                <td>{duration(b.p99)}</td>
                <td>{duration(b.max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** A bar per day, scaled to the biggest in the window.
 *
 *  Drawn in CSS rather than with a chart library: the whole shape is "one
 *  proportional bar per day", and a dependency for that would be a dependency
 *  the packaged desktop and Android bundles carry for one panel. */
function Trend(
  { rows, heading, format }: {
    rows: { key: string; value: number; note?: string }[];
    heading: string;
    format: (value: number) => string;
  },
) {
  const peak = Math.max(1, ...rows.map((r) => r.value));
  if (rows.length === 0) return null;
  return (
    <section className="stats-block">
      <h2 className="section-label">{heading}</h2>
      <ol className="stats-trend">
        {rows.map((r) => (
          <li key={r.key}>
            <span className="stats-trend-day">{r.key}</span>
            <span className="stats-trend-track">
              <span className="stats-trend-bar"
                    style={{ width: `${Math.round((r.value / peak) * 100)}%` }} />
            </span>
            <span className="stats-trend-value">{format(r.value)}</span>
            {r.note && <span className="field-hint">{r.note}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Keys for a list of log rows that is PREPENDED to.
 *
 *  Neither obvious key works. A bare array index is stable only while the
 *  list is append-only, and the live tail puts new rows at the FRONT — so
 *  every existing row's key would change on every batch. The timestamp is not
 *  unique either: rows are stamped to the millisecond and a retry loop
 *  produces several inside one.
 *
 *  So: the row's own content, plus an occurrence number for the genuinely
 *  identical ones. Prepending leaves every existing key untouched, and two
 *  byte-identical lines still get distinct keys instead of React silently
 *  dropping one of them. */
function keyed(rows: LogRow[]): { row: LogRow; key: string }[] {
  const seen = new Map<string, number>();
  return rows.map((row) => {
    const base = `${row.ts}|${row.level}|${row.module}|${row.message}`;
    const nth = (seen.get(base) ?? 0) + 1;
    seen.set(base, nth);
    return { row, key: nth === 1 ? base : `${base}#${nth}` };
  });
}

function LogRows({ rows, empty }: { rows: LogRow[]; empty: string }) {
  if (rows.length === 0) return <p className="empty-state"><span className="empty-what">{empty}</span></p>;
  return (
    <ol className="stats-log">
      {keyed(rows).map(({ row: r, key }) => (
        <li key={key} className={`stats-log-row level-${r.level}`}>
          <span className="stats-log-level">{r.level.toUpperCase()}</span>
          <span className="stats-log-time">{clock(r.ts)}</span>
          <span className="stats-log-module">{r.module}</span>
          <span className="stats-log-message">
            {r.message}
            {r.kind && <span className="chip on stats-log-kind">{r.kind}</span>}
            {r.campaign && (
              <span className="field-hint"> {r.campaign}{r.scene ? ` / ${r.scene}` : ""}</span>
            )}
            {/* The traceback the logging bridge captured. Recorded since the
                first version and shown by nothing until now, which made the
                most useful half of an error row write-only -- and a debug log
                whose stack traces are unreachable is not a debug log. Collapsed,
                because four hundred rows each unrolling twenty frames is a page
                nobody can scan. */}
            {r.trace && (
              <details className="stats-log-trace">
                <summary>traceback</summary>
                <pre>{r.trace}</pre>
              </details>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Performance, errors and the debug log: the three views over what the
 *  backend has been doing (#154/#155/#156).
 *
 *  One page rather than a tab strip inside Configuration, which #154 asked
 *  for as a "tab": config is a page of settings, and this is a page of
 *  readings — nothing here is editable except the log's own level, which is
 *  saved through the same `PUT /config` everything else is. It is the
 *  `PageShell` column pattern like every other page: the column says what you
 *  are navigating, main says what you are reading.
 */
export default function StatsView() {
  const [section, setSection] = useState<SectionKey>("performance");
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [failed, setFailed] = useState("");

  // ---- the log's own filters ----
  const [errorModule, setErrorModule] = useState("");
  const [errorSummary, setErrorSummary] = useState<ErrorSummary | null>(null);
  const [level, setLevel] = useState<LogLevel>("debug");
  const [module, setModule] = useState("");
  const [query, setQuery] = useState("");
  const [settled, setSettled] = useState("");
  const [page, setPage] = useState<LogPage | null>(null);
  const [live, setLive] = useState(false);
  const [tailed, setTailed] = useState<LogRow[]>([]);
  const [configured, setConfigured] = useState<LogLevel | null>(null);

  useEffect(() => {
    let alive = true;
    setStats(null);
    api.getStats(days)
      .then((s) => { if (alive) { setStats(s); setFailed(""); } })
      .catch((e) => { if (alive) setFailed(errorText(e)); });
    return () => { alive = false; };
  }, [days]);

  // The errors section reads `/api/errors` rather than leaning on the copy
  // `/api/stats` already carries. The embedded one is #154's headline count
  // and is scoped to the stats window alone; this one takes a module filter,
  // which is the question the section exists to answer ("is it always
  // dossiers?"). Same store either way, so the two can never disagree.
  useEffect(() => {
    if (section !== "errors") return;
    let alive = true;
    api.getErrorSummary(days, { module: errorModule })
      .then((e) => { if (alive) { setErrorSummary(e); setFailed(""); } })
      .catch((e) => { if (alive) setFailed(errorText(e)); });
    return () => { alive = false; };
  }, [section, days, errorModule]);

  // `query` settled. The page read alone would not need this -- it is a local
  // file behind a local server, and 200 rows cost nothing -- but the live tail
  // is keyed on the same filters, and an SSE stream torn down and reopened on
  // every keystroke is a burst of connections for a filter the user has not
  // finished typing. One debounce, both readers, so the two cannot end up
  // showing different filters.
  useEffect(() => {
    const timer = setTimeout(() => setSettled(query), QUERY_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // The log page is re-read whenever a filter moves.
  useEffect(() => {
    if (section !== "log") return;
    let alive = true;
    api.getLogs({ level, module, q: settled, limit: LOG_LIMIT })
      .then((p) => { if (alive) { setPage(p); setFailed(""); } })
      .catch((e) => { if (alive) setFailed(errorText(e)); });
    return () => { alive = false; };
  }, [section, level, module, settled]);

  useEffect(() => {
    let alive = true;
    api.getLogLevel().then((l) => { if (alive) setConfigured(l.level); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // ---- the live tail ----
  //
  // Its own effect, keyed on the filters as well as the switch, so changing a
  // filter mid-tail reopens the stream against the new one rather than
  // quietly continuing to receive rows the page is no longer showing.
  const tailRows = useRef<LogRow[]>([]);
  useEffect(() => {
    if (!live || section !== "log") return;
    const abort = new AbortController();
    tailRows.current = [];
    setTailed([]);
    api.streamLogTail({ level, module, q: settled }, (event) => {
      if (!event.rows?.length) return;
      // Newest first, matching the page above it -- the two lists are read as
      // one, and a live half that grew downward while the page grew upward
      // would be two lists pretending to be one.
      tailRows.current = [...event.rows].reverse()
        .concat(tailRows.current).slice(0, TAIL_KEEP);
      setTailed(tailRows.current);
    }, abort.signal).catch((e) => {
      // An abort is this effect cleaning up, not a failure to report.
      if (!abort.signal.aborted) setFailed(errorText(e));
    });
    return () => abort.abort();
  }, [live, section, level, module, settled]);

  const paletteSource = useCallback((): PaletteItem[] =>
    SECTIONS.map((s) => ({
      id: `stats:${s.key}`, group: "ON THIS PAGE", label: s.label,
      meta: "readings", action: true, run: () => setSection(s.key),
    })), []);
  usePaletteSource(paletteSource);

  const current = SECTIONS.find((s) => s.key === section) ?? SECTIONS[0];
  // Whichever is current: the section's own filtered read once it has landed,
  // the stats copy until then, so opening Errors does not blank the page.
  const errors: ErrorSummary | null = errorSummary ?? stats?.errors ?? null;
  // Built from the unfiltered copy, so picking a module cannot remove every
  // other option from the control you picked it with.
  const errorModules = stats?.errors.modules.map((m) => m.module) ?? [];

  const counts = useMemo(() => ({
    performance: stats ? stats.totals.calls : null,
    errors: errors ? errors.total : null,
    log: page ? page.total : null,
  }), [stats, errors, page]);

  const column = (
    <>
      <div className="ledger-ident">
        <div className="eyebrow">What the backend has been doing</div>
        <h2 className="ledger-ident-name">Instrumentation</h2>
      </div>
      <ColumnSection label="Readings">
        {SECTIONS.map((s) => (
          <button key={s.key} type="button"
                  className={"column-row" + (section === s.key ? " active" : "")}
                  onClick={() => setSection(s.key)}>
            <span className="column-row-label">{s.label}</span>
            {/* A dash is "still reading"; a 0 would claim the section is
                empty, which is a different and possibly wrong statement. */}
            <span className="column-row-count">
              {counts[s.key] === null ? "—" : counts[s.key]}
            </span>
          </button>
        ))}
      </ColumnSection>
    </>
  );

  const footer = (
    <label className="stats-window">
      <span className="section-label">Window</span>
      <select value={days} onChange={(e) => setDays(Number(e.target.value))}
              aria-label="How many days to report on">
        {WINDOWS.map((d) => (
          <option key={d} value={d}>{d === 1 ? "Today" : `Last ${d} days`}</option>
        ))}
      </select>
    </label>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Readings">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow">{current.eyebrow}</div>
            <h1 className="screen-title">{current.label}</h1>
          </div>
        </div>

        {failed && <p className="empty-state"><span className="empty-what">{failed}</span></p>}

        {section === "performance" && (
          stats === null
            ? <p className="column-empty">Reading the ledger…</p>
            : <>
                <dl className="stats-cards">
                  <div><dt>Calls</dt><dd>{stats.totals.calls}</dd></div>
                  <div><dt>Median</dt><dd>{duration(stats.totals.p50)}</dd></div>
                  <div><dt>p90</dt><dd>{duration(stats.totals.p90)}</dd></div>
                  <div><dt>p99</dt><dd>{duration(stats.totals.p99)}</dd></div>
                  <div>
                    <dt>Failed calls</dt>
                    <dd className={stats.totals.errors ? "stats-bad" : undefined}>
                      {stats.totals.errors} · {percent(stats.totals.error_rate)}
                    </dd>
                  </div>
                </dl>
                {stats.totals.calls === 0 && (
                  <p className="empty-state">
                    <span className="empty-what">No calls in this window yet.</span>{" "}
                    Play a scene and the numbers arrive on their own.
                  </p>
                )}
                <BucketTable buckets={stats.by_task} heading="By task" label="Task" />
                <BucketTable buckets={stats.by_model} heading="By model" label="Model" />
                <Trend heading="Median latency by day" format={duration}
                       rows={stats.by_day.map((d) => ({
                         key: d.key, value: d.p50,
                         note: `${d.calls} call${d.calls === 1 ? "" : "s"}`,
                       }))} />
                <p className="ledger-lead">
                  Latency is measured around the whole call, retries included — what the
                  person waiting experienced, not the provider's own service time.
                  “Failed calls” counts calls that went wrong, out of the ledger that also
                  knows how many worked; the Errors section counts failures recorded
                  anywhere, including the ones that were never a call. The two are
                  different questions, so the numbers differ.
                </p>
              </>
        )}

        {section === "errors" && errorModules.length > 1 && (
          // Above the empty check, deliberately: filtering to a module with
          // nothing in it must not also remove the control that would undo
          // that. Built from the unfiltered copy for the same reason.
          <div className="stats-filters">
            <label>
              <span className="section-label">Module</span>
              <select value={errorModule}
                      onChange={(e) => setErrorModule(e.target.value)}
                      aria-label="Module to report on">
                <option value="">every module</option>
                {errorModules.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
          </div>
        )}

        {section === "errors" && (
          errors === null
            ? <p className="column-empty">Reading the log…</p>
            : errors.total === 0
              ? <p className="empty-state">
                  <span className="empty-what">
                    {errorModule
                      ? `Nothing has gone wrong in ${errorModule} in this window.`
                      : "Nothing has gone wrong in this window."}
                  </span>{" "}
                  {errorModule && (
                    <button type="button" className="chip"
                            onClick={() => setErrorModule("")}>every module</button>
                  )}
                </p>
              : <>
                  <section className="stats-block">
                    <h2 className="section-label">By module</h2>
                    <div className="ledger-table-wrap">
                      <table className="ledger-table stats-table">
                        <thead>
                          <tr>
                            <th scope="col">Module</th>
                            <th scope="col">Failures</th>
                            <th scope="col">Kinds</th>
                            <th scope="col">Most recent</th>
                          </tr>
                        </thead>
                        <tbody>
                          {errors.modules.map((m) => (
                            <tr key={m.module}>
                              <td className="stats-key">{m.module}</td>
                              <td className="stats-bad">{m.count}</td>
                              <td>
                                {m.kinds.map((k) => (
                                  <span key={k.kind} className="chip on">
                                    {k.kind} {k.count}
                                  </span>
                                ))}
                              </td>
                              <td title={m.last}>{m.last_detail}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                  <Trend heading="Failures by day" format={(v) => String(v)}
                         rows={errors.daily.map((d) => ({ key: d.day, value: d.count }))} />
                  <section className="stats-block">
                    <h2 className="section-label">
                      Most recent
                      {errors.truncated && (
                        <span className="field-hint">
                          {" "}showing {errors.rows.length} of {errors.total}
                        </span>
                      )}
                    </h2>
                    <LogRows rows={errors.rows} empty="Nothing recorded." />
                  </section>
                </>
        )}

        {section === "log" && (
          <>
            <div className="stats-filters">
              <label>
                <span className="section-label">Level</span>
                <select value={level} onChange={(e) => setLevel(e.target.value as LogLevel)}
                        aria-label="Quietest level to show">
                  {(page?.levels ?? ["debug", "info", "warning", "error", "critical"]).map((l) => (
                    <option key={l} value={l}>{l} and worse</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="section-label">Module</span>
                <select value={module} onChange={(e) => setModule(e.target.value)}
                        aria-label="Module to show">
                  <option value="">every module</option>
                  {(page?.modules ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="stats-search">
                <span className="section-label">Contains</span>
                <input value={query} onChange={(e) => setQuery(e.target.value)}
                       placeholder="message, module or kind" aria-label="Filter by text" />
              </label>
              <label className="stats-live">
                <input type="checkbox" checked={live}
                       onChange={(e) => setLive(e.target.checked)} />
                <span>Live</span>
              </label>
            </div>

            {/* The level a row has to clear to be WRITTEN, which is a different
                thing from the filter above and the one that explains an empty
                page. Changed from Configuration, so there is one writer. */}
            {configured && configured !== "debug" && (
              <p className="field-hint stats-threshold">
                Recording at <strong>{configured}</strong> and above — anything quieter is
                never written. Change it under Configuration.
              </p>
            )}

            {live && (
              <section className="stats-block">
                <h2 className="section-label">
                  Live
                  <span className="field-hint">
                    {" "}{tailed.length === 0 ? "waiting…" : `${tailed.length} since you started watching`}
                  </span>
                </h2>
                <LogRows rows={tailed} empty="Nothing yet. New lines appear here as they are written." />
              </section>
            )}

            <section className="stats-block">
              <h2 className="section-label">
                Recorded
                {page?.truncated && (
                  <span className="field-hint">
                    {" "}showing {page.rows.length} of {page.total}
                  </span>
                )}
              </h2>
              {page === null
                ? <p className="column-empty">Reading the log…</p>
                : <LogRows rows={page.rows} empty="Nothing matches those filters." />}
            </section>
          </>
        )}
      </div>
    </PageShell>
  );
}
