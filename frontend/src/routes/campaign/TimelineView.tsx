/**
 * Timeline view (spec 14 §Timeline view).
 *
 * Scenes rendered as cards along the in-game timeline. Threads from
 * ``threads_introduced`` / ``threads_paid_off`` are drawn as visual lines
 * connecting their endpoints; the user can filter by mood / tag / status
 * and search by title or summary.
 */

import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { campaignApi } from "../../api/campaign";
import { viewsApi } from "../../api/views";
import type { SceneSummary, Thread } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Loading } from "./common";
import { ImportSceneDialog } from "./ImportSceneDialog";

export function TimelineView() {
  const { campaignId = "" } = useParams();
  const navigate = useNavigate();
  const state = useApi(useCallback(() => viewsApi.listScenes(campaignId), [campaignId]));
  const charState = useApi(
    useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]),
  );
  const pcState = useApi(
    useCallback(() => campaignApi.listPCs(campaignId), [campaignId]),
  );
  const nameMap = useMemo(() => {
    const m = new Map<string, string>();
    if (charState.status === "ok") {
      for (const rc of charState.data) {
        m.set(rc.character.id, rc.character.name);
        m.set(`emergent/${rc.character.id}`, rc.character.name);
      }
    }
    if (pcState.status === "ok") {
      for (const pc of pcState.data) {
        m.set(pc.character_ref, pc.name);
      }
    }
    return m;
  }, [charState, pcState]);

  const [search, setSearch] = useState("");
  const [moodFilter, setMoodFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "closed">("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);

  const jumpToScene = (sceneId: string) => {
    navigate(`/campaigns/${encodeURIComponent(campaignId)}?scene=${encodeURIComponent(sceneId)}`);
  };

  const toggleExpand = (id: string) => setExpanded((prev) => (prev === id ? null : id));

  return (
    <section className="route campaign-timeline" aria-labelledby="timeline-heading">
      <header className="route-header">
        <h2 id="timeline-heading">Timeline</h2>
        <button
          type="button"
          className="import-scene-btn"
          onClick={() => setShowImport(true)}
        >
          Import Scene
        </button>
      </header>
      {showImport && (
        <ImportSceneDialog
          campaignId={campaignId}
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false);
            state.reload();
          }}
        />
      )}
      <Loading state={state} emptyMessage="No scenes recorded yet.">
        {(scenes) => {
          const moods = collectMoods(scenes);
          const visible = filterScenes(scenes, search, moodFilter, statusFilter);
          const threadLinks = collectThreadLinks(visible);
          return (
            <div className="timeline-layout">
              <div className="timeline-toolbar">
                <input
                  type="search"
                  placeholder="Search title or summary…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  aria-label="Search scenes"
                />
                <label className="field">
                  <span>Mood</span>
                  <select value={moodFilter} onChange={(e) => setMoodFilter(e.target.value)}>
                    <option value="all">All</option>
                    {moods.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Status</span>
                  <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value as "all" | "open" | "closed")}
                  >
                    <option value="all">All</option>
                    <option value="open">Open</option>
                    <option value="closed">Closed</option>
                  </select>
                </label>
              </div>

              <ol className="timeline">
                {visible.map((scene) => (
                  <SceneCard
                    key={scene.id}
                    scene={scene}
                    nameMap={nameMap}
                    expanded={expanded === scene.id}
                    onToggle={() => toggleExpand(scene.id)}
                    onJump={() => jumpToScene(scene.id)}
                  />
                ))}
                {visible.length === 0 && (
                  <li className="muted">No scenes match the current filters.</li>
                )}
              </ol>

              {threadLinks.length > 0 && (
                <aside className="thread-summary">
                  <h3>Threads</h3>
                  <ul>
                    {threadLinks.map((link, i) => (
                      <li key={i}>
                        <strong>{link.text}</strong>
                        <span className="muted">
                          {" "}
                          · {link.openedIn} → {link.resolvedIn ?? "(unresolved)"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </aside>
              )}
            </div>
          );
        }}
      </Loading>
    </section>
  );
}

function SceneCard({
  scene,
  nameMap,
  expanded,
  onToggle,
  onJump,
}: {
  scene: SceneSummary;
  nameMap: Map<string, string>;
  expanded: boolean;
  onToggle: () => void;
  onJump: () => void;
}) {
  const time = scene.in_game_start?.moment ?? "";
  const pcSet = new Set(scene.present_pc_refs);
  return (
    <li className={expanded ? "timeline-item active" : "timeline-item"}>
      <button type="button" onClick={onToggle} className="timeline-card">
        <div className="timeline-card-head">
          <span className="ordinal">#{scene.ordinal}</span>
          <span className="title">{scene.title || scene.slug}</span>
          {scene.closed && <span className="badge">closed</span>}
          <span className="timeline-card-post-count">
            {scene.post_count} post{scene.post_count === 1 ? "" : "s"}
          </span>
        </div>
        <div className="timeline-card-meta">
          {time && <time dateTime={time}>{formatTime(time)}</time>}
          {scene.location_ref && <span>· {scene.location_ref}</span>}
          {scene.mood && <span className={`mood mood-${slug(scene.mood)}`}>· {scene.mood}</span>}
        </div>
        {scene.present_character_refs.length > 0 && (
          <ul className="timeline-cast">
            {scene.present_character_refs.map((ref) => (
              <li key={ref} className={pcSet.has(ref) ? "cast-chip pc" : "cast-chip"}>
                {nameMap.get(ref) ?? ref}
              </li>
            ))}
          </ul>
        )}
        {scene.summary && <p className="timeline-summary">{scene.summary}</p>}
        {scene.tags.length > 0 && (
          <ul className="tag-row">
            {scene.tags.map((t) => (
              <li key={t} className="tag">
                {t}
              </li>
            ))}
          </ul>
        )}
      </button>
      {expanded && (
        <div className="timeline-detail">
          {scene.key_beats.length > 0 && (
            <div className="timeline-detail-section">
              <h4>Key beats</h4>
              <ul>
                {scene.key_beats.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
          )}
          <ThreadList title="Introduced" threads={scene.threads_introduced} />
          <ThreadList title="Paid off" threads={scene.threads_paid_off} />
          <button type="button" className="primary timeline-jump-btn" onClick={onJump}>
            Jump to scene
          </button>
        </div>
      )}
    </li>
  );
}

function ThreadList({ title, threads }: { title: string; threads: Thread[] }) {
  if (threads.length === 0) return null;
  return (
    <>
      <h4>{title}</h4>
      <ul>
        {threads.map((t, i) => (
          <li key={i}>{t.text}</li>
        ))}
      </ul>
    </>
  );
}

// ---------- helpers ----------

function collectMoods(scenes: SceneSummary[]): string[] {
  const set = new Set<string>();
  for (const s of scenes) {
    if (s.mood) set.add(s.mood);
  }
  return [...set].sort();
}

function filterScenes(
  scenes: SceneSummary[],
  search: string,
  mood: string,
  status: "all" | "open" | "closed",
): SceneSummary[] {
  const needle = search.trim().toLowerCase();
  return scenes
    .filter((s) => {
      if (mood !== "all" && s.mood !== mood) return false;
      if (status === "open" && s.closed) return false;
      if (status === "closed" && !s.closed) return false;
      if (!needle) return true;
      return (
        s.title.toLowerCase().includes(needle) ||
        s.summary.toLowerCase().includes(needle) ||
        s.slug.toLowerCase().includes(needle)
      );
    })
    .sort((a, b) => a.ordinal - b.ordinal);
}

interface ThreadLink {
  text: string;
  openedIn: string;
  resolvedIn: string | null;
}

function collectThreadLinks(scenes: SceneSummary[]): ThreadLink[] {
  const out: ThreadLink[] = [];
  for (const s of scenes) {
    for (const t of s.threads_introduced) {
      out.push({ text: t.text, openedIn: `#${s.ordinal}`, resolvedIn: null });
    }
  }
  for (const s of scenes) {
    for (const t of s.threads_paid_off) {
      const existing = out.find((x) => x.text === t.text && !x.resolvedIn);
      if (existing) existing.resolvedIn = `#${s.ordinal}`;
      else out.push({ text: t.text, openedIn: "?", resolvedIn: `#${s.ordinal}` });
    }
  }
  return out;
}

function formatTime(moment: string): string {
  try {
    const d = new Date(moment);
    if (Number.isNaN(d.getTime())) return moment;
    return d.toLocaleString();
  } catch {
    return moment;
  }
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}
