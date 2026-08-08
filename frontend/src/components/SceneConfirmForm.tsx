import { useEffect, useState } from "react";
import { api, type CharacterSummary, type EntitySummary, type PCSummary,
         type RosterEntry } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { errMsg } from "./errMsg";
import type { DraftCast, SceneDraft } from "./sceneDraft";

export function SceneConfirmForm({ cid, draft, notice, onBack, onCancel, onCreated, onWriting }: {
  cid: string;
  draft: SceneDraft;
  /** a warning raised while the draft was built, e.g. a failed extraction */
  notice?: string | null;
  onBack: () => void;
  /** closes the whole chooser, distinct from onBack (which only returns to the
   *  picker). Optional so tests that don't care about it need not pass one. */
  onCancel?: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
  /** reports the create sequence in and out of flight, so the orchestrator can
   *  refuse to dismiss mid-write: unmounting cancels nothing. */
  onWriting?: (active: boolean) => void;
}) {
  const [title, setTitle] = useState(draft.title);
  const [date, setDate] = useState(draft.date);
  const [location, setLocation] = useState(draft.location);
  const [cast, setCast] = useState<DraftCast[]>(draft.source === "greeting" ? [] : draft.cast);
  const [premise, setPremise] = useState(draft.source === "greeting" ? "" : draft.premise);
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [addId, setAddId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // set when the scene exists but a later, non-fatal step failed: the user
  // reads what went wrong, then goes to the scene anyway
  const [salvaged, setSalvaged] = useState<string | null>(null);

  useEffect(() => {
    api.listEntities({ kind: "campaign", id: cid }, "locations").then(setLocations).catch(() => setLocations([]));
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars).catch(() => setChars([]));
    api.listCampaignPCs(cid).then(setPCs).catch(() => setPCs([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
  }, [cid]);

  // pcless scenes never seat players, matching start_from_greeting's guards.
  // Filtering `pcs` alone is NOT enough: a player can be seated as a
  // `characters` actor (CastPanel's role selector offers it), which is the same
  // hole Task 2 closed in the suggestion parser. So the roster's roles decide.
  const playerTokens = new Set(
    roster.filter((r) => r.role === "player").map((r) => `${r.kind}/${r.id}`));
  const addable: DraftCast[] = [
    ...chars.map((c) => ({ kind: "characters" as const, id: c.id, name: c.name })),
    ...pcs.map((p) => ({ kind: "pcs" as const, id: p.id, name: p.name })),
  // A campaign PC that hasn't appeared yet is absent from the roster, so
  // `playerTokens` alone would miss it: a pcless draft must exclude every PC
  // by kind, not just the ones the roster already knows are players.
  ].filter((o) => !(draft.pcless && (o.kind === "pcs" || playerTokens.has(`${o.kind}/${o.id}`))))
   .filter((o) => !cast.some((c) => c.kind === o.kind && c.id === o.id));

  function setWriting(active: boolean) { setBusy(active); onWriting?.(active); }

  // The cleanup-on-failure paths delete the half-made scene so it doesn't
  // strand a stray. If the delete itself fails, that stray is now invisible:
  // the user sees only the original error, presses Create again, and gets a
  // second scene while the first sits there unlisted-but-real. Say so.
  async function deleteAndReport(sid: string, msg: string): Promise<string> {
    try {
      await api.deleteScene(cid, sid);
      return msg;
    } catch {
      return `${msg} (cleanup also failed -- a half-made scene may be left behind)`;
    }
  }

  async function create() {
    setWriting(true);
    setError(null);
    const finalTitle = title.trim() || draft.defaultTitle;
    let sid: string;
    try {
      // 1. the date also goes in as suggested_date, so a later failure still
      //    leaves CastPanel's date box pre-filled
      ({ id: sid } = await api.createScene(cid, finalTitle, date || undefined, draft.pcless));
    } catch (err: any) {
      setError(errMsg(err));
      setWriting(false);
      return;
    }
    // 2. cast — the last step for which deleting the scene is still clean
    const soft: string[] = [];
    if (draft.source !== "greeting" && cast.length) {
      try {
        const r = await api.addCastBatch(cid, sid, cast.map((c) => ({ kind: c.kind, id: c.id })));
        // A chip the user explicitly added can still be skipped server-side
        // (e.g. its default version moved since the actor's first appearance)
        // -- say so rather than handing off as though the cast were complete.
        if (r.skipped.length) {
          const names = r.skipped.map((ref) => cast.find((c) => `${c.kind}/${c.id}` === ref)?.name ?? ref);
          soft.push(`not seated: ${names.join(", ")}`);
        }
      } catch (err: any) {
        setError(await deleteAndReport(sid, errMsg(err)));
        setWriting(false);
        return;
      }
    }
    // 3-4. location and date BEFORE seeding: start_from_greeting expands the
    //      greeting body through expand_macros, which resolves {{date}} from
    //      the scene's CURRENT moment. Seeding first dates it against nothing.
    //      Neither failure deletes: each is one independent piece of metadata.
    if (location) {
      try { await api.setSceneLocation(cid, sid, location); }
      catch (err: any) { soft.push(errMsg(err)); }
    }
    if (date) {
      try {
        const r = await api.setSceneDatetime(cid, sid, date);
        sid = r.id;
      } catch (err: any) { soft.push(errMsg(err)); }
    }
    // 5. seed. A failure here has written nothing outside the scene, so the
    //    scene goes; anything after has, so nothing does.
    if (draft.source === "greeting") {
      try {
        const r = await api.startFromGreeting(cid, sid, draft.gid);
        sid = r.id;
      } catch (err: any) {
        setError(await deleteAndReport(sid, errMsg(err)));
        setWriting(false);
        return;
      }
      // The title field is what the user was looking at when they pressed
      // Create, so it is their intent whether or not they typed in it — and
      // start_from_greeting has just overwritten it with the greeting's name.
      try {
        const r = await api.renameScene(cid, sid, finalTitle);
        sid = r.id;
      } catch (err: any) { soft.push(errMsg(err)); }
    }
    setWriting(false);
    const prompt = draft.source === "greeting" ? undefined : (premise || undefined);
    if (soft.length) { setSalvaged(sid); setError(soft.join(" · ")); return; }
    onCreated(sid, prompt);
  }

  return (
    <>
      {(error ?? notice) && <div className="banner">{error ?? notice}</div>}

      <label className="role" htmlFor="confirm-title">Title</label>
      <input id="confirm-title" aria-label="Title" type="text" value={title}
             onChange={(e) => setTitle(e.target.value)} />

      <div className="role">When</div>
      <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={date}
                          onChange={setDate} ariaLabel="Scene date" />

      <div className="role">Where</div>
      <select aria-label="Location" value={location} onChange={(e) => setLocation(e.target.value)}>
        <option value="">— no location —</option>
        {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>

      {draft.source === "greeting" ? (
        <div className="field-hint">
          The greeting supplies the opening post and seats its own cast.
        </div>
      ) : (
        <>
          <div className="role">In this scene</div>
          {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
          {cast.map((c) => (
            <span className="chip on" key={`${c.kind}/${c.id}`}>
              {c.name}
              <button className="subtle" aria-label={`Remove ${c.name}`}
                      onClick={() => setCast(cast.filter((x) => !(x.kind === c.kind && x.id === c.id)))}>×</button>
            </span>
          ))}
          <div className="picker">
            <select aria-label="Add to cast" value={addId}
                    onChange={(e) => setAddId(e.target.value)}>
              <option value="">— pick —</option>
              {addable.map((o) => (
                <option key={`${o.kind}/${o.id}`} value={`${o.kind}/${o.id}`}>{o.name}</option>
              ))}
            </select>
            <button className="primary" disabled={!addId} onClick={() => {
              const found = addable.find((o) => `${o.kind}/${o.id}` === addId);
              if (found) setCast([...cast, found]);
              setAddId("");
            }}>Add</button>
          </div>

          <label className="role" htmlFor="confirm-premise">Premise</label>
          <textarea id="confirm-premise" aria-label="Premise" rows={3} value={premise}
                    onChange={(e) => setPremise(e.target.value)} />
          <div className="field-hint">Seeds the opener box once the scene exists.</div>
        </>
      )}

      <div className="form-actions">
        {salvaged ? (
          <button className="primary" onClick={() =>
            onCreated(salvaged, draft.source === "greeting" ? undefined : (premise || undefined))}>
            Continue to scene
          </button>
        ) : (
          <>
            <button className="subtle" disabled={busy} onClick={onBack}>← Back</button>
            {onCancel && <button className="subtle" disabled={busy} onClick={onCancel}>Cancel</button>}
            <button className="primary" disabled={busy} onClick={create}>
              {busy ? "…" : "Create scene"}
            </button>
          </>
        )}
      </div>
    </>
  );
}
