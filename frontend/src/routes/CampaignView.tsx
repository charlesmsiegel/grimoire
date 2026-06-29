import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type SceneMeta, type Message } from "../api/client";
import type { ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";
import { CastPanel } from "../components/CastPanel";
import { SceneInspector } from "../components/SceneInspector";

export default function CampaignView({ keySet }: { keySet: boolean }) {
  const { cid = "" } = useParams();
  const [name, setName] = useState("");
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ctxKey, setCtxKey] = useState(0);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => setName(c.meta.name));
    api.listScenes(cid).then((list) => {
      setScenes(list);
      if (list.length) selectScene(list[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  async function selectScene(id: string) {
    setActiveId(id);
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

  async function retry() {
    if (!activeId || busy) return;
    await runStream((onEvent) => api.retry(cid, activeId, onEvent));
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <Link to="/" className="back-link">‹ Campaigns</Link>
        <button onClick={newScene}>+ New scene</button>
        {scenes.map((s) => (
          <EditableRow
            key={s.id}
            label={s.title}
            active={s.id === activeId}
            onSelect={() => selectScene(s.id)}
            onRename={(title) => renameScene(s.id, title)}
            onDelete={() => deleteScene(s)}
          />
        ))}
      </aside>
      <section className="main">
        <div className="campaign-header">{name}</div>
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
        {activeId && (
          <CastPanel
            cid={cid}
            sid={activeId}
            sceneEmpty={messages.length === 0}
            keySet={keySet}
            onSeeded={() => selectScene(activeId)}
          />
        )}
        <div className="stream" ref={streamRef}>
          {messages.map((m, i) => (
            <div className="msg" key={i}>
              <div className="role">{m.role === "user" ? "You" : "Grimoire"}</div>
              <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown>
            </div>
          ))}
          {streaming && (
            <div className="msg">
              <div className="role">Grimoire</div>
              <Markdown remarkPlugins={[remarkGfm]}>{streaming}</Markdown>
              <span className="cursor" />
            </div>
          )}
        </div>
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder="Speak your intent…  (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </section>
      {activeId && <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey} />}
    </div>
  );
}
