/** The ledger's Knowledge section (roadmap 11 §7.4): the user's word on who
 *  knows, suspects or never heard what happened in a scene.
 *
 *  The ledger owns the screen, so the statements are rows in its context
 *  column (`KnowledgeRows`) and the detail is here in main (`KnowledgePane`):
 *  read-only by default, with an explicit Edit step, as every record page.
 *
 *  One rule shapes the form more than any other: a grant is only ever stored
 *  about text the reader was shown. Every post is listed with the fingerprint
 *  the server computed for the text on screen, and a `knows`/`suspects` save
 *  sends those back as `expect` -- so a swipe or an edit landing in between is
 *  refused (409 `moved`) rather than granting text nobody read. The same holds
 *  for "Confirm against current text" on a statement whose text moved. */
import { useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import {
  api, type CharacterSummary, type EntitySummary, type KnowledgeEdit, type KnowledgeEntry,
  type KnowledgeList, type KnowledgeSave, type KnowledgeScene, type KnowledgeStatus,
  type SceneMeta,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { encodeSegment } from "../../urlSegment";
import { sectionHref } from "../../worldPaths";
import { Field } from "../Field";

const STATUS_LABEL: Record<KnowledgeStatus, string> = {
  knows: "knows", suspects: "suspects", unaware: "never heard",
};

const STALE_HINT: Record<string, string> = {
  changed: "The text changed since this was written, so it no longer grants anything. "
    + "Confirm it against the current text, or remove it.",
  hidden: "A post it names is hidden from context, so it reaches no one and grants nothing.",
  gone: "It points at a deleted scene or post, so it no longer applies. Remove it.",
  audience: "The audience was renamed or no longer exists, so it grants nothing. If it is "
    + "still the same one, confirm it under its current name.",
  unverifiable: "Its scene could not be read just now (a synced folder mid-write?), so it is "
    + "not counted until it can be. Nothing needs doing; look again shortly.",
};

/** Who the statement is about, by its current name where it still has one. */
export function audienceLabel(e: KnowledgeEntry): string {
  if (e.audience === "*") return "Everyone";
  return e.audience_now ?? e.audience_name;
}

/** One column row's text: "Mara knows · Pier". */
export function knowledgeRowLabel(e: KnowledgeEntry): string {
  return `${audienceLabel(e)} ${STATUS_LABEL[e.status]}`;
}

/** The statements, re-read whenever the ledger's epoch moves. */
export function useKnowledge(cid: string, epoch: number, enabled = true) {
  const [state, setState] = useState<{ cid: string; data: KnowledgeList | null;
                                       error: string | null } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    api.ledgerKnowledge(cid)
      .then((data) => { if (live) setState({ cid, data, error: null }); })
      .catch((err: unknown) => { if (live) setState({ cid, data: null, error: errorText(err) }); });
    return () => { live = false; };
  }, [cid, epoch, enabled]);
  return state && state.cid === cid ? state : null;
}

/** The context column's rows: `+ New statement`, then one per statement. */
export function KnowledgeRows({ entries, selected, onNew, onOpen }: {
  entries: KnowledgeEntry[] | null; selected: string | null;
  onNew: () => void; onOpen: (kid: string) => void;
}) {
  return (
    <>
      <button className={"column-row" + (selected === "new" ? " active" : "")} onClick={onNew}>
        <span className="column-row-label">+ New statement</span>
      </button>
      {entries === null && <p className="column-empty">Reading…</p>}
      {entries?.map((e) => (
        <button key={e.id} className={"column-row" + (selected === e.id ? " active" : "")}
                onClick={() => onOpen(e.id)}>
          <span className="column-row-label">{knowledgeRowLabel(e)}</span>
          <span className="column-row-count">
            {e.stale ? "stale" : e.scene ? e.scene.title : "—"}
          </span>
        </button>
      ))}
    </>
  );
}

type Form = {
  audience: string; status: KnowledgeStatus; scene: string; whole: boolean;
  posts: string[]; note: string;
};

const slot = (key: string, part: string) => `${key}#${part}`;

function formOf(e: KnowledgeEntry | null): Form {
  if (!e) return { audience: "", status: "knows", scene: "", whole: false, posts: [], note: "" };
  return {
    audience: e.audience, status: e.status, scene: e.scene?.sid ?? "",
    whole: e.subject.kind === "scene",
    posts: e.subject.kind === "posts" ? e.subject.posts.map((p) => slot(p.key, p.part)) : [],
    note: e.note,
  };
}

export function KnowledgePane({ cid, entry, creating, onSaved, onDeleted }: {
  cid: string;
  /** The statement on screen, or null with `creating`. */
  entry: KnowledgeEntry | null;
  creating: boolean;
  onSaved: (kid: string) => void;
  onDeleted: () => void;
}) {
  const [mode, setMode] = useState<"view" | "edit">(creating ? "edit" : "view");
  const [form, setForm] = useState<Form>(() => formOf(entry));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [groups, setGroups] = useState<EntitySummary[]>([]);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [shown, setShown] = useState<KnowledgeScene | null>(null);

  // A different record, or a new one: back to its own view and its own form.
  useEffect(() => {
    setMode(creating ? "edit" : "view");
    setForm(formOf(entry));
    setError(null);
  }, [entry?.id, creating]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode !== "edit") return;
    const scope = { kind: "campaign" as const, id: cid };
    api.listCharacters(scope).then(setChars).catch(() => setChars([]));
    api.listEntities(scope, "groups").then(setGroups).catch(() => setGroups([]));
    api.listScenes(cid).then(setScenes).catch(() => setScenes([]));
  }, [cid, mode]);

  // The posts the form can name, with the fingerprints a grant sends back.
  useEffect(() => {
    if (mode !== "edit" || !form.scene) { setShown(null); return; }
    let live = true;
    api.ledgerKnowledgeScene(cid, form.scene)
      .then((s) => { if (live) setShown(s); })
      .catch((err: unknown) => { if (live) { setShown(null); setError(errorText(err)); } });
    return () => { live = false; };
  }, [cid, form.scene, mode]);

  const original = useMemo(() => formOf(entry), [entry]);
  const sameSubject = form.scene === original.scene && form.whole === original.whole
    && [...form.posts].sort().join() === [...original.posts].sort().join();

  async function write(run: () => Promise<unknown>, kid?: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await run();
      const id = kid ?? (out as { id?: string }).id ?? "";
      setMode("view");
      onSaved(id);
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function expectFor(keys: string[]) {
    return (shown?.posts ?? []).filter((p) => keys.includes(slot(p.key, p.part)))
      .map((p) => ({ key: p.key, part: p.part, variant: p.variant, digest: p.digest }));
  }

  function subject() {
    const posts = form.whole ? null : (shown?.posts ?? [])
      .filter((p) => form.posts.includes(slot(p.key, p.part)))
      .map((p) => ({ key: p.key, part: p.part }));
    return {
      scene: form.scene, posts, whole_scene: form.whole,
      expect: form.whole ? null : expectFor(form.posts),
      expect_digest: form.whole ? shown?.digest ?? "" : "",
    };
  }

  function save() {
    if (!entry) {
      const body: KnowledgeSave = { audience: form.audience, status: form.status,
                                    note: form.note, ...subject() };
      void write(() => api.ledgerCreateKnowledge(cid, body));
      return;
    }
    // Only what moved: a note edit must never re-fingerprint the subject, or
    // it would confirm a stale grant behind the reader's back.
    const body: KnowledgeEdit = {};
    if (form.audience !== original.audience) body.audience = form.audience;
    if (form.status !== original.status) body.status = form.status;
    if (form.note !== original.note) body.note = form.note;
    // A subject moves when the reader changed it -- and when an `unaware` is
    // becoming a grant, which the server accepts only with the posts named
    // again (and fingerprinted as shown), since a keyless part's `unaware` is
    // stored about the whole scene.
    if (!sameSubject || (original.status === "unaware" && form.status !== "unaware")) {
      Object.assign(body, subject());
    }
    void write(() => api.ledgerSaveKnowledge(cid, entry.id, body), entry.id);
  }

  function confirmAudience(e: KnowledgeEntry) {
    // Records the audience's current name again: the reader is saying it is
    // still the same character or group.
    void write(() => api.ledgerSaveKnowledge(cid, e.id, { audience: e.audience }), e.id);
  }

  function restamp(e: KnowledgeEntry) {
    // Exactly the text this view shows: the list read's own fingerprints.
    const body: KnowledgeEdit = e.subject.kind === "scene"
      ? { restamp: true, expect_digest: e.scene?.digest ?? "" }
      : { restamp: true,
          expect: e.posts.map((p) => ({ key: p.key, part: p.part, variant: p.variant ?? "",
                                        digest: p.digest ?? "" })) };
    void write(() => api.ledgerSaveKnowledge(cid, e.id, body), e.id);
  }

  function remove(e: KnowledgeEntry) {
    if (busy) return;
    setBusy(true);
    setError(null);
    api.ledgerDeleteKnowledge(cid, e.id)
      .then(() => onDeleted())
      .catch((err: unknown) => setError(errorText(err)))
      .finally(() => setBusy(false));
  }

  if (mode === "view" && entry) {
    const scope = { kind: "campaign" as const, id: cid };
    const [kind, rid] = entry.audience.split(":");
    const audienceHref = entry.audience === "*" || !entry.audience_now ? null
      : sectionHref(scope, { kind: "record", at: kind === "groups" ? "groups" : "characters",
                             rid: rid ?? "" });
    const body = entry.subject.kind === "scene"
      ? `The whole scene${entry.scene ? ` *${entry.scene.title}*` : ""}.`
      : entry.posts.map((p) => p.missing ? "*A post no longer in the scene.*"
        : `**${p.speaker || "—"}:** ${p.text}`).join("\n\n");
    return (
      <div className="detail-view">
        <div className="detail-main">
          <h3>{knowledgeRowLabel(entry)}</h3>
          {error && <div className="banner">{error}</div>}
          <div className="detail-rendered">
            <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
          </div>
          {entry.note && (
            <div className="detail-rendered">
              <h4>Note</h4>
              <Markdown remarkPlugins={[remarkGfm]}>{entry.note}</Markdown>
            </div>
          )}
        </div>
        <aside className="detail-sidebar">
          <div className="form-actions">
            <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
          </div>
          <div className="side-section">
            <h4>Audience</h4>
            <div className="chips">
              {audienceHref
                ? <Link className="chip on" to={audienceHref}>{audienceLabel(entry)}</Link>
                : <span className="chip on">{audienceLabel(entry)}</span>}
            </div>
            {entry.audience.startsWith("groups:") && (
              <p className="field-hint">
                Reaches the group's leader and members as of now: someone who joins later is
                covered, and a world-level membership edit changes it for every campaign of
                that world.
              </p>
            )}
          </div>
          <div className="side-section">
            <h4>Scene</h4>
            {entry.scene ? (
              <div className="chips">
                <Link className="chip on"
                      to={`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(entry.scene.sid)}`}>
                  {entry.scene.title}
                </Link>
              </div>
            ) : (
              <p className="field-hint">
                {entry.unverifiable ? "The scene could not be read just now."
                  : "Points at a deleted scene."}
              </p>
            )}
          </div>
          <div className="side-section">
            <h4>Status</h4>
            <div className="chips">
              <span className="chip on">{STATUS_LABEL[entry.status]}</span>
              <span className="chip on">{entry.source === "accepted" ? "accepted" : "by hand"}</span>
            </div>
          </div>
          {entry.stale && (
            <div className="side-section">
              <h4>{entry.status === "unaware" ? "No longer applies" : "No longer grants"}</h4>
              <p className="field-hint">{STALE_HINT[entry.stale_reason ?? "changed"]}</p>
              <div className="form-actions">
                {entry.stale_reason === "changed" && (
                  <button className="subtle" disabled={busy} onClick={() => restamp(entry)}>
                    Confirm against current text
                  </button>
                )}
                {entry.stale_reason === "audience" && entry.audience_now && (
                  <button className="subtle" disabled={busy} onClick={() => confirmAudience(entry)}>
                    {`Confirm it is still ${entry.audience_now}`}
                  </button>
                )}
                {(entry.stale_reason === "gone" || entry.stale_reason === "hidden") && (
                  <button className="subtle" disabled={busy} onClick={() => remove(entry)}>
                    Remove
                  </button>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    );
  }

  const grant = form.status !== "unaware";
  const flipToGrant = !!entry && original.status === "unaware" && grant;
  // An edit that leaves the subject alone needs no scene read -- so the note
  // of a statement about a deleted scene can still be corrected.
  // An edit that changes nothing has nothing to save.
  const dirty = !entry || !sameSubject || flipToGrant || form.audience !== original.audience
    || form.status !== original.status || form.note !== original.note;
  const ready = dirty && !!form.audience && (entry && sameSubject && !flipToGrant
    || (!!form.scene && !!shown && (form.whole || form.posts.length > 0)));
  return (
    <div className="form">
      <h3>{entry ? "Edit statement" : "New statement"}</h3>
      {error && <div className="banner">{error}</div>}
      <Field label="Who">
        <select aria-label="Who" value={form.audience}
                onChange={(e) => setForm({ ...form, audience: e.target.value })}>
          <option value="">— choose —</option>
          <option value="*">Everyone (except whoever wrote the post)</option>
          {chars.map((c) => <option key={c.id} value={`characters:${c.id}`}>{c.name}</option>)}
          {groups.map((g) => <option key={g.id} value={`groups:${g.id}`}>{g.name} (group)</option>)}
          {form.audience && form.audience !== "*"
            && !chars.some((c) => `characters:${c.id}` === form.audience)
            && !groups.some((g) => `groups:${g.id}` === form.audience) && (
            <option value={form.audience}>{entry ? audienceLabel(entry) : form.audience}</option>
          )}
        </select>
      </Field>
      <Field label="What they have of it">
        <select aria-label="Status" value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value as KnowledgeStatus })}>
          <option value="knows">Knows</option>
          <option value="suspects">Suspects</option>
          <option value="unaware">Never heard</option>
        </select>
      </Field>
      <Field label="Scene">
        <select aria-label="Scene" value={form.scene}
                onChange={(e) => setForm({ ...form, scene: e.target.value, posts: [] })}>
          <option value="">— choose —</option>
          {scenes.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
      </Field>
      {shown && (
        <Field label="About"
               hint={grant ? "knowing a whole scene includes its summary and title"
                 : "never hearing it withholds the posts from them"}>
          <label className="chip-check">
            <input type="checkbox" checked={form.whole}
                   onChange={(e) => setForm({ ...form, whole: e.target.checked })} />
            <span>The whole scene</span>
          </label>
          {!form.whole && (
            <div className="knowledge-posts" role="group" aria-label="Posts">
              {shown.posts.map((p) => {
                const id = slot(p.key, p.part);
                return (
                  <label key={id} className="chip-check">
                    <input type="checkbox" checked={form.posts.includes(id)}
                           disabled={grant && p.hidden}
                           onChange={(e) => setForm({
                             ...form,
                             posts: e.target.checked ? [...form.posts, id]
                               : form.posts.filter((x) => x !== id),
                           })} />
                    <span>{`${p.speaker || "—"}: ${p.text.slice(0, 160)}`}</span>
                  </label>
                );
              })}
              {shown.keyless > 0 && (
                <p className="field-hint">
                  {shown.keyless} older post{shown.keyless === 1 ? "" : "s"} carry no id and can
                  only be covered by a whole-scene statement.
                </p>
              )}
            </div>
          )}
        </Field>
      )}
      <Field label="Note" hint="for you only: it never reaches a prompt">
        <textarea aria-label="Note" rows={3} value={form.note}
                  onChange={(e) => setForm({ ...form, note: e.target.value })} />
      </Field>
      <div className="form-actions">
        {entry && (
          <button className="subtle" disabled={busy} onClick={() => remove(entry)}>Delete</button>
        )}
        {entry && (
          <button className="subtle" onClick={() => { setForm(original); setMode("view"); }}>
            Cancel
          </button>
        )}
        <button className="primary" disabled={busy || !ready} onClick={save}>
          {entry ? "Save statement" : "Add statement"}
        </button>
      </div>
    </div>
  );
}
