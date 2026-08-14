import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Timeline, type TimelineScene } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";

/** What a failed load degrades to: the empty state, never a stuck "Reading…" —
 *  the policy the ledger runs on. */
const EMPTY: Timeline = { scenes: [], threads: [] };

type State = "all" | "absorbed" | "open";

const STATES: { key: State; label: string }[] = [
  { key: "all", label: "Every scene" },
  { key: "absorbed", label: "Absorbed" },
  { key: "open", label: "Not absorbed" },
];

/** The distinct in-fiction dates, in **play order** — first appearance walking
 *  the scenes as the server sorted them.
 *
 *  Play order, not sorted order, and that is the whole of it: a native date is
 *  `<year>-<month key>-<day>` where the month key is a *string* a calendar
 *  provider supplies, so sorting those strings orders months alphabetically.
 *  The scene sequence is the authority on when things happened (a flashback is
 *  out of date order on purpose), so the dates inherit its order rather than
 *  imposing one of their own. */
function momentsOf(scenes: TimelineScene[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const s of scenes) {
    if (s.date && !seen.has(s.date)) { seen.add(s.date); out.push(s.date); }
  }
  return out;
}

/** Each scene's position in that sequence of moments, by index.
 *
 *  A scene with no date **carries the rank of the last dated scene before it**,
 *  which is exactly what an undated scene means on a timeline: it happened
 *  after that date and before the next. Without the carry-forward every
 *  undated scene — the ordinary case for anything not yet absorbed, and for
 *  any scene whose datetime has not been set — would drop out of every span
 *  the reader picked, which is the opposite of what a span is for.
 *
 *  A scene before *any* dated one ranks -1: it genuinely precedes the first
 *  known moment, so it belongs to no span that starts at one. */
function ranksOf(scenes: TimelineScene[], moments: string[]): number[] {
  let running = -1;
  return scenes.map((s) => {
    if (s.date) running = moments.indexOf(s.date);
    return running;
  });
}

export default function TimelineView() {
  const { cid = "" } = useParams();
  const [name, setName] = useState("");
  // Held with the campaign the cards came FROM, the way the ledger holds its
  // rows: the route is not keyed on `cid`, so a campaign switch keeps this
  // component mounted and a bare `Timeline | null` would go on showing one
  // game's scenes under the other's name until the new request settled.
  const [loaded, setLoaded] = useState<{ cid: string; data: Timeline } | null>(null);

  // The three filters the view offers. Threads are multi-select (OR): asking
  // "the sea wall or the debt" is the ordinary way a reader narrows a play
  // history, and forcing one at a time would make that two passes.
  const [threads, setThreads] = useState<string[]>([]);
  const [state, setState] = useState<State>("all");
  // Indices into `moments`, or null for "no bound". Held as dates rather than
  // indices would break the moment a scene is re-dated under them.
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);

  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => setName(c.meta.name)).catch(() => setName(cid));
  }, [cid]);

  useEffect(() => {
    // Superseded responses are dropped rather than raced: two reads can be in
    // flight after a campaign switch, and without this whichever settles LAST
    // wins regardless of which was asked for.
    let live = true;
    api.campaignTimeline(cid)
      .then((t) => { if (live) setLoaded({ cid, data: t }); })
      .catch(() => { if (live) setLoaded({ cid, data: EMPTY }); });
    return () => { live = false; };
  }, [cid]);

  // The filters describe *this* campaign's threads and moments, so a switch has
  // to clear them — a thread id from the campaign you left narrows the one you
  // arrived at to nothing, with no visible cause.
  useEffect(() => { setThreads([]); setState("all"); setFrom(null); setTo(null); }, [cid]);

  const timeline = loaded && loaded.cid === cid ? loaded.data : null;
  const scenes = useMemo(() => timeline?.scenes ?? [], [timeline]);
  const moments = useMemo(() => momentsOf(scenes), [scenes]);
  const ranks = useMemo(() => ranksOf(scenes, moments), [scenes, moments]);

  /** How many scenes each thread touches — the count beside its chip, and the
   *  reason the roster is worth a column of its own. */
  const perThread = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of scenes) {
      for (const t of new Set(s.beats.map((b) => b.thread))) {
        counts.set(t, (counts.get(t) ?? 0) + 1);
      }
    }
    return counts;
  }, [scenes]);

  const shown = useMemo(() => scenes.filter((s, i) => {
    if (threads.length && !s.beats.some((b) => threads.includes(b.thread))) return false;
    if (state === "absorbed" && !s.done) return false;
    if (state === "open" && s.done) return false;
    if (from !== null && ranks[i] < from) return false;
    if (to !== null && ranks[i] > to) return false;
    return true;
  }), [scenes, threads, state, from, to, ranks]);

  const filtered = threads.length > 0 || state !== "all" || from !== null || to !== null;
  const clear = useCallback(() => {
    setThreads([]); setState("all"); setFrom(null); setTo(null);
  }, []);
  const toggleThread = useCallback((id: string) => {
    setThreads((cur) => cur.includes(id) ? cur.filter((t) => t !== id) : [...cur, id]);
  }, []);

  /** What this page contributes to ⌘K: its threads, so "the sea wall" narrows
   *  the play history from anywhere rather than being a chip you have to be
   *  here to find. */
  const paletteSource = useCallback((): PaletteItem[] =>
    (timeline?.threads ?? []).map((t) => ({
      id: `timeline:${t.id}`, group: "IN THIS CAMPAIGN", label: t.title,
      meta: `timeline · ${perThread.get(t.id) ?? 0} scenes`,
      run: () => setThreads([t.id]),
    })), [timeline, perThread]);
  usePaletteSource(paletteSource);

  const column = (
    <>
      <Link className="column-back" to={`/campaigns/${cid}`}>‹ {name || "The campaign"}</Link>
      <div className="ledger-ident">
        <div className="eyebrow">What this campaign has played</div>
        <h2 className="ledger-ident-name">{name || cid}</h2>
      </div>

      <ColumnSection label="Scenes" count={timeline ? `${shown.length}/${scenes.length}` : "—"}>
        {STATES.map((s) => (
          <button key={s.key} className={"column-row" + (state === s.key ? " active" : "")}
                  onClick={() => setState(s.key)}>
            <span className="column-row-label">{s.label}</span>
          </button>
        ))}
      </ColumnSection>

      {/* Only the threads with a beat somewhere are offered — the server drops
          the rest for the same reason: a chip that filters to nothing is worse
          than no chip. So an empty roster is a real answer, not a gap. */}
      <ColumnSection label="Threads" count={timeline ? timeline.threads.length : "—"}>
        {timeline && timeline.threads.length === 0 && (
          <p className="column-empty">No thread has moved in a scene yet.</p>
        )}
        {(timeline?.threads ?? []).map((t) => (
          <button key={t.id}
                  className={"column-row" + (threads.includes(t.id) ? " active" : "")}
                  aria-pressed={threads.includes(t.id)}
                  onClick={() => toggleThread(t.id)}>
            <span className="column-row-label">{t.title}</span>
            <span className="column-row-count">{perThread.get(t.id) ?? 0}</span>
          </button>
        ))}
      </ColumnSection>

      {/* Two bounds over the campaign's own moments rather than a date field:
          the dates are calendar-provider strings, so there is nothing to type
          into and nothing to parse — the reader picks from what the campaign
          actually has. Hidden entirely when it has none, since a span control
          over zero moments is furniture. */}
      {moments.length > 0 && (
        <ColumnSection label="Span">
          <label className="timeline-span">
            <span>From</span>
            <select value={from === null ? "" : from}
                    onChange={(e) => setFrom(e.target.value === "" ? null : Number(e.target.value))}>
              <option value="">The beginning</option>
              {moments.map((m, i) => <option key={m} value={i}>{m}</option>)}
            </select>
          </label>
          <label className="timeline-span">
            <span>To</span>
            <select value={to === null ? "" : to}
                    onChange={(e) => setTo(e.target.value === "" ? null : Number(e.target.value))}>
              <option value="">Now</option>
              {moments.map((m, i) => <option key={m} value={i}>{m}</option>)}
            </select>
          </label>
        </ColumnSection>
      )}
    </>
  );

  const footer = (
    <button className="timeline-clear" onClick={clear} disabled={!filtered}>
      <span>{filtered ? "Clear filters" : "No filters"}</span>
    </button>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Timeline filters">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow">EVERY SCENE IN PLAY ORDER · BEATS AS THEY LANDED</div>
            <h1 className="screen-title">Timeline</h1>
          </div>
        </div>

        {timeline === null && <p className="column-empty">Reading the timeline…</p>}

        {timeline !== null && scenes.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">
              No scenes yet. The timeline fills in as you play — a scene lands here the
              moment it exists, and gains its summary when you end it.
            </span>{" "}
            <Link to={`/campaigns/${cid}`}>Back to play →</Link>
          </p>
        )}

        {timeline !== null && scenes.length > 0 && shown.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">
              No scene matches these filters. {scenes.length} played in all.
            </span>{" "}
            <button className="link" onClick={clear}>Clear them →</button>
          </p>
        )}

        {shown.length > 0 && (
          <ol className="timeline-list">
            {shown.map((s) => (
              <li key={s.id} className={"timeline-card" + (s.done ? " done" : "")}>
                <div className="timeline-when">{s.date || "UNDATED"}</div>
                <div className="timeline-body">
                  <h2 className="timeline-title">
                    {/* The card is a way back into the scene, not a dead
                        record: this is the same route the play view answers
                        to with a scene selected (#87). */}
                    <Link to={`/campaigns/${cid}/scenes/${s.id}`}>{s.title}</Link>
                  </h2>
                  {/* An unabsorbed scene has no summary and is the ORDINARY
                      case, so it says what it is rather than rendering blank
                      — a card with a hole in it reads as a bug. */}
                  <p className={"timeline-line" + (s.one_line ? "" : " pending")}>
                    {s.one_line || "Not absorbed yet — no summary written."}
                  </p>
                  <div className="timeline-meta">
                    {s.location && <span className="chip on">{s.location}</span>}
                    {s.pcless && <span className="chip on">OFFSCREEN</span>}
                    <span className="chip on">{s.done ? "ABSORBED" : "IN PLAY"}</span>
                  </div>
                  {s.beats.length > 0 && (
                    <ul className="timeline-beats">
                      {s.beats.map((b, i) => (
                        <li key={`${b.thread}-${i}`}>
                          {/* The thread is a filter, not a label: the reason
                              to see "the sea wall" on a card is to ask what
                              else it touched. */}
                          <button className={"chip" + (threads.includes(b.thread) ? " on" : "")}
                                  onClick={() => toggleThread(b.thread)}>
                            {/* Cased as written, not upper-cased here: `.chip`
                                already carries `text-transform`, and doing it
                                twice makes the accessible name shout. */}
                            {b.title} · {b.status}
                          </button>
                          <span className="timeline-beat-text">{b.text}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </PageShell>
  );
}
