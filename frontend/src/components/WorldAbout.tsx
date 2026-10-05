import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type WorldProfile } from "../api/client";
import { errorText } from "../api/errors";
import { draftOf, profileOf, type ProfileDraft } from "../worldProfile";
import { Field } from "./Field";

const EMPTY: WorldProfile = { genre: "", tone: "", themes: [], description: "" };

/** What the world is (#38): its genre, tone, themes and description, at the
 *  top of the overview. Read-only until Edit, like every record view, and
 *  saved through `PUT /worlds/{wid}` -- which also puts it in the prompt of
 *  every campaign played here, as the World overview section. */
export function WorldAbout({ wid }: { wid: string }) {
  const [profile, setProfile] = useState<WorldProfile | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Which world the screen is showing NOW. This component outlives a world
  // switch (the overview is not keyed by world), so a save still in flight
  // when the reader moves on must not land its world's profile on the next.
  const shown = useRef(wid);

  useEffect(() => {
    let live = true;
    shown.current = wid;
    setProfile(null); setDraft(null); setError(null); setSaving(false);
    api.getWorld(wid).then((w) => {
      if (live) setProfile({ genre: w.meta.genre ?? "", tone: w.meta.tone ?? "",
                             themes: w.meta.themes ?? [], description: w.body.trim() });
    }).catch((err: unknown) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [wid]);

  async function save() {
    if (!draft) return;
    const next = profileOf(draft);
    const target = wid;
    const current = () => shown.current === target;
    setSaving(true); setError(null);
    try {
      await api.updateWorld(target, next);
      // Re-read rather than echo: the store folds and de-duplicates what it
      // keeps, and the view should show what was kept.
      const w = await api.getWorld(target);
      if (!current()) return;
      setProfile({ genre: w.meta.genre ?? "", tone: w.meta.tone ?? "",
                   themes: w.meta.themes ?? [], description: w.body.trim() });
      setDraft(null);
    } catch (err: unknown) { if (current()) setError(errorText(err)); }
    finally { if (current()) setSaving(false); }
  }

  const p = profile ?? EMPTY;
  const empty = !p.genre && !p.tone && !p.themes.length && !p.description;
  return <div className="side-section world-about">
    <h4>About</h4>
    {error && <div className="banner">{error}</div>}
    {draft ? <div className="form">
      <Field label="Genre"><input type="text" value={draft.genre}
        onChange={(e) => setDraft({ ...draft, genre: e.target.value })} /></Field>
      <Field label="Tone"><input type="text" value={draft.tone}
        onChange={(e) => setDraft({ ...draft, tone: e.target.value })} /></Field>
      <Field label="Themes" hint="Comma-separated. They describe the world; they gate nothing.">
        <input type="text" value={draft.themes} onChange={(e) => setDraft({ ...draft, themes: e.target.value })} />
      </Field>
      <Field label="Description"><textarea rows={8} value={draft.description}
        onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
      <div className="form-actions">
        <button className="subtle" onClick={() => setDraft(null)}>Cancel</button>
        <button className="primary" disabled={saving} onClick={() => void save()}>Save</button>
      </div>
    </div> : profile && <>
      <div className="form-actions">
        <button className="subtle" onClick={() => setDraft(draftOf(p))}>Edit</button>
      </div>
      {empty ? <div className="field-hint">
          No description yet. Genre, tone, themes and a description reach the narrator
          in every campaign played in this world.
        </div> : <>
        {(p.genre || p.tone) && <div className="field-hint">
          {[p.genre && `Genre: ${p.genre}`, p.tone && `Tone: ${p.tone}`].filter(Boolean).join(" · ")}
        </div>}
        {p.themes.length > 0 && <div className="chips">
          {p.themes.map((t) => <span key={t} className="chip on">{t}</span>)}
        </div>}
        {p.description && <div className="detail-rendered">
          <Markdown remarkPlugins={[remarkGfm]}>{p.description}</Markdown>
        </div>}
      </>}
    </>}
  </div>;
}
