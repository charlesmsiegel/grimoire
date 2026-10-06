import { useEffect, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, ApiError, type QuickReply, type QuickReplyDraft, type QuickReplyEntry, type QuickReplyKind,
  type QuickReplyMode, type QuickReplyScope, type QuickReplySet, type QuickReplyTask,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "./Field";
import { isHide, moveEntry, quickReplyTitle, removeEntry, upsertEntry } from "./quickReplies";

const KIND_LABEL: Record<QuickReplyKind, string> = {
  send: "Speak", direct: "Direct", roll: "Roll", task: "Task", opener: "Opener",
};
const MODE_LABEL: Record<QuickReplyMode, string> = {
  send: "Send at once", insert: "Put in the composer",
};
const TASK_LABEL: Record<QuickReplyTask, string> = {
  rolling_summary: "Refresh summary", scene_break: "Ask about a scene break", next_scene: "Next scene",
};

const CHANGED_ELSEWHERE =
  "This set changed elsewhere and has been re-read — save again to apply your change.";

type Form = {
  label: string; kind: QuickReplyKind; text: string; mode: QuickReplyMode;
  notation: string; roll_label: string; task: QuickReplyTask;
};

const EMPTY_FORM: Form = {
  label: "", kind: "send", text: "", mode: "send", notation: "", roll_label: "", task: "rolling_summary",
};

function formOf(r: QuickReply): Form {
  return {
    label: r.label, kind: r.kind, text: r.text ?? "", mode: r.mode ?? "send",
    notation: r.notation ?? "", roll_label: r.roll_label ?? "", task: r.task ?? "rolling_summary",
  };
}

/** Only the kind's own fields: a reply switched from Speak to Roll does not
 *  carry its old text along. */
function entryOf(form: Form, id: string | undefined): QuickReplyDraft {
  const base: QuickReplyDraft = { ...(id ? { id } : {}), label: form.label.trim(), kind: form.kind };
  switch (form.kind) {
    case "send":
    case "direct":
      return { ...base, text: form.text, mode: form.mode };
    case "roll":
      return { ...base, notation: form.notation.trim(),
               ...(form.roll_label.trim() ? { roll_label: form.roll_label.trim() } : {}) };
    case "task":
      return { ...base, task: form.task };
    case "opener":
      return base;
  }
}

function savable(form: Form): boolean {
  if (!form.label.trim()) return false;
  if (form.kind === "send" || form.kind === "direct") return !!form.text.trim();
  if (form.kind === "roll") return !!form.notation.trim();
  return true;
}

/** A world's or a campaign's quick replies, as a list/detail editor (CLAUDE.md).
 *
 *  Order is part of the contract -- it is the order the composer shows -- so the
 *  rail reorders with ↑/↓. Every change is the whole set PUT back with the
 *  digest it was read at; a set that moved since is re-read rather than
 *  overwritten, and the draft in the form survives that. */
export function QuickReplyEditor({ scope }: { scope: QuickReplyScope }) {
  const [set, setSet] = useState<QuickReplySet | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  // The id the form is editing, or null for a new reply.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);

  useEffect(() => {
    let live = true;
    api.getQuickReplies(scope)
      .then((s) => { if (live) setSet(s); })
      .catch((err: unknown) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
    // The scope's identity is its id; the parent keys the editor on it anyway.
  }, [scope.kind, scope.kind === "world" ? scope.wid : scope.cid]);   // eslint-disable-line react-hooks/exhaustive-deps

  const entries: QuickReplyEntry[] = set?.replies ?? [];
  const own: QuickReply[] = entries.filter((e): e is QuickReply => !isHide(e));
  const current = own.find((r) => r.id === selected) ?? null;

  /** PUT `next`; resolves to the stored set, or null when it was refused (the
   *  refusal is on screen by then). */
  async function write(next: (QuickReplyEntry | QuickReplyDraft)[]): Promise<QuickReplySet | null> {
    if (!set) return null;
    setWriting(true);
    setError(null);
    try {
      const saved = await api.setQuickReplies(scope, next, set.digest);
      setSet(saved);
      return saved;
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409 && err.kind === "set_changed") {
        try {
          setSet(await api.getQuickReplies(scope));
          setError(CHANGED_ELSEWHERE);
        } catch (reread: unknown) {
          setError(errorText(reread));
        }
      } else {
        setError(errorText(err));
      }
      return null;
    } finally {
      setWriting(false);
    }
  }

  function open(id: string) {
    setSelected(id);
    setMode("view");
    setError(null);
  }

  function startNew() {
    setSelected(null);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setMode("edit");
    setError(null);
  }

  function startEdit(r: QuickReply) {
    setEditingId(r.id);
    setForm(formOf(r));
    setMode("edit");
    setError(null);
  }

  async function save() {
    const before = new Set(entries.map((e) => e.id));
    const entry = entryOf(form, editingId ?? undefined);
    const saved = await write(upsertEntry<QuickReplyEntry | QuickReplyDraft>(
      entries, entry, editingId ?? undefined));
    if (!saved) return;
    const id = editingId ?? saved.replies.find((e) => !before.has(e.id))?.id ?? null;
    setSelected(id);
    setMode("view");
  }

  async function remove() {
    if (!editingId) return;
    const saved = await write(removeEntry(entries, editingId));
    if (!saved) return;
    setSelected(null);
    setEditingId(null);
    setMode("view");
  }

  function move(id: string, delta: -1 | 1) {
    void write(moveEntry(entries, id, delta));
  }

  function cancel() {
    setMode("view");
    setError(null);
    if (!editingId) setSelected(null);
  }

  function row(r: QuickReply, i: number, list: QuickReply[]) {
    return (
      <div key={r.id} className="quick-reply-row">
        <button type="button" className={"row" + (selected === r.id ? " active" : "")}
                onClick={() => open(r.id)}>
          {r.label}
        </button>
        <button type="button" className="subtle quick-reply-move" aria-label={`Move ${r.label} up`}
                disabled={writing || i === 0} onClick={() => move(r.id, -1)}>↑</button>
        <button type="button" className="subtle quick-reply-move" aria-label={`Move ${r.label} down`}
                disabled={writing || i === list.length - 1} onClick={() => move(r.id, 1)}>↓</button>
      </div>
    );
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button type="button" className="primary new" onClick={startNew}>+ New quick reply</button>
        {own.map((r, i) => row(r, i, own))}
        {set && own.length === 0 && <div className="field-hint">No quick replies yet.</div>}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" ? (
          current ? (
            <ReplyView reply={current} actions={
              <button className="subtle" onClick={() => startEdit(current)}>Edit</button>
            } />
          ) : (
            <div className="field-hint">
              Pick a quick reply, or add one. They appear above the composer, in this order.
            </div>
          )
        ) : (
          <div className="form">
            <h3>{editingId ? "Edit quick reply" : "New quick reply"}</h3>
            <Field label="Label" hint="what the button says">
              <input type="text" maxLength={40} value={form.label}
                     onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </Field>
            <Field label="Kind">
              <select value={form.kind}
                      onChange={(e) => setForm({ ...form, kind: e.target.value as QuickReplyKind })}>
                {(Object.keys(KIND_LABEL) as QuickReplyKind[]).map((k) => (
                  <option key={k} value={k}>{KIND_LABEL[k]}</option>
                ))}
              </select>
            </Field>
            {(form.kind === "send" || form.kind === "direct") && (
              <>
                <Field label="Text" hint={form.kind === "direct"
                  ? "sent as a director note: it steers the reply and is never posted"
                  : "posted as your character"}>
                  <textarea rows={4} maxLength={2000} value={form.text}
                            onChange={(e) => setForm({ ...form, text: e.target.value })} />
                </Field>
                <Field label="Mode">
                  <select value={form.mode}
                          onChange={(e) => setForm({ ...form, mode: e.target.value as QuickReplyMode })}>
                    {(Object.keys(MODE_LABEL) as QuickReplyMode[]).map((m) => (
                      <option key={m} value={m}>{MODE_LABEL[m]}</option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            {form.kind === "roll" && (
              <>
                <Field label="Notation" hint="2d6+3, 4d6kh3, 7d10t6, 1d20+5 vs 15…">
                  <input type="text" value={form.notation}
                         onChange={(e) => setForm({ ...form, notation: e.target.value })} />
                </Field>
                <Field label="Roll label" hint="optional; what the transcript calls the roll">
                  <input type="text" maxLength={80} value={form.roll_label}
                         onChange={(e) => setForm({ ...form, roll_label: e.target.value })} />
                </Field>
              </>
            )}
            {form.kind === "task" && (
              <Field label="Task">
                <select value={form.task}
                        onChange={(e) => setForm({ ...form, task: e.target.value as QuickReplyTask })}>
                  {(Object.keys(TASK_LABEL) as QuickReplyTask[]).map((t) => (
                    <option key={t} value={t}>{TASK_LABEL[t]}</option>
                  ))}
                </select>
              </Field>
            )}
            <div className="form-actions">
              {editingId && (
                <button className="subtle" disabled={writing} onClick={() => void remove()}>Delete</button>
              )}
              <button className="subtle" onClick={cancel}>Cancel</button>
              <button className="primary" disabled={writing || !set || !savable(form)}
                      onClick={() => void save()}>Save</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** One reply, read-only: its text (or what it does) and its attributes. */
function ReplyView({ reply, actions, note }: {
  reply: QuickReply; actions: ReactNode; note?: string;
}) {
  const textual = reply.kind === "send" || reply.kind === "direct";
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>{reply.label}</h3>
        <div className="detail-rendered">
          {textual
            ? <Markdown remarkPlugins={[remarkGfm]}>{reply.text ?? ""}</Markdown>
            : <p>{quickReplyTitle(reply)}</p>}
        </div>
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">{actions}</div>
        {note && <div className="side-section"><div className="field-hint">{note}</div></div>}
        <div className="side-section">
          <h4>Kind</h4>
          <span className="chip on">{KIND_LABEL[reply.kind]}</span>
        </div>
        {textual && reply.mode && (
          <div className="side-section">
            <h4>Mode</h4>
            <span className="chip on">{MODE_LABEL[reply.mode]}</span>
          </div>
        )}
        {reply.kind === "roll" && (
          <div className="side-section">
            <h4>Notation</h4>
            <span className="chip on">{reply.notation}</span>
          </div>
        )}
        {reply.kind === "roll" && reply.roll_label && (
          <div className="side-section">
            <h4>Roll label</h4>
            <span className="chip on">{reply.roll_label}</span>
          </div>
        )}
        {reply.kind === "task" && reply.task && (
          <div className="side-section">
            <h4>Task</h4>
            <span className="chip on">{TASK_LABEL[reply.task]}</span>
          </div>
        )}
      </aside>
    </div>
  );
}
