import { memo, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, type SceneMeta, type Message, type SceneAbsorb, type SceneDatetime, type StagedEdit,
} from "../api/client";
import type { ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";
import { CastPanel } from "../components/CastPanel";
import { ChangesPanel } from "../components/ChangesPanel";
import { CalendarConfig } from "../components/CalendarConfig";
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
  const [colorQuotes, setColorQuotes] = useState(false);
  const [labels, setLabels] = useState({ user: "You", assistant: "Grimoire" });
  const [showChanges, setShowChanges] = useState(false);
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  const [absorbing, setAbsorbing] = useState(false);
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      setName(c.meta.name);
      api.getWorld(c.meta.world).then((w) => setWorldName(w.meta.name)).catch(() => setWorldName(""));
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

  async function selectScene(id: string) {
    setActiveId(id);
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    const scene = await api.getScene(cid, id);
    setMessages(scene.messages);
    setStreaming("");
    setCtxKey((n) => n + 1);
  }

  async function newScene() {
    const { id } = await api.createScene(cid);
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  async function renameScene(id: string, title: string) {
    const { id: newId } = await api.renameScene(cid, id, title);
    if (activeId === id) setActiveId(newId);
    setScenes(await api.listScenes(cid));
  }

  // the first date set renames the scene file — re-list and adopt the new id
  async function sceneRenamed(id: string) {
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

  async function runStream(start: (onEvent: (e: ChatEvent) => void) => Promise<void>) {
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
      if (acc) setMessages((m) => [...m, { role: "assistant", content: acc }]);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
      setCtxKey((n) => n + 1);
    }
  }

  async function send() {
    if (!input.trim() || busy) return;
    let id = activeId;
    if (!id) {
      id = (await api.createScene(cid)).id;
      setScenes(await api.listScenes(cid));
      setActiveId(id);
    }
    const content = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content }]);
    await runStream((onEvent) => api.chat(cid, id!, content, onEvent));
  }

  async function saveEdit() {
    if (!editing || !activeId) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    await selectScene(activeId);
  }

  async function retry() {
    if (!activeId || busy) return;
    await runStream((onEvent) => api.retry(cid, activeId, onEvent));
  }

  async function reroll() {
    if (!activeId || busy) return;
    setMessages((m) => m.slice(0, -1));
    await runStream((onEvent) => api.regenerate(cid, activeId, onEvent));
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
            sceneEmpty={true}
            keySet={keySet}
            onSeeded={() => selectScene(activeId)}
            onSceneRenamed={sceneRenamed}
          />
        )}
        {activeId && (
          <h2 className="scene-title">{scenes.find((s) => s.id === activeId)?.title ?? ""}</h2>
        )}
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}>
          {messages.map((m, i) => (
            <div className={`msg ${m.role}`} key={i}>
              <span className="spine">{m.speaker ?? labels[m.role]}</span>
              <div className="msg-body">
                {editing?.index === i ? (
                  <div className="msg-edit-form">
                    <textarea aria-label="Edit message" rows={4} value={editing.text}
                              onChange={(e) => setEditing({ index: i, text: e.target.value })} />
                    <div className="form-actions">
                      <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                      <button className="primary" onClick={saveEdit}>Save</button>
                    </div>
                  </div>
                ) : (
                  <RenderedMarkdown content={m.content} />
                )}
              </div>
              {editing?.index !== i && !busy && (
                <span className="msg-actions">
                  {m.role === "assistant" && i === messages.length - 1 && i > 0 && (
                    <button className="msg-edit" onClick={reroll}>Reroll</button>
                  )}
                  <button className="msg-edit" aria-label={`Edit message ${i + 1}`} title="Edit"
                          onClick={() => setEditing({ index: i, text: m.content })}>✎</button>
                </span>
              )}
            </div>
          ))}
          {streaming && (
            <div className="msg assistant">
              <span className="spine">{labels.assistant}</span>
              <div className="msg-body">
                <RenderedMarkdown content={streaming} />
                <span className="cursor" />
              </div>
            </div>
          )}
        </div>
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder="Speak your intent…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : "Send ▸"}
          </button>
        </div>
      </section>
      {activeId && (
        <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                        onSceneChanged={() => selectScene(activeId)}
                        onSceneRenamed={sceneRenamed} />
      )}
      </div>
    </div>
  );
}
