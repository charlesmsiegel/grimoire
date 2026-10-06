import { useEffect, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, ApiError, type QuickReply, type QuickReplyDraft, type QuickReplyEntry, type QuickReplyKind,
  type QuickReplyMode, type QuickReplyScope, type QuickReplySet, type QuickReplyTask,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "./Field";
import {
  hideEntry, isHide, moveEntry, overrideOf, quickReplyTitle, removeEntry, upsertEntry,
} from "./quickReplies";

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
  // A campaign lists two kinds of row -- its own replies and its world's -- and
  // an override shares its world reply's id, so a selection says which list.
  const [selected, setSelected] = useState<{ id: string; from: "own" | "world" } | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  // What the form saves: `replaceId` is the stored entry it replaces (absent
  // for a new one), `id` the id it is saved under (absent: the server mints
  // one; a world reply's id: an override).
  const [editing, setEditing] = useState<{ replaceId?: string; id?: string }>({});
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

  const campaign = scope.kind === "campaign";
  const entries: QuickReplyEntry[] = set?.replies ?? [];
  const inherited: QuickReply[] = set?.inherited ?? [];
  const inheritedIds = new Set(inherited.map((r) => r.id));
  const hidden = new Set(entries.filter(isHide).map((e) => e.id));
  const replies: QuickReply[] = entries.filter((e): e is QuickReply => !isHide(e));
  const overrides = new Map(replies.filter((r) => inheritedIds.has(r.id)).map((r) => [r.id, r]));
  // The set's own rows: everything but hides and overrides, which are listed
  // under the world reply they act on. Reordering moves within these only.
  const isMine = (e: QuickReplyEntry | QuickReplyDraft) =>
    !isHide(e as QuickReplyEntry) && !(e.id !== undefined && inheritedIds.has(e.id));
  const mine: QuickReply[] = replies.filter(isMine);

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

  function open(id: string, from: "own" | "world") {
    setSelected({ id, from });
    setMode("view");
    setError(null);
  }

  function startNew() {
    setSelected(null);
    setEditing({});
    setForm(EMPTY_FORM);
    setMode("edit");
    setError(null);
  }

  function startEdit(r: QuickReply) {
    setEditing({ replaceId: r.id, id: r.id });
    setForm(formOf(r));
    setMode("edit");
    setError(null);
  }

  /** A campaign's own copy of a world reply, saved under the world reply's id
   *  -- which is what makes it replace that reply rather than sit beside it. */
  function startOverride(w: QuickReply) {
    setEditing({ id: w.id });
    setForm(formOf(overrideOf(w)));
    setMode("edit");
    setError(null);
  }

  async function save() {
    const before = new Set(entries.map((e) => e.id));
    const entry = entryOf(form, editing.id);
    const saved = await write(upsertEntry<QuickReplyEntry | QuickReplyDraft>(
      entries, entry, editing.replaceId));
    if (!saved) return;
    // An override stays selected under its world row; anything else is one of
    // the set's own -- a new one under the id the server gave it.
    if (selected?.from !== "world") {
      const id = editing.id ?? saved.replies.find((e) => !before.has(e.id))?.id;
      setSelected(id ? { id, from: "own" } : null);
    }
    setMode("view");
  }

  async function remove() {
    if (!editing.replaceId) return;
    const saved = await write(removeEntry(entries, editing.replaceId));
    if (!saved) return;
    // Deleting an override brings the world reply back, still selected.
    if (selected?.from !== "world") setSelected(null);
    setEditing({});
    setMode("view");
  }

  function move(id: string, delta: -1 | 1) {
    void write(moveEntry<QuickReplyEntry>(entries, id, delta, isMine));
  }

  function cancel() {
    setMode("view");
    setError(null);
  }

  /** What the body shows for the selection, in view mode. */
  function viewOf(): ReactNode {
    if (!selected) return null;
    if (selected.from === "own") {
      const r = mine.find((x) => x.id === selected.id);
      return r ? (
        <ReplyView reply={r} actions={
          <button className="subtle" onClick={() => startEdit(r)}>Edit</button>
        } />
      ) : null;
    }
    const w = inherited.find((x) => x.id === selected.id);
    if (!w) return null;
    const override = overrides.get(w.id);
    if (override) {
      return (
        <ReplyView reply={override} note={`Replaces the world's “${w.label}” in this campaign.`}
                   actions={<button className="subtle" onClick={() => startEdit(override)}>Edit</button>} />
      );
    }
    if (hidden.has(w.id)) {
      return (
        <ReplyView reply={w} note="From the world, hidden in this campaign." actions={
          <button className="subtle" disabled={writing}
                  onClick={() => void write(removeEntry(entries, w.id))}>Show</button>
        } />
      );
    }
    return (
      <ReplyView reply={w} note="From the world. Override it to change it here, or hide it."
                 actions={<>
                   <button className="subtle" onClick={() => startOverride(w)}>Override</button>
                   <button className="subtle" disabled={writing}
                           onClick={() => void write(hideEntry<QuickReplyEntry>(entries, w.id))}>Hide</button>
                 </>} />
    );
  }

  function row(r: QuickReply, i: number, list: QuickReply[]) {
    return (
      <div key={r.id} className="quick-reply-row">
        <button type="button"
                className={"row" + (selected?.from === "own" && selected.id === r.id ? " active" : "")}
                onClick={() => open(r.id, "own")}>
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
        {campaign && <div className="rail-heading">This campaign</div>}
        {mine.map((r, i) => row(r, i, mine))}
        {set && mine.length === 0 && (
          <div className="field-hint">{campaign ? "None of this campaign's own yet." : "No quick replies yet."}</div>
        )}
        {campaign && inherited.length > 0 && (
          <>
            <div className="rail-heading">From the world</div>
            {inherited.map((w) => (
              <button key={w.id} type="button"
                      className={"row" + (selected?.from === "world" && selected.id === w.id ? " active" : "")}
                      onClick={() => open(w.id, "world")}>
                {w.label}
                {hidden.has(w.id) && <span className="chip">hidden</span>}
                {overrides.has(w.id) && <span className="chip">overridden</span>}
              </button>
            ))}
          </>
        )}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" ? (
          viewOf() ?? (
            <div className="field-hint">
              Pick a quick reply, or add one. They appear above the composer, in this order.
            </div>
          )
        ) : (
          <div className="form">
            <h3>{editing.replaceId ? "Edit quick reply" : editing.id ? "Override for this campaign" : "New quick reply"}</h3>
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
              {editing.replaceId && (
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
