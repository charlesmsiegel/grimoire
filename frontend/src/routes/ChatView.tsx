import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ConvMeta, type Message } from "../api/client";

export default function ChatView({ keySet }: { keySet: boolean }) {
  const [convs, setConvs] = useState<ConvMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.listConversations().then((list) => {
      setConvs(list);
      if (list.length && !activeId) selectConv(list[0].id);
    });
  }, []);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  async function selectConv(id: string) {
    setActiveId(id);
    const conv = await api.getConversation(id);
    setMessages(conv.messages);
    setStreaming("");
  }

  async function newConversation() {
    const { id } = await api.createConversation();
    setConvs(await api.listConversations());
    selectConv(id);
  }

  async function send() {
    if (!input.trim() || busy) return;
    let id = activeId;
    if (!id) {
      id = (await api.createConversation()).id;
      setConvs(await api.listConversations());
      setActiveId(id);
    }
    const content = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content }]);
    setBusy(true);
    setError(null);
    let acc = "";
    try {
      await api.chat(id, content, (e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
      setMessages((m) => [...m, { role: "assistant", content: acc }]);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <button onClick={newConversation}>+ New conversation</button>
        {convs.map((c) => (
          <div
            key={c.id}
            className={"conv-item" + (c.id === activeId ? " active" : "")}
            onClick={() => selectConv(c.id)}
          >
            {c.title}
          </div>
        ))}
      </aside>
      <section className="main">
        {!keySet && (
          <div className="banner">
            No OpenRouter key set. <Link to="/config">Set your key in Config</Link>.
          </div>
        )}
        {error && <div className="banner">{error}</div>}
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
            placeholder="Speak your intent…  (Ctrl/Cmd+Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </section>
    </div>
  );
}
