import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, splitNativeDate, type CharacterSummary, type EntitySummary, type PCSummary,
         type RosterEntry } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { errorText } from "../api/errors";
import type { DraftCast, SceneDraft } from "./sceneDraft";

/** Where the scene's opening post comes from (issue #90).
 *  - `greeting` — the greeting body, verbatim, seeded by `start_from_greeting`
 *    (which also seats the greeting's cast). Offered by greeting drafts only.
 *  - `premise`  — nothing is posted here; the premise is handed to `CastPanel`,
 *    whose generate → preview → adopt loop streams against a scene that exists.
 *    Generation genuinely cannot happen before creation, so this stays a
 *    handoff rather than a fourth write in `create()`.
 *  - `none`     — an empty scene the reader writes into themselves.
 *  #91's adapted greeting becomes the fourth member of this union. */
type FirstPost = "none" | "greeting" | "premise";

export function SceneConfirmForm({ cid, draft, notice, ready, onBack, onCancel, onCreated,
                                    onWriting, onSalvaged }: {
  cid: string;
  draft: SceneDraft;
  /** whether an LLM connection exists. This pane cannot generate anything
   *  itself -- `POST .../opener` needs a scene -- but it is now the pane that
   *  OFFERS to, so it has to say when there is nothing to generate with, the
   *  same way `SceneIdeaPicker` and `CastPanel` do. Reachable without one: the
   *  typed path builds a premise-carrying draft whether or not an LLM is set up.
   *
   *  REQUIRED, unlike the optional callbacks below, and deliberately so: a
   *  default would have to be `true` to preserve what the existing tests
   *  assert, which is the unsafe direction -- dropping `ready={ready}` at the
   *  one call site would leave this pane claiming it can generate, and every
   *  test here would stay green. Required makes that omission a `tsc -b`
   *  error, which CI runs, instead of a behavior nothing checks. */
  ready: boolean;
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
  /** reports the id once a soft failure leaves a real, created scene behind
   *  (`salvaged` below) -- Escape and the backdrop can dismiss the modal from
   *  here (unlike the busy-write case, `writing` is already clear), and
   *  without this the orchestrator has no way to know a scene now exists that
   *  its own scene list does not. */
  onSalvaged?: (sid: string) => void;
}) {
  const [title, setTitle] = useState(draft.title);
  const [date, setDate] = useState(draft.date);
  const [location, setLocation] = useState(draft.location);
  const [cast, setCast] = useState<DraftCast[]>(draft.source === "greeting" ? [] : draft.cast);
  const [premise, setPremise] = useState(draft.source === "greeting" ? "" : draft.premise);
  // A greeting was picked *for* its body, so it starts as the first post. Every
  // other draft starts on whatever it actually carries: a premise if the
  // suggestion or the reader supplied one, otherwise nothing -- "Create blank
  // scene" must not land on a generate option with an empty box.
  const [firstPost, setFirstPost] = useState<FirstPost>(
    draft.source === "greeting" ? "greeting" : draft.premise.trim() ? "premise" : "none");
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  // Tracks the locations read specifically (not chars/pcs/roster): the
  // controlled <select> can only ever offer what has loaded, so a location
  // this pane hasn't shown yet must not be able to reach setSceneLocation.
  const [locationsLoading, setLocationsLoading] = useState(true);
  const [locationsNotice, setLocationsNotice] = useState<string | null>(null);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [addId, setAddId] = useState("");
  // The campaign's current moment, offered as a one-click fill (never applied
  // on its own). `date` is where the story left off, `friendly` how to say so.
  const [clock, setClock] = useState<{ date: string; friendly: string } | null>(null);
  // Bumped on every fill, and used as the picker's `key`. The picker
  // deliberately DROPS an external value while its fields are half-filled, so
  // an in-progress edit is never stomped -- but a click on this button is the
  // one external change that is the user's own explicit request, and must win.
  // Remounting re-seeds it from `value` without teaching that component a
  // second, weaker notion of what an external change means.
  const [fillNonce, setFillNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // set when the scene exists but a later, non-fatal step failed: the user
  // reads what went wrong, then goes to the scene anyway
  const [salvaged, setSalvaged] = useState<string | null>(null);

  useEffect(() => {
    setLocationsLoading(true);
    setLocationsNotice(null);
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then((ls) => { setLocations(ls); setLocationsLoading(false); })
      .catch(() => {
        // Two choices here: leave Create disabled forever (locationsLoading
        // never settling) or drop the unresolved location and say why. The
        // first strands the user with no way forward; the second keeps the
        // form usable and still guarantees nothing the user never saw reaches
        // setSceneLocation. `location` is only cleared when it was actually
        // non-empty, so a draft with no location stays silent.
        setLocations([]);
        setLocationsLoading(false);
        setLocation((prev) => {
          if (prev) setLocationsNotice("Locations failed to load — the pre-filled location was cleared.");
          return "";
        });
      });
    // A campaign with no date yet, and a read that fails, both mean "nothing to
    // offer": the button simply does not render. It is a convenience over a
    // date the user can always pick by hand, so it never gets a banner.
    api.getCampaignClock(cid)
      .then((c) => setClock(c.now ? { date: splitNativeDate(c.now).date, friendly: c.friendly } : null))
      .catch(() => setClock(null));
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

  // Ordered by how much each source supplies, so the pre-selected one is at or
  // near the top. `greeting` is offered only where a greeting exists to seed
  // from; #91's adapted greeting joins it there.
  const sources: { value: FirstPost; label: string }[] = [
    ...(draft.source === "greeting"
      ? [{ value: "greeting" as const, label: "The greeting, verbatim" }] : []),
    // "in the scene", not just "generate": pressing Create generates nothing.
    // The premise is carried to CastPanel's opener box, where the reader still
    // has to generate and accept a post. A label promising the post itself
    // would be untrue on the commonest path of all -- every generated
    // suggestion arrives with a premise and so defaults to this option.
    { value: "premise", label: "Generate one in the scene, from a premise" },
    { value: "none", label: "Nothing — you write the first post" },
  ];

  // Once Create has read the fields (create() closes over that render's
  // values) or the scene already exists with a soft failure (Continue can't
  // save further edits -- there's nothing left for them to reach), every
  // field goes read-only so what's displayed always matches what will be
  // persisted or handed off.
  const locked = busy || !!salvaged;

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

  // `create()` below is several awaited writes long, all against the `cid`
  // this closure captured -- and NewSceneChooser discards a stale draft the
  // instant its `cid` prop changes, which unmounts this component. Unlike
  // CastPanel's `live` ref (compared against fresh props on a component that
  // stays mounted across the switch), this component never gets a chance to
  // observe the new `cid`: it is simply gone, mid-sequence, before another
  // render could show it one. So the only signal available is its own
  // unmount, and `create()` checks it before every write after the first.
  //
  // A LAYOUT effect's cleanup, not a passive one, for the same race
  // CastPanel's comment documents: a passive cleanup is scheduled in its own
  // task, so a write's `.then` can land as a microtask in the gap between the
  // unmounting commit and that task, reading a ref that has not flipped yet.
  // A layout cleanup runs synchronously inside the commit that unmounts this
  // component, so no later step can ever observe it as stale.
  //
  // This cannot make the sequence atomic -- a step already in flight when the
  // switch happens still lands, there is no client-side way to cancel an
  // issued HTTP request -- it only stops the STEPS AFTER IT from compounding
  // the problem. On a detected switch the sequence also does not delete the
  // scene it already made and does not call `onCreated`: reporting this id
  // into a `CampaignView` now showing a different campaign would be wrong,
  // and deleting a scene in a campaign the reader has left is worse than
  // leaving it there, unlisted but real, for them to find on return.
  //
  // Set on the way IN as well as cleared on the way out, for the reason
  // `CampaignView`'s `mountedRef` spells out (#95): main.tsx renders inside
  // StrictMode, and in development React runs setup / cleanup / setup on mount
  // — layout effects included. A cleanup-only flag is left `false` by that
  // middle step for the whole life of the form, so `create()` bailed at the
  // first check below on EVERY create: the scene was made server-side but
  // nothing was cast, dated, located or reported, and `busy` (cleared only on
  // paths that check this same flag) pinned the dialog on "…" forever. Dev-only
  // — and dev is where the app is run.
  const live = useRef(true);
  useLayoutEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  /** What `CastPanel`'s opener box is seeded with. Only the premise source
   *  hands anything over: declining the first post has to leave that box empty,
   *  or "nothing" would still arrive as a suggestion to generate from. */
  function handoff(): string | undefined {
    return firstPost === "premise" && premise.trim() ? premise : undefined;
  }

  async function create() {
    setWriting(true);
    setError(null);
    const finalTitle = title.trim() || draft.defaultTitle;
    const prompt = handoff();
    let sid: string;
    try {
      // 1. the date also goes in as suggested_date, so a later failure still
      //    leaves CastPanel's date box pre-filled
      ({ id: sid } = await api.createScene(cid, finalTitle, date || undefined, draft.pcless));
    } catch (err: any) {
      if (live.current) { setError(errorText(err)); setWriting(false); }
      return;
    }
    if (!live.current) return;    // switched campaigns while createScene was in flight -- stop here
    // 2. cast — the last step for which deleting the scene is still clean
    const soft: string[] = [];
    // Gated on the first-post SOURCE, not on the draft's kind: a greeting draft
    // that is no longer using its greeting gets no backend-seated cast either,
    // so the chips the form collected are the only cast it will ever have.
    if (firstPost !== "greeting" && cast.length) {
      try {
        // A `characters`-kind actor can be the player themselves (CastPanel's
        // role selector allows it, and the roster's roles are how this pane
        // already knows to keep such a token in an onscreen pcless filter --
        // see `playerTokens` above). _seat_cast_member defaults an omitted
        // role to "npc"; for a roster-known player that default fights the
        // campaign-locked "player" role and appear() rejects the seat as
        // though the user's explicit pick were skipped. Carry the role the
        // roster already told us rather than let the backend guess.
        const r = await api.addCastBatch(cid, sid, cast.map((c) => ({
          kind: c.kind, id: c.id,
          ...(c.kind === "characters" && playerTokens.has(`${c.kind}/${c.id}`) ? { role: "player" } : {}),
        })));
        // A chip the user explicitly added can still be skipped server-side
        // (e.g. its default version moved since the actor's first appearance)
        // -- say so rather than handing off as though the cast were complete.
        if (r.skipped.length) {
          const names = r.skipped.map((ref) => cast.find((c) => `${c.kind}/${c.id}` === ref)?.name ?? ref);
          soft.push(`not seated: ${names.join(", ")}`);
        }
      } catch (err: any) {
        if (live.current) { setError(await deleteAndReport(sid, errorText(err))); setWriting(false); }
        return;
      }
    }
    if (!live.current) return;
    // 3-4. location and date BEFORE seeding: start_from_greeting expands the
    //      greeting body through expand_macros, which resolves {{date}} from
    //      the scene's CURRENT moment. Seeding first dates it against nothing.
    //      Neither failure deletes: each is one independent piece of metadata.
    if (location) {
      try { await api.setSceneLocation(cid, sid, location); }
      catch (err: any) { soft.push(errorText(err)); }
      if (!live.current) return;
    }
    if (date) {
      try {
        const r = await api.setSceneDatetime(cid, sid, date);
        sid = r.id;
      } catch (err: any) { soft.push(errorText(err)); }
      if (!live.current) return;
    }
    // 5. seed. A failure here has written nothing outside the scene, so the
    //    scene goes; anything after has, so nothing does.
    if (draft.source === "greeting" && firstPost === "greeting") {
      try {
        const r = await api.startFromGreeting(cid, sid, draft.gid);
        sid = r.id;
      } catch (err: any) {
        if (live.current) { setError(await deleteAndReport(sid, errorText(err))); setWriting(false); }
        return;
      }
      if (!live.current) return;
      // The title field is what the user was looking at when they pressed
      // Create, so it is their intent whether or not they typed in it — and
      // start_from_greeting has just overwritten it with the greeting's name.
      try {
        const r = await api.renameScene(cid, sid, finalTitle);
        sid = r.id;
      } catch (err: any) { soft.push(errorText(err)); }
      if (!live.current) return;
    }
    // 6. the ledger, last: an idea becomes "used" only once the scene it
    //    became actually exists, and nothing before this point depends on it.
    //    A failure is soft rather than fatal for the same reason the location
    //    and date writes are -- the scene is real and usable -- but it is
    //    still reported: silently leaving the idea active means it comes back
    //    in the picker as though it had never been played, and the reader
    //    would have no way to tell that from a deliberate keep.
    if (draft.source === "saved" && draft.lid) {
      try { await api.setSceneIdeaStatus(cid, draft.lid, "used", sid); }
      catch (err: any) { soft.push(`${errorText(err)} — the saved idea is still on the list`); }
      if (!live.current) return;
    }
    setWriting(false);
    if (soft.length) { setSalvaged(sid); onSalvaged?.(sid); setError(soft.join(" · ")); return; }
    onCreated(sid, prompt);
  }

  return (
    <>
      {(error ?? notice ?? locationsNotice) && <div className="banner">{error ?? notice ?? locationsNotice}</div>}

      <label className="role" htmlFor="confirm-title">Title</label>
      <input id="confirm-title" aria-label="Title" type="text" value={title} disabled={locked}
             onChange={(e) => setTitle(e.target.value)} />

      <div className="role">When</div>
      <div className="picker">
        <CalendarDatePicker key={fillNonce} scope={{ kind: "campaign", id: cid }} value={date}
                            disabled={locked} onChange={setDate} ariaLabel="Scene date" />
        {clock ? (
          /* Fills the fields above and nothing else -- the scene's date is
             written by Create (step 4), and moving time afterwards stays the
             scene's own "When" row and POST /advance. */
          <button className="subtle" disabled={locked}
                  title={`Fill the date fields with ${clock.friendly || clock.date}`}
                  onClick={() => { setDate(clock.date); setFillNonce((n) => n + 1); }}>
            Last scene's date
          </button>
        ) : null}
      </div>

      <div className="role">Where</div>
      <select aria-label="Location" value={location} disabled={locked}
              onChange={(e) => setLocation(e.target.value)}>
        <option value="">— no location —</option>
        {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>

      <div className="role" id="confirm-first-post">First post</div>
      <div className="radio-group" role="radiogroup" aria-labelledby="confirm-first-post">
        {sources.map((s) => (
          <label className="radio-row" key={s.value}>
            <input type="radio" name="first-post" value={s.value} disabled={locked}
                   checked={firstPost === s.value} onChange={() => setFirstPost(s.value)} />
            {s.label}
          </label>
        ))}
      </div>

      {firstPost === "greeting" ? (
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
              <button className="subtle" aria-label={`Remove ${c.name}`} disabled={locked}
                      onClick={() => setCast(cast.filter((x) => !(x.kind === c.kind && x.id === c.id)))}>×</button>
            </span>
          ))}
          <div className="picker">
            <select aria-label="Add to cast" value={addId} disabled={locked}
                    onChange={(e) => setAddId(e.target.value)}>
              <option value="">— pick —</option>
              {addable.map((o) => (
                <option key={`${o.kind}/${o.id}`} value={`${o.kind}/${o.id}`}>{o.name}</option>
              ))}
            </select>
            <button className="primary" disabled={!addId || locked} onClick={() => {
              const found = addable.find((o) => `${o.kind}/${o.id}` === addId);
              if (found) setCast([...cast, found]);
              setAddId("");
            }}>Add</button>
          </div>

          {firstPost === "premise" && (
            <>
              <label className="role" htmlFor="confirm-premise">Premise</label>
              <textarea id="confirm-premise" aria-label="Premise" rows={3} value={premise} disabled={locked}
                        onChange={(e) => setPremise(e.target.value)} />
              <div className="field-hint">Seeds the opener box once the scene exists.</div>
              {/* Not disabled, and the default is left alone: the box is still
                  worth filling for a reader who sets a connection up after
                  creating the scene, and CastPanel takes the same line --
                  prompt input enabled, hint shown, only Generate disabled. */}
              {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
            </>
          )}
        </>
      )}

      <div className="form-actions">
        {salvaged ? (
          <button className="primary" onClick={() => onCreated(salvaged, handoff())}>
            Continue to scene
          </button>
        ) : (
          <>
            <button className="subtle" disabled={busy} onClick={onBack}>← Back</button>
            {onCancel && <button className="subtle" disabled={busy} onClick={onCancel}>Cancel</button>}
            <button className="primary" disabled={busy || locationsLoading} onClick={create}>
              {busy ? "…" : "Create scene"}
            </button>
          </>
        )}
      </div>
    </>
  );
}
