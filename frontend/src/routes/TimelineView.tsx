import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Timeline } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";
import { boundRank, inSpan, spanOf } from "./timelineSpan";

/** What a failed load degrades to: the empty state, never a stuck "Reading…" —
 *  the policy the ledger runs on. */
const EMPTY: Timeline = { scenes: [], threads: [] };

/** Which half of the campaign to show: everything, the scenes whose absorb has
 *  run, or the ones still in play. */
type AbsorbFilter = "all" | "absorbed" | "open";

const ABSORB_FILTERS: { key: AbsorbFilter; label: string }[] = [
  { key: "all", label: "Every scene" },
  { key: "absorbed", label: "Absorbed" },
  { key: "open", label: "Not absorbed" },
];

export default function TimelineView() {
  const { cid = "" } = useParams();
  // Held with the campaign it names, and dropped rather than raced, for exactly
  // the reason the timeline below is: the route is not keyed on `cid`, so a
  // campaign switch keeps this component mounted, and an unheld name would sit
  // over the other game's scenes — in the heading, the back link and the shell
  // context at once — until this settled. Two reads can also be in flight at
  // once, and nothing orders their responses.
  const [named, setNamed] = useState<{ cid: string; name: string } | null>(null);
  const [loaded, setLoaded] = useState<{ cid: string; data: Timeline } | null>(null);

  // The three filters the view offers. Threads are multi-select (OR): asking
  // "the sea wall or the debt" is the ordinary way a reader narrows a play
  // history, and forcing one at a time would make that two passes.
  const [threads, setThreads] = useState<string[]>([]);
  const [absorb, setAbsorb] = useState<AbsorbFilter>("all");
  // The span bounds, held as **dates** rather than as positions — see
  // `boundRank`, which carries the argument: a position is an index into a list
  // derived from the data, so a scene re-dated underneath it re-points the
  // filter at a different moment with nothing on screen changing.
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string | null>(null);

  const name = named && named.cid === cid ? named.name : "";
  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  useEffect(() => {
    let live = true;
    api.getCampaign(cid)
      .then((c) => { if (live) setNamed({ cid, name: c.meta.name }); })
      .catch(() => { if (live) setNamed({ cid, name: cid }); });
    return () => { live = false; };
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

  const clear = useCallback(() => {
    setThreads([]); setAbsorb("all"); setFrom(null); setTo(null);
  }, []);

  // The filters describe *this* campaign's threads and moments, so a switch has
  // to clear them — a thread id from the campaign you left narrows the one you
  // arrived at to nothing, with no visible cause.
  //
  // On a *switch*, which is why the first cid is remembered rather than just
  // calling `clear()` on every run: the filters start cleared, and `setThreads([])`
  // installs a new array every time, so an unguarded effect would fail the
  // Object.is bail-out and spend a second render on mount saying nothing.
  const priorCid = useRef(cid);
  useEffect(() => {
    if (priorCid.current === cid) return;
    priorCid.current = cid;
    clear();
  }, [cid, clear]);

  const timeline = loaded && loaded.cid === cid ? loaded.data : null;
  const scenes = useMemo(() => timeline?.scenes ?? [], [timeline]);
  const { moments, ranks } = useMemo(() => spanOf(scenes), [scenes]);

  // The bounds resolved to positions — `null` both when nothing is chosen and
  // when what was chosen is a date this campaign no longer has. Resolved once,
  // here, and everything downstream reads THESE rather than the raw held
  // strings: the filter, the "is anything filtering" test behind the pinned
  // control, and the selects' own displayed values. A vanished bound has to
  // read as absent in all three or the page disagrees with itself — a select
  // showing "The beginning" over state that still says otherwise.
  const fromRank = boundRank(moments, from);
  const toRank = boundRank(moments, to);

  /** Everything the OTHER two filters admit. The thread chips are counted
   *  against this rather than against the whole campaign, and `shown` is this
   *  narrowed by them.
   *
   *  That split is the point: a count taken over every scene reads "2" beside a
   *  chip that, with NOT ABSORBED also on, produces nothing when clicked — the
   *  column contradicting the page it labels. `LedgerView` learned the same
   *  thing about SHOW RETIRED and its section counts. */
  const base = useMemo(() => scenes.filter((s, i) =>
    !(absorb === "absorbed" && !s.done)
    && !(absorb === "open" && s.done)
    && inSpan(ranks[i], fromRank, toRank)),
  [scenes, absorb, fromRank, toRank, ranks]);

  /** How many of those each thread touches — the count beside its chip, and the
   *  reason the roster is worth a column of its own. */
  const perThread = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of base) {
      for (const t of new Set(s.beats.map((b) => b.thread))) {
        counts.set(t, (counts.get(t) ?? 0) + 1);
      }
    }
    return counts;
  }, [base]);

  const shown = useMemo(() => (
    threads.length
      ? base.filter((s) => s.beats.some((b) => threads.includes(b.thread)))
      : base
  ), [base, threads]);

  const filtered = threads.length > 0 || absorb !== "all"
                   || fromRank !== null || toRank !== null;
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
        {/* `aria-pressed`, like the thread chips below: the `.active` class is
            the only other thing saying which of the three is on, and a class is
            not something a screen reader can read. */}
        {ABSORB_FILTERS.map((f) => (
          <button key={f.key} className={"column-row" + (absorb === f.key ? " active" : "")}
                  aria-pressed={absorb === f.key}
                  onClick={() => setAbsorb(f.key)}>
            <span className="column-row-label">{f.label}</span>
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
          {/* The option VALUE is the date itself, so the bound survives the
              list being rebuilt under it — see `boundRank`. Displayed back
              through `moments[rank]` rather than the held string, which is
              the same date whenever it resolves and needs no cast to prove
              an option exists for it. */}
          <label className="timeline-span">
            <span>From</span>
            <select value={fromRank === null ? "" : moments[fromRank]}
                    onChange={(e) => setFrom(e.target.value || null)}>
              <option value="">The beginning</option>
              {moments.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="timeline-span">
            <span>To</span>
            <select value={toRank === null ? "" : moments[toRank]}
                    onChange={(e) => setTo(e.target.value || null)}>
              <option value="">Now</option>
              {moments.map((m) => <option key={m} value={m}>{m}</option>)}
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
                                  aria-pressed={threads.includes(b.thread)}
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
