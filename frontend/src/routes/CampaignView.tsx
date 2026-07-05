import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, type Actor, type SceneMeta, type Message, type RosterEntry, type SceneAbsorb,
  type SceneDatetime, type StagedEdit,
} from "../api/client";
import type { ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";
import { CastPanel } from "../components/CastPanel";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { ChangesPanel } from "../components/ChangesPanel";
import { CalendarConfig } from "../components/CalendarConfig";
import { Portrait } from "../components/Portrait";
import { RecordDrawer, type DrawerTarget } from "../components/RecordDrawer";
import { SceneInspector } from "../components/SceneInspector";
import { quotePlugin } from "../markdown/quotePlugin";

// Memoized so typing in the input bar (which re-renders CampaignView on every
// keystroke) doesn't re-parse the markdown of every unchanged message.
const RenderedMarkdown = memo(function RenderedMarkdown({ content }: { content: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[quotePlugin]}>{content}</Markdown>
  );
});

export default function CampaignView({ keySet }: { keySet: boolean }) {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [worldName, setWorldName] = useState("");
  const [dt, setDt] = useState<SceneDatetime | null>(null);
  const [showCalendar, setShowCalendar] = useState(false);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ctxKey, setCtxKey] = useState(0);
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
  const [colorQuotes, setColorQuotes] = useState(false);
  const [labels, setLabels] = useState({ user: "You", assistant: "Grimoire" });
  const [cast, setCast] = useState<Actor[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  const [absorbing, setAbsorbing] = useState(false);
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [seedPrompt, setSeedPrompt] = useState<{ sid: string; prompt: string } | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      setName(c.meta.name);
      setWorldName(c.meta.world_name ?? ""); // embedded: no second fetch
    });
    api.listScenes(cid).then((list) => {
      setScenes(list);
      if (list.length) selectScene(list[0].id);
    });
    api.getConfig().then((c) => {
      setColorQuotes(c.quote_color === "on");
      setLabels({ user: c.user_label || "You", assistant: c.assistant_label || "Grimoire" });
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  // unstamped user lines fall back to the sole player's name on their plate
  const playerName = useMemo(() => {
    const players = cast.filter((a) => a.role === "player");
    return players.length === 1 ? players[0].name : null;
  }, [cast]);

  // offscreen scenes take director notes instead of PC dialogue
  const activePcless = useMemo(
    () => scenes.find((s) => s.id === activeId)?.pcless ?? false,
    [scenes, activeId]);
  const [directorNote, setDirectorNote] = useState<string | null>(null);

  async function selectScene(id: string) {
    setActiveId(id);
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    const scene = await api.getScene(cid, id);
    setMessages(scene.messages);
    setStreaming("");
    setCtxKey((n) => n + 1);
  }

  function newScene() {
    setChooserOpen(true);
  }

  async function sceneCreated(id: string, initialPrompt?: string) {
    setChooserOpen(false);
    if (initialPrompt) setSeedPrompt({ sid: id, prompt: initialPrompt });
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  async function renameScene(id: string, title: string) {
    const { id: newId } = await api.renameScene(cid, id, title);
    if (activeId === id) setActiveId(newId);
    setSeedPrompt((p) => (p && p.sid === id ? { ...p, sid: newId } : p));
    setScenes(await api.listScenes(cid));
  }

  // the first date set renames the scene file — re-list and adopt the new id
  async function sceneRenamed(id: string) {
    setSeedPrompt((p) => (p && p.sid === activeId ? { ...p, sid: id } : p));
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    await api.deleteScene(cid, s.id);
    const list = await api.listScenes(cid);
    setScenes(list);
    if (activeId === s.id) {
      if (list.length) selectScene(list[0].id);
      else {
        setActiveId(null);
        setMessages([]);
      }
    }
  }

  async function runStream(id: string, start: (onEvent: (e: ChatEvent) => void) => Promise<void>) {
    setBusy(true);
    setError(null);
    let acc = "";
    try {
      await start((e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
      // the reply is persisted as per-speaker posts — re-fetch to show them
      // (selectScene also bumps ctxKey and refreshes the player name)
      await selectScene(id);
    }
  }

  async function send() {
    if (busy) return;
    const content = input.trim();
    let id = activeId;
    if (!id) {
      if (!content) return;
      id = (await api.createScene(cid)).id;
      setScenes(await api.listScenes(cid));
      setActiveId(id);
    }
    setInput("");
    // ephemeral turns are never stored: a director note (offscreen scene) or —
    // in any scene — an empty send meaning "next NPC round"
    if (activePcless || !content) {
      if (activePcless) setDirectorNote(content || null);
      try {
        await runStream(id, (onEvent) => api.chat(cid, id!, content, onEvent));
      } finally {
        setDirectorNote(null);
      }
      return;
    }
    setMessages((m) => [...m, { role: "user", content }]);
    await runStream(id, (onEvent) => api.chat(cid, id!, content, onEvent));
  }

  async function saveEdit() {
    if (!editing || !activeId) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    await selectScene(activeId);
  }

  async function retry() {
    if (!activeId || busy) return;
    await runStream(activeId, (onEvent) => api.retry(cid, activeId, onEvent));
  }

  async function reroll() {
    if (!activeId || busy) return;
    const guidance = (rerollPrompt ?? "").trim();
    setRerollPrompt(null);
    // one turn is a run of assistant posts — drop the whole trailing run
    setMessages((m) => {
      let end = m.length;
      while (end > 0 && m[end - 1].role === "assistant") end--;
      return m.slice(0, end);
    });
    // omit the 4th argument entirely for a plain reroll (an explicit
    // undefined would change the call shape)
    await runStream(activeId, (onEvent) => guidance
      ? api.regenerate(cid, activeId!, onEvent, guidance)
      : api.regenerate(cid, activeId!, onEvent));
  }

  async function endScene() {
    if (!activeId || absorbing) return;
    setAbsorbing(true);
    setError(null);
    try {
      const a = await api.absorbScene(cid, activeId);
      setAbsorb(a);
      setEditRows(a.edits.map((e) => ({ ...e, approved: true })));
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setAbsorbing(false);
    }
  }

  async function saveAbsorb() {
    if (!absorb || !activeId) return;
    await api.saveChronicle(cid, activeId, {
      one_line: absorb.one_line, summary: absorb.summary, keywords: absorb.keywords,
      timeline_events: absorb.timeline_events,
      edits: editRows.filter((e) => e.approved).map(({ approved, ...e }) => e) });
    setAbsorb(null);
    setEditRows([]);
    setCtxKey((n) => n + 1);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // rerolling regenerates the trailing assistant run; a run that reaches the
  // first message is the opener and is not rerollable
  const canReroll = messages.length > 0 &&
    messages[messages.length - 1].role === "assistant" &&
    messages.some((x) => x.role === "user");

  const speakerOf = (m: Message) =>
    m.speaker ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant);

  // A speaker label names a cast member if it matches exactly (case-insensitive)
  // or is a word-boundary prefix of exactly one name — "winifred" is winifred
  // winterbourne; an ambiguous or mid-word label matches no one. Mirrors the
  // backend's scenes.match_name so role attribution and plates agree.
  function matchActor(speaker: string): Actor | undefined {
    const low = speaker.trim().toLowerCase();
    if (!low) return undefined;
    const exact = cast.filter((a) => a.name.toLowerCase() === low);
    if (exact.length) return exact.length === 1 ? exact[0] : undefined;
    const prefixed = cast.filter((a) => {
      const n = a.name.toLowerCase();
      return n.startsWith(low) && !/[\p{L}\p{N}]/u.test(n[low.length] ?? "");
    });
    return prefixed.length === 1 ? prefixed[0] : undefined;
  }

  // consecutive messages by the same speaker form one run under a single plate
  type Run = { speaker: string; pc: boolean; actor: Actor | undefined;
               posts: { m: Message; index: number }[] };
  const runs: Run[] = [];
  messages.forEach((m, index) => {
    const speaker = speakerOf(m);
    const last = runs[runs.length - 1];
    if (last && last.speaker === speaker) {
      last.posts.push({ m, index });
      return;
    }
    const actor = matchActor(speaker);
    runs.push({ speaker, pc: actor ? actor.role === "player" : m.role === "user",
                actor, posts: [{ m, index }] });
  });

  function plateAvatar(run: Run): string | null {
    if (!run.actor || run.actor.kind !== "characters") return null;
    const ver = roster.find((r) => r.kind === "characters" && r.id === run.actor!.id)?.version;
    return ver ? api.campaignImageUrl(cid, run.actor.id, ver, "avatar") : null;
  }

  return (
    <div className="workspace">
      <div className="subheader">
        <Link to="/" className="sub-back">‹ Campaigns</Link>
        <span className="sub-divider" />
        <span className="sub-name">{name}</span>
        {worldName && (
          <Link to={`/campaigns/${cid}/world`} className="sub-world">World ▸ {worldName} ↗</Link>
        )}
        <div className="sub-actions">
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="sub-end" onClick={endScene}
                  disabled={!activeId || absorbing || busy}>
            {absorbing ? "Ending…" : "End scene"}
          </button>
        </div>
      </div>
      <div className="layout">
      <aside className="scene-rail">
        <div className="rail-counter">Scenes / {String(scenes.length).padStart(2, "0")}</div>
        <button className="btn-chrome rail-new" onClick={newScene}>+ New Scene</button>
        <div className="rail-scenes">
          {scenes.map((s, i) => (
            <EditableRow
              key={s.id}
              label={s.title}
              prefix={String(scenes.length - i).padStart(2, "0")}
              subtitle={s.pcless ? "Offscreen" : undefined}
              active={s.id === activeId}
              onSelect={() => selectScene(s.id)}
              onRename={(title) => renameScene(s.id, title)}
              onDelete={() => deleteScene(s)}
            />
          ))}
        </div>
        <div className="rail-foot">
          <button className="btn-outline rail-world" onClick={() => navigate(`/campaigns/${cid}/world`)}>
            Campaign World ↗
          </button>
          {dt?.current && (
            <button className="rail-date" onClick={() => setShowCalendar((v) => !v)}
                    title="Calendar settings">
              {dt.current.weekday} {dt.current.friendly}
              {dt.current.holidays_today.length > 0 && (
                <span className="rail-holiday">✦ {dt.current.holidays_today[0]}</span>
              )}
            </button>
          )}
        </div>
      </aside>
      <section className="main">
        {showCalendar && (
          <div className="panel-slot">
            <CalendarConfig cid={cid} />
          </div>
        )}
        {showChanges && <ChangesPanel cid={cid} />}
        {absorb && (
          <div className="absorb-panel">
            <h4>Review scene summary</h4>
            <label className="field-hint" htmlFor="absorb-oneline">One line</label>
            <input id="absorb-oneline" aria-label="Scene one-line" value={absorb.one_line}
                   onChange={(e) => setAbsorb({ ...absorb, one_line: e.target.value })} />
            <label className="field-hint" htmlFor="absorb-summary">Summary</label>
            <textarea id="absorb-summary" aria-label="Scene summary" rows={5} value={absorb.summary}
                      onChange={(e) => setAbsorb({ ...absorb, summary: e.target.value })} />
            {absorb.timeline_events.length > 0 && (
              <ul className="absorb-timeline">
                {absorb.timeline_events.map((t, i) => (
                  <li key={i}><strong>{t.date}</strong> {t.text}</li>
                ))}
              </ul>
            )}
            {editRows.length > 0 && (
              <div className="absorb-edits">
                <h5>Proposed changes</h5>
                {editRows.map((e, i) => (
                  <div className={"absorb-edit" + (e.authored ? " authored" : "")} key={e.id}>
                    <label>
                      <input type="checkbox" aria-label={`Approve ${e.label}`} checked={e.approved}
                             onChange={() => setEditRows((rows) => rows.map((r, j) =>
                               j === i ? { ...r, approved: !r.approved } : r))} />
                      {e.label}{e.authored ? " · card edit" : ""}
                    </label>
                    {e.kind === "relationship" || e.kind === "bond" ? (
                      <div className="absorb-diff">
                        {e.before && <span className="absorb-before">{e.before}</span>}
                        <span className="absorb-after">{e.after}</span>
                      </div>
                    ) : (
                      <>
                        {e.before && <div className="absorb-before">{e.before}</div>}
                        <textarea aria-label={`After ${e.label}`} rows={2} value={e.after}
                                  onChange={(ev) => setEditRows((rows) => rows.map((r, j) =>
                                    j === i ? { ...r, after: ev.target.value } : r))} />
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="form-actions">
              <button className="subtle" onClick={() => { setAbsorb(null); setEditRows([]); }}>Cancel</button>
              <button className="primary" onClick={saveAbsorb}>Save summary</button>
            </div>
          </div>
        )}
        {!keySet && (
          <div className="banner">
            No OpenRouter key set. <Link to="/config">Set your key in Config</Link>.
          </div>
        )}
        {error && (
          <div className="banner error-banner">
            <span>{error}</span>
            <button className="retry" onClick={retry} disabled={busy}>
              Retry
            </button>
          </div>
        )}
        {activeId && messages.length === 0 && (
          <CastPanel
            cid={cid}
            sid={activeId}
            keySet={keySet}
            onSeeded={() => selectScene(activeId)}
            onSceneRenamed={sceneRenamed}
            initialPrompt={seedPrompt?.sid === activeId ? seedPrompt.prompt : undefined}
            pcless={activePcless}
          />
        )}
        {activeId && (
          <h2 className="scene-title">
            {scenes.find((s) => s.id === activeId)?.title ?? ""}
            {activePcless && <span className="chip on offscreen-badge">Offscreen</span>}
          </h2>
        )}
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}>
          {runs.map((run) => (
            <div className={"run" + (run.pc ? " pc" : "")} key={run.posts[0].index}>
              <div className={"plate" + (run.pc ? " pc" : "")}>
                {run.actor ? (
                  <>
                    <button className="plate-avatar" aria-label={`Open ${run.speaker} record`}
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
                      <Portrait src={plateAvatar(run)} name={run.speaker} />
                    </button>
                    <button className="plate-name"
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
                      {run.speaker}
                    </button>
                  </>
                ) : (
                  <>
                    <span className="plate-avatar"><Portrait src={null} name={run.speaker} /></span>
                    <span className="plate-name">{run.speaker}</span>
                  </>
                )}
                <span className="role-chip">{run.pc ? "pc" : "npc"}</span>
              </div>
              {run.posts.map(({ m, index }) => (
                <div className={`msg ${m.role}`} key={index}>
                  <span className="msg-gutter">
                    {editing?.index !== index && !busy && (
                      <span className="gutter-icons">
                        {index === messages.length - 1 && canReroll && (
                          <button className="msg-edit" title="Reroll" aria-label="Reroll"
                                  onClick={() => setRerollPrompt("")}>↻</button>
                        )}
                        <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
                                onClick={() => setEditing({ index, text: m.content })}>✎</button>
                      </span>
                    )}
                    {rerollPrompt !== null && !busy &&
                     index === messages.length - 1 && canReroll && (
                      <span className="reroll-pop">
                        <input
                          autoFocus
                          placeholder="Guide the reroll (optional)…"
                          aria-label="Reroll guidance"
                          value={rerollPrompt}
                          onChange={(e) => setRerollPrompt(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") reroll();
                            if (e.key === "Escape") setRerollPrompt(null);
                          }}
                        />
                        <button className="btn-chrome" onClick={reroll}>Reroll ▸</button>
                      </span>
                    )}
                  </span>
                  <div className="msg-body">
                    {editing?.index === index ? (
                      <div className="msg-edit-form">
                        <textarea aria-label="Edit message" rows={4} value={editing.text}
                                  onChange={(e) => setEditing({ index, text: e.target.value })} />
                        <div className="form-actions">
                          <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                          <button className="primary" onClick={saveEdit}>Save</button>
                        </div>
                      </div>
                    ) : (
                      <RenderedMarkdown content={m.content} />
                    )}
                  </div>
                </div>
              ))}
            </div>
          ))}
          {directorNote && busy && (
            <div className="run director-note">
              <div className="msg assistant">
                <span className="msg-gutter" />
                <div className="msg-body">🎬 {directorNote}</div>
              </div>
            </div>
          )}
          {streaming && (
            <div className="run">
              {(messages.length === 0 ||
                speakerOf(messages[messages.length - 1]) !== labels.assistant) && (
                <div className="plate">
                  <span className="plate-avatar"><Portrait src={null} name={labels.assistant} /></span>
                  <span className="plate-name">{labels.assistant}</span>
                  <span className="role-chip">npc</span>
                </div>
              )}
              <div className="msg assistant">
                <span className="msg-gutter" />
                <div className="msg-body">
                  <RenderedMarkdown content={streaming} />
                  <span className="cursor" />
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder={activePcless ? "Direct the scene (optional)…" : "Speak your intent…"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : !input.trim() ? "Continue ▶" : "Send ▸"}
          </button>
        </div>
      </section>
      {activeId && (
        <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                        onSceneChanged={() => selectScene(activeId)}
                        onSceneRenamed={sceneRenamed} pcless={activePcless} />
      )}
      {drawer && activeId && (
        <RecordDrawer cid={cid} sid={activeId} target={drawer} onClose={() => setDrawer(null)} />
      )}
      {chooserOpen && (
        <NewSceneChooser cid={cid} afterSid={activeId} keySet={keySet}
                         onClose={() => setChooserOpen(false)} onCreated={sceneCreated} />
      )}
      </div>
    </div>
  );
}
