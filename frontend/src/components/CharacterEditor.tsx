import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Appearance, type Card, type CardFormat, type Casefile, type CharacterDetail, type CharacterSummary, type ChubImportResult, type ChubUnlinkedVersion, type EntityScope, type Greeting, type ModuleDetail, type VersionRef } from "../api/client";
import { AvatarFocusPicker } from "./AvatarFocusPicker";
import { CalendarDatePicker } from "./CalendarDatePicker";
import CreationWizard from "./CreationWizard";
import { Field } from "./Field";
import { GreetingMarkdown } from "./GreetingMarkdown";
import { HtmlNote } from "./HtmlNote";
import { ImageDescriptionField } from "./ImageDescriptionField";
import { OwnedLorePanel } from "./OwnedLorePanel";
import SheetPanel from "./SheetPanel";
import { ErrorNote } from "./ErrorNote";
import { TaglinePrompt } from "./TaglinePrompt";
import { UrlImportPrompt } from "./UrlImportPrompt";
import { scrollShellToTop } from "../shellScroll";

import { errorText } from "../api/errors";
const TEXT_FIELDS: { key: string; label: string; area?: boolean }[] = [
  { key: "description", label: "Description", area: true },
  { key: "personality", label: "Personality", area: true },
  { key: "scenario", label: "Scenario", area: true },
  { key: "first_mes", label: "First message", area: true },
  { key: "mes_example", label: "Example dialogue", area: true },
  { key: "system_prompt", label: "System prompt", area: true },
  { key: "post_history_instructions", label: "Post-history instructions", area: true },
  { key: "creator_notes", label: "Creator notes", area: true },
];

function describeChubResult(result: ChubImportResult): string {
  const parts: string[] = [];
  if (result.gallery.attempted > 0) {
    parts.push(`${result.gallery.stored}/${result.gallery.attempted} gallery image${result.gallery.attempted === 1 ? "" : "s"}`);
  }
  if (result.lore.lorebooks_found > 0) {
    const n = result.lore.created.length;
    parts.push(`${result.lore.lorebooks_found} lorebook${result.lore.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`);
  }
  const lead = result.updated ? "Updated this version from URL" : "Downloaded from URL";
  return parts.length ? `${lead} — ${parts.join(", ")}` : lead;
}

type Mode = "grid" | "detail" | "edit";

/** The middle pane's tabs. Everything here is part of the *card* — the
 *  world-level record that is sent to the model and shared by every campaign
 *  built on this world. What one campaign has made of her lives in the right
 *  pane and is deliberately not reachable from this strip. */
type CardTab = "card" | "lore" | "greetings" | "art";

/** A scene's story number, read out of its own id (`<NNN>--<date>--<slug>`).
 *  The same read `CampaignView.sceneNumber` makes, and for the same reason: the
 *  number belongs to the file, never to a list's ordering, which drifts the
 *  moment an earlier scene is re-edited. */
function sceneOrdinal(id: string): string {
  const m = /^(\d+)--/.exec(id);
  return m ? String(parseInt(m[1], 10)) : id;
}

/** A rough size for the description's cost stamp.
 *
 *  There is no tokenizer in the browser: the only real token counts grimoire
 *  has come from the backend's context builder, which measures a whole
 *  assembled prompt once per turn and never an individual field. So this is the
 *  usual four-characters-a-token estimate, and it is rendered behind a `≈` so
 *  it reads as the order of magnitude it is. Its job is to make the size of a
 *  field legible *before* it costs a turn, not to be added up. */
function estimateTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

const EXPORT_FORMATS: { format: CardFormat; label: string; hint: string }[] = [
  { format: "json", label: "JSON", hint: "card text plus the avatar, embedded" },
  { format: "png", label: "PNG", hint: "the avatar, with the card written into it" },
  { format: "charx", label: "CHARX", hint: "card and avatar in one zip" },
];

/** Download the viewed version as a card. Plain links, like the campaign
 *  exports: the response is binary and the route names the file, so there is
 *  nothing for the client to assemble. World scope only — the export route
 *  hangs off /worlds. */
function ExportMenu({ wid, cid, vid }: { wid: string; cid: string; vid: string }) {
  return (
    <details className="export-menu">
      <summary className="export-toggle">Export</summary>
      <div className="export-options">
        {EXPORT_FORMATS.map(({ format, label, hint }) => (
          <a key={format} href={api.exportUrl(wid, cid, vid, format)} download title={hint}>
            {label}
          </a>
        ))}
      </div>
    </details>
  );
}

function focusStyle(f?: number | null): React.CSSProperties | undefined {
  return f == null ? undefined : { objectPosition: `${f}% ${f}%` };
}

function CampaignRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="local-row">
      <span className="data-label">{label}</span>
      <p className="local-value">{value}</p>
    </div>
  );
}

/** The right pane: what one campaign has made of her.
 *
 *  Nothing in here is part of the card and nothing in here is editable from
 *  this screen — it is a readout of files the absorb pass writes, and looking
 *  like a readout rather than a form is the point. The middle pane is a
 *  document someone authors and every campaign then shares; this is one
 *  campaign's record of what happened, and it belongs to that campaign alone.
 *  Today both sit in one stack of fields, which is how a shared record gets
 *  edited by someone who believes they are editing one campaign.
 *
 *  The world route has no campaign at all, so it says so rather than showing an
 *  empty frame: a blank pane under a heading reads as "this campaign knows
 *  nothing about her", which is a claim, and the wrong one. */
function CampaignPane(
  { worldScope, label, name, state }: {
    worldScope: boolean;
    /** The campaign's name, or its slug if the name could not be read. */
    label: string;
    name: string;
    /** `null` while the campaign's record of her is still being read. */
    state: { scenes: string[]; casefile: Casefile | null } | null;
  },
) {
  if (worldScope) {
    return (
      <aside className="campaign-pane no-campaign" aria-label="Campaign state">
        <div className="pane-stamp">
          <span className="eyebrow">No campaign in scope</span>
        </div>
        <p className="local-empty">
          You are editing the world's record of {name}. Play state — what she
          currently knows, her dossier, the scenes she has walked into — belongs
          to a campaign, and this page is not open in one. Open her from a
          campaign's world copy to see it.
        </p>
      </aside>
    );
  }

  return (
    <aside className="campaign-pane" aria-label="Campaign state">
      <div className="pane-stamp">
        <span className="eyebrow accent">In {label}</span>
        <span className="eyebrow">Campaign-local · not part of the card</span>
      </div>

      {state === null ? (
        <p className="local-empty">Reading…</p>
      ) : state.scenes.length === 0 ? (
        <p className="local-empty">
          {name} has not been in a scene in {label} yet. Play one and the absorb
          pass writes her state and her dossier here — the card on the left does
          not change.
        </p>
      ) : <>
        {state.casefile === null ? (
          <p className="local-empty">
            Could not read {name}'s state in {label}. The scenes below are what
            her appearance record still says.
          </p>
        ) : <>
          {state.casefile.standing && <CampaignRow label="Current state" value={state.casefile.standing} />}
          {state.casefile.knows && <CampaignRow label="Knows" value={state.casefile.knows} />}
          {state.casefile.suspects && <CampaignRow label="Suspects" value={state.casefile.suspects} />}
          {/* The tagline is the guess a dossier replaces, so it only stands in
              while there is no dossier — showing both would present a first
              impression and the record that outgrew it as equals. */}
          {(state.casefile.dossier || state.casefile.tagline) && (
            <div className="local-row">
              <span className="data-label">Dossier</span>
              <p className="local-value">{state.casefile.dossier || state.casefile.tagline}</p>
              <span className="dossier-source">
                {state.casefile.dossier ? "dossier.md" : "tagline.md"}
              </span>
            </div>
          )}
          {!state.casefile.standing && !state.casefile.knows && !state.casefile.suspects
            && !state.casefile.dossier && !state.casefile.tagline && (
            <p className="local-empty">
              Nothing recorded yet. {label} has had her on stage but no absorb
              pass has written her state or her dossier.
            </p>
          )}
        </>}

        <div className="local-row">
          <span className="data-label">Appears in</span>
          {/* Plain attributes, not links: a scene is somewhere this editor
              cannot navigate to, and a chip that looks clickable and is not is
              worse than one that never claimed to be. */}
          <div className="chip-row">
            {state.scenes.map((sid) => (
              <span className="chip on" key={sid}>Scene {sceneOrdinal(sid)}</span>
            ))}
          </div>
        </div>
      </>}
    </aside>
  );
}

export function CharacterEditor({ scope, wid, resetSignal, focus, onOpenLore, onOpenGreeting, module = null }:
  { scope: EntityScope; wid: string; resetSignal?: number; focus?: { cid: string; vid: string } | null;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
    onOpenGreeting?: (gid: string) => void;
    module?: ModuleDetail | null }) {
  const worldScope = scope.kind === "world";
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("grid");
  // The raw rejection rather than its text, so the banner can tell a model
  // that could not be reached from any other refusal (#210). Generating a
  // voice anchor or a tagline is a provider call like any other, and a plain
  // string here would have thrown the `kind` away before the banner saw it.
  // Every other setter still writes a composed sentence; `ErrorNote` renders
  // a string unchanged.
  const [error, setError] = useState<unknown>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const versionFileRef = useRef<HTMLInputElement>(null);
  const avatarRef = useRef<HTMLInputElement>(null);
  const shelfFileRef = useRef<HTMLInputElement>(null);
  const [imageAppearances, setImageAppearances] = useState<Appearance[]>([]);
  const [worldGreetings, setWorldGreetings] = useState<Greeting[]>([]);
  const [bookMsg, setBookMsg] = useState<string | null>(null);
  const [localizeProg, setLocalizeProg] = useState<{ done: number; total: number } | null>(null);
  const [localizeMsg, setLocalizeMsg] = useState<string | null>(null);
  const [galleryProg, setGalleryProg] = useState<{ done: number; total: number } | null>(null);
  const [unlinkedVersions, setUnlinkedVersions] = useState<ChubUnlinkedVersion[] | null>(null);
  const [bulkLocalize, setBulkLocalize] = useState<{ current: number; cards: number } | null>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [birthdate, setBirthdate] = useState("");
  const [tagline, setTagline] = useState("");
  const [taglineBusy, setTaglineBusy] = useState(false);
  const taglineReq = useRef(0);
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
  const [voiceAnchor, setVoiceAnchor] = useState("");
  const [anchorBusy, setAnchorBusy] = useState(false);
  const [anchorSaving, setAnchorSaving] = useState(false);
  const [anchorState, setAnchorState] = useState<"loading" | "ready" | "error">("loading");
  const anchorReq = useRef(0);
  // What the anchor textarea held when it was last in sync with the server --
  // set on a successful load and on a successful save. `voiceAnchor` differing
  // from it is an UNSAVED DRAFT, which `select()` must not silently discard.
  const anchorLoaded = useRef("");
  const [urlPromptOpen, setUrlPromptOpen] = useState(false);
  const [cropOpen, setCropOpen] = useState(false);
  const [bulkUrl, setBulkUrl] = useState<{ current: number; total: number; name: string; step: string } | null>(null);
  const lockReq = useRef(0);
  const [locked, setLocked] = useState<string | null>(null);       // campaign: locked version id
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [importVid, setImportVid] = useState("");
  // Which half of the card the middle pane is showing. Reset per character
  // below, not per version -- comparing two versions' art is exactly what the
  // version list is for, and snapping back to CARD each click would undo it.
  const [tab, setTab] = useState<CardTab>("card");
  // campaign: what THIS campaign has made of the open character, for the right
  // pane. `null` while it is still being read; in world scope it is never read
  // at all, because there is no campaign to have made anything of her.
  const [campaignState, setCampaignState] =
    useState<{ scenes: string[]; casefile: Casefile | null } | null>(null);
  // The campaign's display name, for the right pane's heading. The pane's whole
  // job is naming an owner, and `scope.id` is a slug -- it stands in when the
  // read fails, rather than the heading losing its subject.
  const [campaignName, setCampaignName] = useState("");
  // How many world-lore entries name this character as an owner, for the LORE
  // tab's count. `OwnedLorePanel` reads the same list to render the entries
  // themselves; both fire in the same tick, and `client.ts` shares in-flight
  // GETs by path, so this is one request between them rather than two.
  const [loreCount, setLoreCount] = useState<number | null>(null);
  // campaign: the ids that have ever been cast in this campaign. `null` while
  // the roster is still loading (or in world scope, where it has no meaning) --
  // distinct from the empty set, which is a campaign nobody has played yet, and
  // distinct again from `rosterFailed`, which is null for a third reason. Those
  // three have to stay separable: the empty set filters everything out, a
  // failure filters nothing, and loading shows no verdict at all.
  const [appeared, setAppeared] = useState<Set<string> | null>(null);
  const [rosterFailed, setRosterFailed] = useState(false);
  // Same token discipline as `lockReq`/`anchorReq`: the roster is re-read on
  // `resetSignal` as well as on a scope change, so two reads of the SAME
  // campaign can be in flight at once and the scope they carry cannot tell them
  // apart. Without this, a slow earlier read lands last and reinstates a roster
  // from before the scene that was just played.
  const rosterReq = useRef(0);
  // A campaign inherits its whole world's roster, most of which never walks
  // on. The grid therefore opens on the campaign's own cast and offers the
  // inherited remainder behind a toggle.
  const [showAll, setShowAll] = useState(false);
  // The character a close is handing back to the grid, held until there is a
  // roster to judge it against. See `keepVisible` below.
  const pendingReveal = useRef<string | null>(null);
  const [revealTick, setRevealTick] = useState(0);

  // LIVE mirrors of state that async continuations have to read. A handler
  // closes over the values from the render that created it, and everything
  // below runs after at least one `await` -- so by the time it resumes, its
  // `scope`, `detail` and `voiceAnchor` may all describe a screen the user has
  // already left. Refs assigned during render always hold the current value.
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const liveAnchor = useRef({ cid: "", text: "", state: "loading" as typeof anchorState });
  liveAnchor.current = { cid: detail?.meta.id ?? "", text: voiceAnchor, state: anchorState };
  // Which character is open, for the `resetSignal` effect: it closes one
  // WITHOUT going through `backToGrid`, and it cannot take `detail` as a
  // dependency (it would then re-fire on every character opened, sending the
  // reader back to the grid they just left).
  const liveDetailId = useRef("");
  liveDetailId.current = detail?.meta.id ?? "";

  /** Hand a character back to the grid without letting the appeared filter
   *  swallow it.
   *
   *  A character can be reached without going through the grid at all -- a
   *  greeting's present-character link, an owner chip, a chub-unlinked chip --
   *  and nothing says it has appeared in this campaign. Landing on a grid that
   *  filters it out reads as the record having been deleted, so the filter
   *  yields to it instead.
   *
   *  Recorded rather than decided here, because BOTH of the closes that call
   *  this can run while the roster read is still in flight (the `focus` route
   *  opens a character on mount, in parallel with that read) -- and a decision
   *  taken against no roster is no decision, it just lets the character vanish
   *  the moment the roster lands. The effect below applies it as soon as there
   *  is something to apply it against, whichever order the two arrive in. */
  function keepVisible(id: string) {
    if (!id || liveScope.current.kind !== "campaign") return;
    pendingReveal.current = id;
    setRevealTick((n) => n + 1);   // re-run the resolver even if `appeared` is unchanged
  }

  useEffect(() => {
    const pend = pendingReveal.current;
    if (pend === null || appeared === null) return;   // nothing pending, or no roster yet
    pendingReveal.current = null;
    if (!appeared.has(pend)) setShowAll(true);
  }, [appeared, revealTick]);

  /** Install a freshly-read character — unless the editor has since left the
   *  scope it was read from, in which case drop it.
   *
   *  The scope effect below clears the open character precisely so a stale id
   *  cannot be combined with the new scope on a write. A read still in flight
   *  when the scope changes puts it straight back: the continuation calls
   *  `setDetail` with scope A's record while the component renders under scope
   *  B, and the next save — the anchor PUT among them — addresses B by A's id.
   *  Guarded here rather than at each call site, because every one of them is
   *  the same shape and only one of them has to forget. */
  function adopt(d: CharacterDetail, from: EntityScope): boolean {
    if (liveScope.current.kind !== from.kind || liveScope.current.id !== from.id) return false;
    setDetail(d);
    return true;
  }

  const reload = useCallback(() => api.listCharacters(scope).then(setChars), [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reload();
    setWizardOpen(false); // a scope change can reuse this instance; never carry a wizard across it
    // ...and never carry an OPEN CHARACTER across it either. `detail.meta.id`
    // is combined with the CURRENT `scope` on every write, so a character left
    // open across a scope change addresses the new world or campaign by the old
    // one's id -- and where that id also exists there, the save lands on the
    // wrong record. Bumping the anchor token additionally orphans a GET still
    // in flight for the scope we just left, whose reply would otherwise fill
    // the textarea with the other scope's anchor.
    anchorReq.current++;
    // ...and never carry the grid FILTER across it either. `showAll` is a
    // statement about one campaign's inherited roster; left standing it opens
    // the next campaign on its whole world instead of on its own cast, which is
    // the state this filter exists to avoid. Reset here rather than beside the
    // roster read, which also re-runs on `resetSignal` -- re-clicking the tab
    // should not undo a toggle the reader just made.
    setShowAll(false);
    pendingReveal.current = null;
    // `setDetail(null)` below does not take effect until the next render, but
    // the `resetSignal` effect can run before that render -- in the same commit,
    // when a caller changes both props at once -- and would then read the ref
    // and hand the PREVIOUS scope's character to this one's filter, re-arming
    // the reveal this line just disarmed and opening the new campaign on its
    // whole inherited roster. Clearing the ref is immediate, so that window
    // closes (Codex review, round 3).
    liveDetailId.current = "";
    setDetail(null);
    setCard(null);
    setMode("grid");
    setVoiceAnchor("");
    setAnchorState("loading");
    setAnchorBusy(false);
    setAnchorSaving(false);
  }, [reload]);

  // Who is actually in a scene here. Re-read on `resetSignal` as well as on a
  // scope change, so re-clicking the Characters tab after playing a scene picks
  // up the actors that scene introduced rather than showing a roster from
  // before it. World scope has no appearances at all, so it clears instead of
  // fetching -- and clearing matters, because this instance is reused across a
  // scope change and a campaign's set left standing would filter a world's grid.
  useEffect(() => {
    // Bumped before the early return too: a world scope must orphan a campaign
    // read still in flight, or that reply installs a campaign's `appeared` set
    // over a world's grid and filters records that have no appearances at all.
    const req = ++rosterReq.current;
    setAppeared(null);
    setRosterFailed(false);
    if (scope.kind !== "campaign") return;
    api.listAppearances(scope.id)
      .then((roster) => {
        if (rosterReq.current !== req) return;   // a later read owns the answer
        // A character closed before this landed is judged by the `keepVisible`
        // resolver above, which re-runs on this very state change.
        //
        // A ROSTER ENTRY IS NOT AN APPEARANCE. `appearances.transitions.leave`
        // drops a scene from an actor's record but deliberately keeps the
        // record itself ("the actor stays appeared campaign-wide"), because the
        // entry is also what locks them to a version and a base. So a character
        // seated and then removed again -- and one whose only scene was since
        // deleted -- sits in the roster with an empty `scenes` list, having
        // never been in one. `suggestions` treats bare presence as appeared on
        // purpose (it is asking "who is already spoken for", and a locked
        // version counts), but this grid is answering the reader's question
        // "who is in this campaign", and the answer to that is the scene list.
        setAppeared(new Set(roster
          .filter((r) => r.kind === "characters" && (r.scenes?.length ?? 0) > 0)
          .map((r) => r.id)));
      })
      // An unreadable roster must not hide the records it was meant to narrow:
      // the filter is withdrawn entirely and the grid shows everything. Tracked
      // separately from "still loading" so the grid can wait for one and not
      // the other.
      .catch(() => { if (rosterReq.current === req) setRosterFailed(true); });
  }, [scope.kind, scope.id, resetSignal]);

  // re-clicking the Characters tab (resetSignal bumps) returns to the grid.
  // This is a close like `backToGrid`'s, so it owes the character it closes the
  // same protection from the appeared filter -- it just gets there without
  // passing through that function.
  useEffect(() => {
    keepVisible(liveDetailId.current);
    setMode("grid");
    setDetail(null);
    setCard(null);
  }, [resetSignal]);

  // arrived via a present-character link: open that character at the given version
  useEffect(() => {
    if (focus) focusCharacter(focus.cid, focus.vid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // detail-view extras: tagged greeting images + world greetings featuring this character
  const detailCid = detail?.meta.id;
  useEffect(() => {
    if (!detailCid) return;
    if (worldScope) api.listImageAppearances(wid, detailCid).then(setImageAppearances).catch(() => setImageAppearances([]));
    api.listGreetings(scope).then(setWorldGreetings).catch(() => setWorldGreetings([]));
  }, [wid, worldScope, scope.kind, scope.id, detailCid]);  // eslint-disable-line react-hooks/exhaustive-deps

  // The middle pane always opens on the card. Keyed to the character rather
  // than to the mode: coming back from the edit form should land where you
  // left, and switching version keeps the tab on purpose (see `tab`).
  useEffect(() => { setTab("card"); }, [detailCid]);

  // How many owned lore entries there are, for the LORE tab's count. Only when
  // there is a Lore tab to route to -- without `onOpenLore` the panel does not
  // render and the count would label a tab that goes nowhere.
  useEffect(() => {
    if (!detailCid || !onOpenLore) { setLoreCount(null); return; }
    let live = true;
    api.listEntities(scope, "lore")
      .then((items) => {
        if (!live) return;
        const ref = `characters:${detailCid}`;
        setLoreCount(items.filter((e) =>
          (e.owners ?? "").split(",").map((o) => o.trim()).includes(ref)).length);
      })
      // A count is an ornament on a tab; failing to read one must not cost the
      // tab, which still opens the panel that will report the failure itself.
      .catch(() => { if (live) setLoreCount(null); });
    return () => { live = false; };
  }, [scope.kind, scope.id, detailCid, !!onOpenLore]);  // eslint-disable-line react-hooks/exhaustive-deps

  // The campaign that owns the right pane, by name. One read per scope, not per
  // character: it is a property of the campaign, and the pane's heading is the
  // only thing on this screen that needs it.
  useEffect(() => {
    if (worldScope) { setCampaignName(""); return; }
    let live = true;
    api.getCampaign(scope.id)
      .then((c) => { if (live) setCampaignName(c.meta.name); })
      .catch(() => { if (live) setCampaignName(""); });
    return () => { live = false; };
  }, [worldScope, scope.id]);

  const hasAvatar = (detail && card)
    ? (detail.versions.find((v) => v.id === vid)?.images ?? []).includes("avatar")
    : false;
  const avatarFocus = detail?.versions.find((v) => v.id === vid)?.avatar_focus ?? null;
  const chubSource = detail?.versions.find((v) => v.id === vid)?.chub_source ?? "";
  const isChub = detail?.versions.find((v) => v.id === vid)?.is_chub ?? false;
  const imageTokens = detail?.versions.find((v) => v.id === vid)?.image_v ?? {};
  // Absent key = never reviewed, "" = reviewed and deliberately undescribed.
  // `?? {}` collapses "this build has no descriptions" into "none reviewed",
  // which is the right reading: both mean nothing has been said about any of
  // them. What must NOT collapse is absent-vs-"" for one image, and indexing
  // preserves that -- `descriptions[name]` is `undefined` or `""`.
  const descriptions = detail?.versions.find((v) => v.id === vid)?.image_descriptions ?? {};
  const galleryImages = (detail?.versions.find((v) => v.id === vid)?.images ?? [])
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));

  function loadVersion(d: CharacterDetail, id: string) {
    const v = d.versions.find((x) => x.id === id) ?? d.versions[0];
    setVid(v.id);
    setCard(v.card);
    setGreetings(v.card.data.alternate_greetings ?? []);
    setCropOpen(false);
    setBookMsg(null);
    setLocalizeMsg(null);
    setLocalizeProg(null);
  }

  // Download every remote image referenced in the card's text into the local
  // asset store and rewrite the text to it, driving a progress bar from the
  // server's SSE events. Reloads the version afterward so the rewrites show.
  async function runLocalize(cid: string, version: string) {
    setLocalizeMsg(null);
    setLocalizeProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.localizeImages(wid, cid, version, (e) => {
        if (e.error) {
          finalMsg = `Localize failed: ${e.error.detail}`;
        } else if (e.summary) {
          const s = e.summary;
          finalMsg =
            s.total === 0
              ? "No remote images found"
              : `Localized ${s.localized} image${s.localized === 1 ? "" : "s"}` +
                (s.skipped ? `, skipped ${s.skipped}` : "") +
                (s.failed ? `, ${s.failed} failed` : "") +
                (s.capped ? " (download cap reached)" : "");
        } else if (typeof e.done === "number") {
          setLocalizeProg({ done: e.done, total: e.total ?? 0 });
        } else if (typeof e.total === "number") {
          setLocalizeProg({ done: 0, total: e.total });
        }
      });
      // show the rewritten text + any new images for the version we localized
      const d = await api.readCharacter(scope, cid);
      if (!adopt(d, scope)) return;
      loadVersion(d, version);  // clears localizeMsg, so set the summary after it
    } catch (err: unknown) {
      finalMsg = `Localize failed: ${errorText(err)}`;
    } finally {
      setLocalizeProg(null);
      if (finalMsg) setLocalizeMsg(finalMsg);
    }
  }

  // Localize a batch of freshly-imported cards back-to-back, accumulating one
  // aggregate summary. Stays on the grid (no card is open), so progress and the
  // result render in the grid toolbar rather than a single card's localize block.
  async function runBulkLocalize(cards: { cid: string; version: string }[]) {
    setImportMsg(null);
    let localized = 0, skipped = 0, failed = 0;
    for (let i = 0; i < cards.length; i++) {
      setBulkLocalize({ current: i + 1, cards: cards.length });
      try {
        await api.localizeImages(wid, cards[i].cid, cards[i].version, (e) => {
          if (e.summary) {
            localized += e.summary.localized;
            skipped += e.summary.skipped;
            failed += e.summary.failed;
          }
        });
      } catch {
        failed += 1;  // a whole card's localize failing shouldn't abort the batch
      }
    }
    setBulkLocalize(null);
    setImportMsg(
      `Localized ${localized} image${localized === 1 ? "" : "s"} across ${cards.length} cards` +
      (skipped ? `, skipped ${skipped}` : "") + (failed ? `, ${failed} failed` : ""),
    );
  }

  // localize reads/writes the server-stored card, so running it with unsaved
  // editor changes would discard them — guard the button when the form is dirty.
  // Compare through buildCard()'s normalization (it always rewrites
  // alternate_greetings) so an unedited card isn't flagged as dirty.
  const storedVersion = detail?.versions.find((v) => v.id === vid);
  const storedCard = storedVersion?.card;
  const normalizedStored = storedCard && {
    ...storedCard,
    data: { ...storedCard.data, alternate_greetings: (storedCard.data.alternate_greetings ?? []).filter((g) => g.trim() !== "") },
  };
  const dirty = !!(card && normalizedStored && JSON.stringify(buildCard()) !== JSON.stringify(normalizedStored));
  // The import posts no card: the route commits whatever character_book is on
  // disk for this version, normalized. So the count is a fact about the stored
  // version, and it comes from the payload that describes it — not from the
  // live editor card (which is the editor's state, not disk's, and would
  // follow a book edit the import ignores) and not from a `.entries.length`
  // taken here, which counts the disabled and blank entries normalization
  // drops: offer 4, land 1 (#16).
  const bookCount = storedVersion?.importable_lore ?? 0;

  function localizeControls(blocked: boolean, blockedHint?: string) {
    if (!detail) return null;
    return (
      <div className="localize-block">
        <button className="subtle" type="button" disabled={!!localizeProg || blocked}
                onClick={() => runLocalize(detail.meta.id, vid)}>
          {localizeProg ? "Localizing…" : "Localize images"}
        </button>
        {localizeProg && (
          <div className="localize-progress">
            <progress value={localizeProg.done} max={localizeProg.total || 1} />
            <span className="field-hint">{localizeProg.done}/{localizeProg.total}</span>
          </div>
        )}
        {!localizeProg && blocked && blockedHint && <span className="field-hint">{blockedHint}</span>}
        {localizeMsg && <span className="field-hint">{localizeMsg}</span>}
      </div>
    );
  }

  async function select(cid: string): Promise<CharacterDetail | null> {
    setError(null);
    const d = await api.readCharacter(scope, cid);
    if (!adopt(d, scope)) return null;
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.meta.default_version);
    // `select()` is the refresh EVERY other save runs (card version, avatar,
    // lock, import...), and none of them touch the anchor -- so reloading it
    // unconditionally discarded an anchor draft whenever the user edited the
    // card and the anchor in one sitting and saved the card first. Reload only
    // when there is nothing to lose: a different character, or no unsaved edit.
    // `focusCharacter` still reloads unconditionally -- that is navigation TO a
    // character, not a refresh of the one already open.
    //
    // Read LIVE, not from the closure. Every caller reaches here after awaiting
    // its own write, and a draft typed during that await is invisible to the
    // render snapshot the handler captured -- so the closed-over `voiceAnchor`
    // still equals `anchorLoaded` and the draft is judged absent, one keystroke
    // too late to save it.
    const live = liveAnchor.current;
    const keepDraft = live.cid === cid && live.state === "ready"
      && live.text !== anchorLoaded.current;
    if (!keepDraft) loadVoiceAnchor(cid);   // campaign-local characters need one too (#59)
    if (worldScope) loadTagline(cid);
    else await loadLockState(cid);
    return d;
  }

  async function loadLockState(cid: string) {
    // token drops a slow earlier response so selecting A then B can't show A's lock on B
    const req = ++lockReq.current;
    // Cleared before the await, not after it: the right pane is the *previous*
    // character's campaign state until this lands, and a wrong dossier under a
    // right name is worse than a pane that says it is still reading.
    setCampaignState(null);
    const roster = await api.listAppearances(scope.id).catch(() => []);
    if (lockReq.current !== req) return;
    const entry = roster.find((r) => r.kind === "characters" && r.id === cid);
    setLocked(entry?.version ?? null);
    setImportVid("");
    loadCasefile(cid, entry?.scenes ?? [], req);
    // the source world's versions feed the import picker; a deleted world char offers none
    api.readCharacter({ kind: "world", id: wid }, cid)
      .then((w) => { if (lockReq.current === req) setWorldVersions(w.versions.map((v) => ({ id: v.id, name: v.name }))); })
      .catch(() => { if (lockReq.current === req) setWorldVersions([]); });
  }

  /** Read the campaign-local half of this screen: current state, what she
   *  knows and suspects, and her dossier paragraph.
   *
   *  There is no campaign-scoped casefile endpoint. The one that exists is
   *  nested under a scene — `GET .../scenes/{sid}/cast/{kind}/{id}/casefile` —
   *  and checks she is really cast in that scene before answering, which is
   *  that route's access control as much as its correctness condition. But the
   *  record it returns is campaign-scoped, not scene-scoped: `store/casefile.
   *  build` says so in as many words ("`sid` is not used to narrow anything").
   *  So asking through the newest scene she is cast in returns exactly the
   *  campaign's *current* state, and the membership check passes by
   *  construction, because that scene came out of her own appearance record.
   *
   *  Someone cast in no scene is not asked about at all. The route would 404,
   *  and a 404 rendered as an empty pane reads as "nothing recorded about her"
   *  for a character the campaign has simply never played — a different
   *  sentence, and the one the pane says instead.
   *
   *  `feels_toward` and `standing_facts` come back too and are deliberately not
   *  shown: feelings are held toward *the rest of that scene's cast*, so on a
   *  screen with no scene in it they would be a relationship set chosen by an
   *  implementation detail of which scene we asked through. The play view's
   *  dossier column, which does have a scene, is where those belong. */
  async function loadCasefile(cid: string, scenes: string[], req: number) {
    if (scenes.length === 0) {
      setCampaignState({ scenes, casefile: null });
      return;
    }
    try {
      const casefile = await api.getCasefile(scope.id, scenes[scenes.length - 1], "characters", cid);
      if (lockReq.current !== req) return;
      setCampaignState({ scenes, casefile });
    } catch {
      // A scene deleted out from under the appearance record, a hand-edited
      // state file, a cast change between the two reads: the pane still knows
      // which scenes she is in and says only that, rather than claiming she has
      // no recorded state or blanking the whole screen.
      if (lockReq.current !== req) return;
      setCampaignState({ scenes, casefile: null });
    }
  }

  async function runPick() {
    if (!detail) return;
    if (!window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    try {
      await api.pickVersion(scope.id, "characters", detail.meta.id, vid);
      await select(detail.meta.id);
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function runImport() {
    if (!detail || !importVid) return;
    if (!window.confirm("Replace the locked version with the world's copy?")) return;
    try {
      await api.importVersion(scope.id, "characters", detail.meta.id, importVid);
      await select(detail.meta.id);
    } catch (err: unknown) {
      setError(err);
    }
  }

  // Fetch the tagline independently of the (synchronous) card load so a slow GET
  // never wedges stale card data next to fresh meta during navigation. The req token
  // drops a slow earlier response so selecting A then B can't leave A's tagline on B.
  function loadTagline(cid: string) {
    const req = ++taglineReq.current;
    setTagline("");
    api.getCharacterTagline(wid, cid)
      .then((r) => { if (taglineReq.current === req) setTagline(r.tagline); })
      .catch(() => { if (taglineReq.current === req) setTagline(""); });
  }

  // Same request-token discipline as the tagline, and its own counter: the two
  // GETs race independently, so sharing one token would let the slower of a
  // single selection cancel the faster.
  //
  // Unlike the tagline this also tracks load STATE, because a blank anchor is
  // destructive: PUT "" deletes it. "" is the placeholder shown while the GET
  // is in flight and what a failed GET leaves behind, so saving from either
  // state would silently remove a stored anchor the user never saw. Save is
  // disabled until a load actually succeeds.
  function loadVoiceAnchor(cid: string) {
    const req = ++anchorReq.current;
    setVoiceAnchor("");
    setAnchorState("loading");
    // Bumping the token above just orphaned any in-flight generation, and an
    // orphan's `finally` no longer matches, so it will never clear this itself.
    // Without this line one abandoned Generate disables the button for every
    // character selected afterwards, until the editor remounts.
    setAnchorBusy(false);
    // Same for an orphaned SAVE: its `finally` no longer matches either, so
    // without this a PUT abandoned by navigation would leave Save disabled for
    // every character opened afterwards.
    setAnchorSaving(false);
    api.getCharacterVoiceAnchor(scope, cid)
      .then((r) => {
        if (anchorReq.current !== req) return;
        setVoiceAnchor(r.voice_anchor);
        anchorLoaded.current = r.voice_anchor;
        setAnchorState("ready");
      })
      .catch(() => { if (anchorReq.current === req) setAnchorState("error"); });
  }

  async function saveVoiceAnchor() {
    if (!detail || anchorState !== "ready" || anchorSaving) return;
    // One PUT at a time. Two overlapping saves race on the server, and the
    // SLOWER one wins the file -- so an edit made between them can end up
    // discarded while the editor still shows it. Blocking the second click is
    // enough here: the writes are whole-value, so there is nothing to merge.
    setAnchorSaving(true);
    // Tokened like the load and the generation -- CAPTURED, not bumped, since a
    // save invalidates nothing. The single-flight check above is not enough on
    // its own: leaving the character (or the scope) clears `anchorSaving`, so
    // the next save is free to start while this PUT is still open, and this
    // one's `finally` would then clear the flag out from under it. Two writes
    // for the same character overlap, the slower wins the file, and the editor
    // goes on showing the newer text.
    const req = anchorReq.current;
    try {
      // Trimmed to blank on purpose: a blank anchor removes it, which is how a
      // character opts back out of voice-drift detection. Only reachable once
      // the load succeeded, so the blank is the user's, not a placeholder.
      await api.setCharacterVoiceAnchor(scope, detail.meta.id, voiceAnchor.trim());
      if (anchorReq.current !== req) return;
      anchorLoaded.current = voiceAnchor;   // in sync again: no longer a draft
    } catch (err: unknown) {
      if (anchorReq.current === req) setError(err);
    } finally {
      if (anchorReq.current === req) setAnchorSaving(false);
    }
  }

  async function regenerateVoiceAnchor() {
    // `anchorSaving` too: a generation landing around an open PUT swaps the
    // textarea for a fresh draft while Save returns to its idle label, so the
    // control says "saved" over a value that never was.
    if (!detail || anchorSaving) return;
    // Tokened like the GET: generation is slow, and without this a draft for
    // character A lands in character B's textarea if the user navigates while
    // it is in flight -- and Save writes it under B, since it reads the CURRENT
    // detail id.
    const req = ++anchorReq.current;
    setAnchorBusy(true);
    try {
      const r = await api.generateCharacterVoiceAnchor(scope, detail.meta.id);
      if (anchorReq.current !== req) return;
      // An empty completion is a failed generation, not a draft. Installing it
      // would arm the destructive save with a blank the user never wrote, and
      // one click would delete a stored anchor that generation failed to
      // replace. Keep the loaded value and state untouched.
      if (!r.voice_anchor.trim()) {
        setError("The model returned an empty voice anchor — nothing was changed.");
        return;
      }
      setVoiceAnchor(r.voice_anchor);
      setAnchorState("ready");
    } catch (err: unknown) {
      if (anchorReq.current === req) setError(err);
    } finally {
      if (anchorReq.current === req) setAnchorBusy(false);
    }
  }

  async function saveTagline() {
    if (!detail) return;
    try {
      await api.setCharacterTagline(wid, detail.meta.id, tagline.trim());
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function regenerateTagline() {
    if (!detail) return;
    setTaglineBusy(true);
    try {
      const r = await api.generateCharacterTagline(wid, detail.meta.id);
      setTagline(r.tagline);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setTaglineBusy(false);
    }
  }

  async function saveBirthdate(value: string) {
    if (!detail) return;
    setBirthdate(value);
    try {
      await api.setCharacterBirthdate(wid, detail.meta.id, value);
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function openDetail(cid: string) {
    scrollShellToTop();
    const d = await select(cid);
    if (!d) return null;   // scope changed under the read; do not open anything
    setMode("detail");
    return d;
  }

  /** Open one character at a given version — the landing point for a
   *  present-character link from a greeting or a scene.
   *
   *  It handles its own failure, and has to: the only caller is a mount effect,
   *  which cannot await it, so an uncaught rejection here is an unhandled
   *  rejection and the screen simply stays on the grid with nothing said. A
   *  link that outlived its character (deleted world-side, or removed from this
   *  campaign) is the ordinary way to get one, so the banner names it. */
  async function focusCharacter(cid: string, vid: string) {
    scrollShellToTop();
    setError(null);
    let d: CharacterDetail;
    try {
      d = await api.readCharacter(scope, cid);
    } catch (err: unknown) {
      setError(err);
      return;
    }
    if (!adopt(d, scope)) return;
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.versions.some((v) => v.id === vid) ? vid : d.meta.default_version);
    loadVoiceAnchor(cid);   // campaign-local characters need one too (#59)
    if (worldScope) loadTagline(cid);
    else await loadLockState(cid);
    setMode("detail");
  }

  async function openEdit(cid: string) {
    scrollShellToTop();
    if (!(await select(cid))) return;   // scope changed under the read
    setMode("edit");
  }

  function backToGrid() {
    scrollShellToTop();
    keepVisible(liveDetailId.current);
    setDetail(null);
    setCard(null);
    setMode("grid");
    reload();
  }

  function setField(key: string, value: unknown) {
    if (!card) return;
    setCard({ ...card, data: { ...card.data, [key]: value } });
  }

  function buildCard(): Card {
    return { ...card!, data: { ...card!.data,
                               // Trimmed so the card, the container and the text
                               // `bake_char_name` already bakes with (it strips)
                               // all hold the same string -- an untrimmed
                               // `data.name` is also the version rail's label.
                               name: (card!.data.name ?? "").trim(),
                               alternate_greetings: greetings.filter((g) => g.trim() !== "") } };
  }

  async function newCharacter() {
    const name = window.prompt("New character name?")?.trim();
    if (!name) return;
    const { character } = await api.createCharacter(wid, { name });
    await reload();
    await openEdit(character);
  }

  async function save() {
    if (!detail || !card) return;
    setError(null);
    try {
      await api.updateVersion(scope, detail.meta.id, vid, buildCard());
      // #13: the card's name is not the character's. The grid tile, the cast
      // panel and every `meta.name` prompt section read the CONTAINER name, so
      // a renamed card has to carry the new name over or the two diverge for
      // good. Three conditions, and each is load-bearing:
      //
      //   `renamed !== stored`  the user changed the Name field in THIS form.
      //     Comparing against the container instead would rename on any save
      //     of a character whose card name already differed -- an old record,
      //     an import given an explicit name, a chub re-download -- so editing
      //     a description would rename her everywhere, with no undo.
      //   `renamed !== meta.name`  spares a no-op PUT when the edit merely
      //     brings the card back into line with the container.
      //   `vid === default_version`  a sibling version's card name is that
      //     version's rail label (`_version_label`), not the character's.
      //
      // Card first, rename second: a failed rename then leaves a saved card
      // and an error, not a renamed container holding edits that never landed.
      const stored = (detail.versions.find((v) => v.id === vid)?.card.data.name ?? "").trim();
      const renamed = (card.data.name ?? "").trim();
      if (renamed && renamed !== stored && renamed !== detail.meta.name
          && vid === detail.meta.default_version) {
        await api.setCharacterName(scope, detail.meta.id, renamed);
      }
      await select(detail.meta.id);
      await reload();
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function addVersion() {
    if (!detail) return;
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    const { version } = await api.createVersion(scope, detail.meta.id, { name, card: buildCard() });
    if (!(await select(detail.meta.id))) return;   // scope changed under the read
    loadVersion(await api.readCharacter(scope, detail.meta.id), version);
  }

  async function onImportVersion(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      const { version } = await api.importCharacter(wid, file, formatOf(file), detail.meta.id);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, version);
      await reload();
      await runLocalize(detail.meta.id, version);
    } catch (err: unknown) {
      setError(err);
    } finally {
      e.target.value = "";
    }
  }

  async function setDefault() {
    if (!detail) return;
    await api.setDefaultVersion(scope, detail.meta.id, vid);
    await select(detail.meta.id);
  }

  async function deleteCharacter(cid: string, name: string) {
    if (!window.confirm(`Delete character '${name}'?`)) return;
    await api.deleteCharacter(wid, cid);
    backToGrid();
  }

  async function importBook() {
    if (!detail) return;
    setBookMsg(null);
    try {
      const { created } = await api.importCharacterBook(wid, detail.meta.id, vid);
      // `lorebook.commit` drops entries already in world lore, so a second
      // click on an unchanged book legitimately creates nothing. "Imported 0
      // entries" reads as a failure; say what actually happened instead.
      setBookMsg(created.length === 0
        ? "Already in world lore — nothing new to import"
        : `Imported ${created.length} entr${created.length === 1 ? "y" : "ies"} to world lore`);
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function onAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      await api.putImage(scope, detail.meta.id, vid, "avatar", file);
      await select(detail.meta.id);
      await reload();
    } catch (err: unknown) {
      setError(err);
    } finally {
      e.target.value = "";
    }
  }

  async function removeAvatar() {
    if (!detail) return;
    await api.deleteImage(scope, detail.meta.id, vid, "avatar");
    await select(detail.meta.id);
    await reload();
  }

  // Reload the open version in place (select() would snap back to the default version).
  async function refreshVersion() {
    if (!detail) return;
    const d = await api.readCharacter(scope, detail.meta.id);
    if (!adopt(d, scope)) return;
    loadVersion(d, vid);
    await reload();
  }

  async function describeImage(name: string, description: string) {
    if (!detail) return;
    await api.setCharacterImageDescription(scope, detail.meta.id, vid, name, description);
    await refreshVersion();
  }

  /** World scope only: that is where the route is, and offering a button that
   *  404s campaign-side would be worse than not offering one. */
  async function draftDescription(name: string): Promise<string> {
    if (!detail) return "";
    const r = await api.draftCharacterImageDescription(wid, detail.meta.id, vid, name);
    return r.description;
  }

  async function promote(name: string) {
    if (!detail) return;
    setError(null);
    try {
      await api.promoteImage(scope, detail.meta.id, vid, name);
      await refreshVersion();
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function copyFromGreeting(a: Appearance, slot: "avatar" | "gallery") {
    if (!detail) return;
    setError(null);
    try {
      await api.copyGreetingImage(scope, detail.meta.id, vid, { gid: a.gid, name: a.name, slot });
      await refreshVersion();
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function saveFocus(f: number) {
    if (!detail) return;
    setCropOpen(false);
    setError(null);
    try {
      await api.setAvatarFocus(scope, detail.meta.id, vid, f);
      await refreshVersion();
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    const next = hasAvatar
      ? `gallery_${galleryImages.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putImage(scope, detail.meta.id, vid, next, file);
      await refreshVersion();
    } catch (err: unknown) {
      setError(err);
    } finally {
      e.target.value = "";
    }
  }

  function formatOf(file: File): string {
    const ext = file.name.split(".").pop()?.toLowerCase();
    return ext === "png" ? "png" : ext === "charx" ? "charx" : "json";
  }

  async function onImport(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const imported: { cid: string; version: string }[] = [];
    for (const file of files) {
      try {
        const { character, version } = await api.importCharacter(wid, file, formatOf(file));
        imported.push({ cid: character, version });
      } catch (err: unknown) {
        failures.push(`${file.name}: ${errorText(err)}`);
      }
    }
    e.target.value = "";
    await reload();
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    else if (imported.length === 1) {
      // single import: open the card so its localize progress shows inline
      const d = await openDetail(imported[0].cid);
      if (d) setTaglineQueue([{ cid: imported[0].cid, name: d.meta.name }]);
      await runLocalize(imported[0].cid, imported[0].version);
    } else if (imported.length > 1) {
      await runBulkLocalize(imported);
    }
  }

  // Bulk pipeline for "Download from URL". Per URL: import (the backend already
  // downloads the avatar, chub gallery, and related chub lorebooks inside this
  // one call), localize embedded images, then import the card's embedded
  // character_book to world lore. Failures record and continue — one bad URL
  // shouldn't sink the batch. Tagline prompts queue up after the whole run.
  async function runBulkUrlImport(urls: string[]) {
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const added: { cid: string; name: string }[] = [];
    let localized = 0, gallery = 0, lore = 0;
    for (let i = 0; i < urls.length; i++) {
      setBulkUrl({ current: i + 1, total: urls.length, name: urls[i], step: "importing" });
      let result: ChubImportResult;
      try {
        result = await api.importCharacterFromChub(wid, urls[i]);
      } catch (err: unknown) {
        failures.push(`${urls[i]}: ${errorText(err)}`);
        continue;
      }
      gallery += result.gallery.stored;
      lore += result.lore.created.length;
      let name = result.character;
      try {
        name = (await api.readCharacter(scope, result.character)).meta.name;
      } catch { /* fall back to the id */ }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "localizing images" });
      try {
        await api.localizeImages(wid, result.character, result.version, (e) => {
          if (e.summary) localized += e.summary.localized;
        });
      } catch (err: unknown) {
        failures.push(`${name}: localize failed (${errorText(err)})`);
      }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "importing lorebook" });
      try {
        const { created } = await api.importCharacterBook(wid, result.character, result.version);
        lore += created.length;
      } catch (err: unknown) {
        failures.push(`${name}: lorebook import failed (${errorText(err)})`);
      }
      added.push({ cid: result.character, name });
      await reload();  // the new card appears in the grid as it lands
    }
    setBulkUrl(null);
    const parts = [`Added ${added.length}/${urls.length} character${urls.length === 1 ? "" : "s"}`];
    if (gallery) parts.push(`${gallery} gallery image${gallery === 1 ? "" : "s"}`);
    if (localized) parts.push(`${localized} image${localized === 1 ? "" : "s"} localized`);
    if (lore) parts.push(`${lore} lore entr${lore === 1 ? "y" : "ies"} imported`);
    setImportMsg(parts.join(" · ") + (failures.length ? ` · failed — ${failures.join("; ")}` : ""));
    if (urls.length === 1 && added.length === 1) await openDetail(added[0].cid);
    setTaglineQueue(added);
  }

  async function downloadVersionFromChub() {
    if (!detail) return;
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url, detail.meta.id, vid);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, result.version);
      await reload();
      setImportMsg(describeChubResult(result));
      await runLocalize(detail.meta.id, result.version);
    } catch (err: unknown) {
      setError(err);
    }
  }

  // One-click re-import from the version's stored link — the backend matches
  // the source and overwrites this version in place instead of forking a new one.
  async function redownloadFromChub() {
    if (!detail || !chubSource) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, chubSource, detail.meta.id, vid);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, result.version);
      await reload();
      setImportMsg(describeChubResult(result));
      await runLocalize(detail.meta.id, result.version);
    } catch (err: unknown) {
      setError(err);
    }
  }

  // Linking is per-version, and the detail view may be showing a
  // non-default version -- reload via loadVersion(d, vid) rather than
  // select() (which always snaps back to the default version).
  async function linkChub() {
    if (!detail) return;
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    try {
      await api.setCharacterChubSource(wid, detail.meta.id, vid, url);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, vid);
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function unlinkChub() {
    if (!detail) return;
    setError(null);
    try {
      await api.clearCharacterChubSource(wid, detail.meta.id, vid);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, vid);
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function downloadChubGallery() {
    if (!detail) return;
    setError(null);
    setImportMsg(null);
    setGalleryProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.downloadCharacterChubGallery(wid, detail.meta.id, vid, (e) => {
        if (e.error) {
          finalMsg = `Gallery download failed: ${e.error.detail}`;
        } else if (e.summary) {
          const s = e.summary;
          finalMsg =
            s.attempted === 0
              ? "No gallery images found on chub.ai"
              : `${s.stored}/${s.attempted} gallery image${s.attempted === 1 ? "" : "s"} downloaded`;
        } else if (typeof e.done === "number") {
          setGalleryProg({ done: e.done, total: e.total ?? 0 });
        } else if (typeof e.total === "number") {
          setGalleryProg({ done: 0, total: e.total });
        }
      });
      // refresh so newly downloaded images show without navigating away and back
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, vid);
    } catch (err: unknown) {
      finalMsg = `Gallery download failed: ${errorText(err)}`;
    } finally {
      setGalleryProg(null);
      if (finalMsg) setImportMsg(finalMsg);
    }
  }

  async function downloadChubLorebooks() {
    if (!detail) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.downloadCharacterChubLorebooks(wid, detail.meta.id, vid);
      const n = result.created.length;
      setImportMsg(
        result.lorebooks_found === 0
          ? "No linked lorebooks found on chub.ai"
          : `${result.lorebooks_found} lorebook${result.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`,
      );
    } catch (err: unknown) {
      setError(err);
    }
  }

  async function checkChubLinks() {
    setError(null);
    try {
      const { versions } = await api.findChubUnlinked(wid);
      setUnlinkedVersions(versions);
    } catch (err: unknown) {
      setError(err);
    }
  }

  /** `?v=` names the exact content state, so these cache immutable; an upload,
   *  a remove or a promote refreshes the tokens through select()/reload().
   *  The token must come from the STORE: a session counter reset to its
   *  initial value on every mount, which pinned the pre-upload image in the
   *  browser cache for a year (an immutable URL is never revalidated). */
  const withToken = (url: string, v?: string | null) => (v ? `${url}?v=${v}` : url);
  const avatarSrc = (cid: string, version: string, v?: string | null) =>
    withToken(api.actorImageUrl(scope, "characters", cid, version, "avatar"), v);

  if (wizardOpen && module && worldScope) {
    return (
      <div className="character-editor">
        <CreationWizard scope={scope} kind="characters" module={module}
                        createRecord={(n) => api.createCharacter(wid, { name: n }).then((r) => r.character)}
                        deleteRecord={(id) => api.deleteCharacter(wid, id).then(() => {})}
                        onDone={async (id) => { setWizardOpen(false); await reload(); await openEdit(id); }}
                        onCancel={() => setWizardOpen(false)} />
      </div>
    );
  }

  if (mode === "grid" || !detail || !card) {
    // The filter is offered only where "appeared" means something and the
    // roster actually loaded; everywhere else `shown` is simply every card.
    const filterable = !worldScope && appeared !== null;
    const appearedChars = filterable ? chars.filter((c) => appeared.has(c.id)) : chars;
    const shown = filterable && !showAll ? appearedChars : chars;
    // Campaign scope has no verdict yet while the roster is in flight. Painting
    // the grid anyway shows every inherited character for as long as that read
    // takes and then yanks most of them away -- so the cards (and the "nobody
    // yet" line, which would be equally wrong) wait for the answer. A FAILED
    // read is not this state: it has its answer, which is "do not filter".
    const rosterPending = !worldScope && appeared === null && !rosterFailed;
    return (
      <div className="character-editor">
        {taglineQueue.length > 0 && (
          <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                         onSaved={(t) => { setTagline(t); reload(); }}
                         onClose={() => setTaglineQueue((q) => q.slice(1))} />
        )}
        {urlPromptOpen && (
          <UrlImportPrompt onClose={() => setUrlPromptOpen(false)} onSubmit={runBulkUrlImport} />
        )}
        <div className="grid-toolbar">
          {worldScope && <>
            <button className="primary" onClick={newCharacter}>+ New character</button>
            {worldScope && module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
              <button className="subtle" onClick={() => setWizardOpen(true)}>+ New character with sheet…</button>
            )}
            <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
            <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden aria-label="Import character card" onChange={onImport} />
            <button className="subtle" onClick={() => setUrlPromptOpen(true)}>Download from URL</button>
            <button className="subtle" onClick={checkChubLinks}>Check chub.ai links</button>
          </>}

          {bulkLocalize && (
            <span className="field-hint">Localizing card {bulkLocalize.current}/{bulkLocalize.cards}…</span>
          )}
          {bulkUrl && (
            <span className="field-hint">
              Adding {bulkUrl.current}/{bulkUrl.total} — {bulkUrl.name}: {bulkUrl.step}…
            </span>
          )}
          {filterable && (
            <div className="chips" role="group" aria-label="Show">
              <button className={"chip" + (showAll ? "" : " on")} aria-pressed={!showAll}
                      onClick={() => setShowAll(false)}>
                Appeared ({appearedChars.length})
              </button>
              <button className={"chip" + (showAll ? " on" : "")} aria-pressed={showAll}
                      onClick={() => setShowAll(true)}>
                All ({chars.length})
              </button>
            </div>
          )}

          {!bulkLocalize && importMsg && <span className="field-hint">{importMsg}</span>}
        </div>
        {unlinkedVersions !== null && (
          <div className="chub-unlinked-list">
            {unlinkedVersions.length === 0 ? (
              <div className="field-hint">All versions are linked to chub.ai</div>
            ) : (
              <>
                <div className="field-hint">
                  {unlinkedVersions.length} version{unlinkedVersions.length === 1 ? "" : "s"} not linked to chub.ai:
                </div>
                <div className="chips">
                  {unlinkedVersions.map((u) => (
                    <button key={`${u.character}:${u.version}`} className="chip"
                            onClick={() => focusCharacter(u.character, u.version)}>
                      {u.character_name} ({u.version_name})
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        {rosterPending ? null : shown.length === 0 ? (
          <div className="editor-empty">
            {chars.length === 0
              ? "No characters yet. Create one or import a card."
              : "No one has appeared in this campaign yet — show All to see the world's roster."}
          </div>
        ) : (
          <div className="char-grid">
            {shown.map((c) => (
              <div key={c.id} className="char-card">
                <button className="char-card-main" onClick={() => openDetail(c.id)}>
                  {c.has_avatar
                    ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                           src={avatarSrc(c.id, c.default_version, c.avatar_v)} />
                    : <div className="initials-avatar" aria-hidden>
                        {c.name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                      </div>}
                  <span className="char-card-name">{c.name}</span>
                  {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
                  {((c.gallery_count ?? 0) > 0 || (c.localized_count ?? 0) > 0 || (c.greeting_count ?? 0) > 0) && (
                    <span className="char-card-badges">
                      {(c.greeting_count ?? 0) > 0 && (
                        <span className="chip">{c.greeting_count} greeting{c.greeting_count === 1 ? "" : "s"}</span>
                      )}
                      {(c.gallery_count ?? 0) > 0 && <span className="chip">{c.gallery_count} gallery</span>}
                      {(c.localized_count ?? 0) > 0 && <span className="chip">{c.localized_count} localized</span>}
                    </span>
                  )}
                </button>
                <div className="char-card-actions">
                  <button className="subtle" onClick={() => openEdit(c.id)}>Edit</button>
                  {worldScope && <button className="subtle" onClick={() => deleteCharacter(c.id, c.name)}>Delete</button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (mode === "detail") {
    const tags = card.data.tags ?? [];
    const name = card.data.name || detail.meta.name;
    const description = (card.data[TEXT_FIELDS[0].key] as string) ?? "";
    const firstMes = (card.data.first_mes as string) ?? "";
    // World greetings that feature her: links to other records, not card
    // content, which is why they do not count toward the tab's greeting count.
    const featuring = worldGreetings.filter((g) => (g.present ?? []).includes(detail.meta.id));
    const greetingCount = (firstMes.trim() ? 1 : 0) + greetings.length;
    const artCount = (hasAvatar ? 1 : 0) + galleryImages.length;
    // The campaign that owns the right pane. `scope.id` is a slug and a poor
    // heading, but a heading with the wrong subject would be worse than a plain
    // one, so it stands in only when the name could not be read.
    const campaignLabel = campaignName || scope.id;

    // Every card field except the description, which the CARD tab renders on
    // its own above these with the cost stamp, and the two greeting fields,
    // which are the GREETINGS tab. An empty field is dropped rather than framed
    // and left blank: an empty frame claims there is something there.
    const filled = (keys: string[]) => TEXT_FIELDS.filter((f) =>
      keys.includes(f.key) && ((card.data[f.key] as string) ?? "").trim());
    const cardField = (f: { key: string; label: string }) => {
      const val = (card.data[f.key] as string) ?? "";
      return (
        <div className="card-field" key={f.key}>
          <span className="data-label">{f.label}</span>
          <div className="card-field-body">
            {f.key === "creator_notes"
              ? <HtmlNote html={val} title="Creator notes" />
              : <div className="detail-text">{val}</div>}
          </div>
        </div>
      );
    };

    return (
      <div className="character-editor">
        {taglineQueue.length > 0 && (
          <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                         onSaved={(t) => { setTagline(t); reload(); }}
                         onClose={() => setTaglineQueue((q) => q.slice(1))} />
        )}
        {cropOpen && hasAvatar && (
          <AvatarFocusPicker src={avatarSrc(detail.meta.id, vid, imageTokens.avatar)}
                             initial={avatarFocus ?? 50}
                             onSave={saveFocus}
                             onClose={() => setCropOpen(false)} />
        )}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}

        {/* 274px identity · the card · 300px campaign state. The middle and
            right halves are the substance of this screen and are built to look
            like they belong to different owners, because they do: the middle is
            the world's record of her, sent to the model and shared by every
            campaign; the right is what one campaign has made of her, and is a
            readout rather than anything you can edit here. */}
        <div className="char-detail">

          <aside className="char-identity" aria-label="Character">
            <div className="char-identity-scroll">
              <button className="column-back" onClick={backToGrid}>‹ All characters</button>

              {hasAvatar
                ? <button className="identity-art avatar-crop-btn" type="button"
                          aria-label="Adjust avatar crop" title="Adjust avatar crop"
                          onClick={() => setCropOpen(true)}>
                    <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
                         src={avatarSrc(detail.meta.id, vid, imageTokens.avatar)} />
                  </button>
                : <div className="identity-art identity-art-empty" aria-hidden>
                    {name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                  </div>}

              <h3 className="identity-name">{name}</h3>
              {card.data.creator ? <div className="detail-byline">by {card.data.creator}</div> : null}
              {tagline && (
                <div className="identity-tagline">
                  <p>{tagline}</p>
                  {/* Named the way the dossier column names its sources: these
                      are files you can go and read, not prose the app made up. */}
                  <span className="dossier-source">tagline.md</span>
                </div>
              )}

              <div className="column-section">
                <div className="column-section-head">
                  <span className="section-label">Versions</span>
                  <span className="column-count">{detail.versions.length}</span>
                </div>
                <div className="version-list">
                  {detail.versions.map((v) => (
                    <div key={v.id} className={"version-row" + (v.id === vid ? " active" : "")}>
                      {/* The badges are siblings of the button, not children:
                          inside it they would join its accessible name, and a
                          version is picked by its name. */}
                      <button className="version-pick" aria-pressed={v.id === vid}
                              onClick={() => loadVersion(detail, v.id)}>
                        {v.name}
                      </button>
                      {v.id === locked
                        ? <span className="version-flag locked">Locked in {campaignLabel}</span>
                        : v.id === detail.meta.default_version
                          ? <span className="version-flag">default</span>
                          : null}
                    </div>
                  ))}
                </div>

                {!worldScope && (
                  locked ? (
                    <div className="version-lock-controls">
                      <select aria-label="Import version" value={importVid}
                              onChange={(e) => setImportVid(e.target.value)}>
                        <option value="">— world version —</option>
                        {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                      </select>
                      <button className="subtle" disabled={!importVid} onClick={runImport}>Import from world</button>
                    </div>
                  ) : detail.versions.length > 1 ? (
                    <div className="version-lock-controls">
                      <span className="field-hint">Picking locks the viewed version and removes the others from this campaign. </span>
                      <button className="subtle" onClick={runPick}>Pick this version</button>
                    </div>
                  ) : (
                    <span className="field-hint">Single version; it locks when first used in a scene.</span>
                  )
                )}
              </div>
            </div>

            {/* Pinned, and the argument the whole layout is making. Which way
                it points depends on the scope, and the two are opposites: a
                world record is shared by every campaign built on this world,
                while a campaign's copy is a fork that leaves the world's
                original alone. Getting this backwards is exactly the mistake
                the split exists to prevent. */}
            <p className={"reach-warning" + (worldScope ? " shared" : "")}>
              {worldScope
                ? "Edits here reach every campaign using this world."
                : "This campaign's own copy. Edits here leave the world record untouched."}
            </p>
          </aside>

          <section className="card-pane" aria-label="Character card">
            <div className="pane-stamp">
              <span className="eyebrow">
                {worldScope ? "World record · shared" : "Campaign copy of the card"}
              </span>
              <span className="eyebrow">Sent to the model</span>
            </div>

            <div className="card-tabs" role="tablist" aria-label="Card">
              {([["card", "Card", null],
                 ...(onOpenLore ? [["lore", "Lore", loreCount] as const] : []),
                 ["greetings", "Greetings", greetingCount],
                 ["art", "Art", artCount]] as [CardTab, string, number | null][])
                .map(([key, label, count]) => (
                  <button key={key} role="tab" aria-selected={tab === key}
                          className={"tab" + (tab === key ? " active" : "")}
                          onClick={() => setTab(key)}>
                    {label}{count === null ? "" : ` ${count}`}
                  </button>
                ))}
            </div>

            <div className="card-pane-body" role="tabpanel">
              {importMsg && <span className="field-hint">{importMsg}</span>}

              {tab === "card" && <>
                <div className="card-field">
                  <span className="data-label">Name</span>
                  <div className="card-field-body"><div className="detail-text">{name}</div></div>
                </div>

                {description.trim() && (
                  <div className="card-field">
                    <span className="data-label">Description</span>
                    {/* The cost of this one field, where it is being read. It
                        is the largest thing on the card and it goes out every
                        single turn she is on stage — which is the fact the
                        stamp exists to make legible before it is paid. */}
                    <span className="card-field-cost">
                      ≈ {estimateTokens(description).toLocaleString()} tokens · sent every turn she is in scene
                    </span>
                    <div className="card-field-body"><div className="detail-text">{description}</div></div>
                  </div>
                )}

                <div className="card-field-pair">
                  {filled(["personality", "scenario"]).map(cardField)}
                </div>
                {filled(["mes_example", "system_prompt", "post_history_instructions", "creator_notes"])
                  .map(cardField)}

                {tags.length > 0 && (
                  <div className="card-field">
                    <span className="data-label">Tags</span>
                    <div className="chip-row">
                      {tags.map((t) => <span className="chip on" key={t}>{t}</span>)}
                    </div>
                  </div>
                )}

                {(card.data.extensions?.sd_prompt) && (
                  <div className="card-field">
                    <span className="data-label">Image prompt</span>
                    <div className="field-hint">{card.data.extensions.sd_prompt}</div>
                  </div>
                )}

                {/* Not on the Greetings tab, and not counted by it: these are
                    separate world records that happen to feature her, the same
                    category as her tags — where this record sits in the world,
                    rather than anything the card carries. The ★ marks the ones
                    she is the primary of. Chips that navigate, per the
                    list/detail rule for metadata referencing other records. */}
                {featuring.length > 0 && (
                  <div className="card-field">
                    <span className="data-label">World greetings</span>
                    <div className="chip-row">
                      {featuring.map((g) => (
                        <button key={g.id} className="chip on" onClick={() => onOpenGreeting?.(g.id)}>
                          {g.character === detail.meta.id ? `★ ${g.name}` : g.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {worldScope && <div className="chub-source-block">
                  {chubSource ? (
                    <>
                      <a className="field-hint"
                         href={chubSource.startsWith("http") ? chubSource : `https://chub.ai/characters/${chubSource}`}
                         target="_blank" rel="noreferrer">
                        {chubSource}
                      </a>
                      <button className="subtle" type="button" onClick={redownloadFromChub}>Re-download</button>
                      <button className="subtle" type="button" onClick={unlinkChub}>Unlink</button>
                      {isChub && (
                        <>
                          <button className="subtle" type="button" disabled={!!galleryProg} onClick={downloadChubGallery}>
                            {galleryProg ? "Downloading…" : "Download gallery"}
                          </button>
                          <button className="subtle" type="button" onClick={downloadChubLorebooks}>Download linked lorebooks</button>
                          {galleryProg && (
                            <div className="localize-progress">
                              <progress value={galleryProg.done} max={galleryProg.total || 1} />
                              <span className="field-hint">{galleryProg.done}/{galleryProg.total}</span>
                            </div>
                          )}
                        </>
                      )}
                    </>
                  ) : (
                    <button className="subtle" type="button" onClick={linkChub}>Link to URL</button>
                  )}
                </div>}

                {module && detail && (
                  <SheetPanel scope={scope} module={module} kind="characters" eid={detail.meta.id} />
                  /* onOpenRef intentionally unset here: no cross-editor navigation target exists
                     yet from a character/PC sheet's ref chips (entity-form refs only; module-content
                     ref chips still preview correctly without it) */
                )}
              </>}

              {tab === "lore" && onOpenLore && (
                <OwnedLorePanel
                  scope={scope}
                  ownerRef={`characters:${detail.meta.id}`}
                  onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                  onNewEntry={() => onOpenLore({ newOwner: `characters:${detail.meta.id}` })}
                />
              )}

              {tab === "greetings" && <>
                {firstMes.trim() && (
                  <div className="card-field">
                    <span className="data-label">First message</span>
                    <div className="card-field-body"><GreetingMarkdown>{firstMes}</GreetingMarkdown></div>
                  </div>
                )}
                {greetings.length > 0 && (
                  <div className="card-field">
                    <span className="data-label">Alternate greetings</span>
                    {greetings.map((g, i) => (
                      <blockquote className="greeting-quote" key={i}>
                        <GreetingMarkdown>{g}</GreetingMarkdown>
                      </blockquote>
                    ))}
                  </div>
                )}
                {greetingCount === 0 && (
                  <p className="empty-state">
                    No greetings on this card. A greeting is the <span className="empty-what">opening
                    a scene can start from</span> — add one from Edit, or import a card that carries
                    some. World greetings that merely feature {name} are listed on the card tab.
                  </p>
                )}
              </>}

              {tab === "art" && <>
                <div className="card-field">
                  <span className="data-label">Images</span>
                  <div className="images-shelf">
                    {hasAvatar ? (
                      <figure className="shelf-tile avatar-tile">
                        <a href={avatarSrc(detail.meta.id, vid, imageTokens.avatar)} target="_blank" rel="noreferrer">
                          <img alt="avatar" src={avatarSrc(detail.meta.id, vid, imageTokens.avatar)} />
                        </a>
                        <figcaption>avatar</figcaption>
                        <ImageDescriptionField name="avatar" value={descriptions.avatar}
                                               onSave={(d) => describeImage("avatar", d)}
                                               onDraft={worldScope ? () => draftDescription("avatar") : undefined} />
                      </figure>
                    ) : (
                      <div className="shelf-tile shelf-empty">no avatar</div>
                    )}
                    {galleryImages.map((imgName) => {
                      const src = withToken(
                        api.actorImageUrl(scope, "characters", detail.meta.id, vid, imgName),
                        imageTokens[imgName]);
                      return (
                        <div className="shelf-tile" key={imgName}>
                          <a href={src} target="_blank" rel="noreferrer"><img alt={imgName} src={src} /></a>
                          <button className="shelf-promote" onClick={() => promote(imgName)}>Set as avatar</button>
                          <ImageDescriptionField name={imgName} value={descriptions[imgName]}
                                                 onSave={(d) => describeImage(imgName, d)}
                                                 onDraft={worldScope ? () => draftDescription(imgName) : undefined} />
                        </div>
                      );
                    })}
                    <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                    <input ref={shelfFileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden
                           aria-label="Add image" onChange={onShelfAdd} />
                  </div>
                </div>

                {imageAppearances.length > 0 && (
                  <div className="card-field">
                    <span className="data-label">Appears in</span>
                    <div className="images-shelf">
                      {imageAppearances.map((a) => (
                        <div className="shelf-tile" key={`${a.gid}/${a.name}`}>
                          <a href={a.url} target="_blank" rel="noreferrer">
                            <img alt={`${a.greeting_name} art`} src={a.thumb ?? a.url} />
                          </a>
                          <button className="shelf-promote" onClick={() => copyFromGreeting(a, "avatar")}>Set as avatar</button>
                          <button className="shelf-promote" onClick={() => copyFromGreeting(a, "gallery")}>Add to gallery</button>
                          {onOpenGreeting && (
                            <button className="shelf-promote" onClick={() => onOpenGreeting(a.gid)}>{a.greeting_name}</button>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {worldScope && localizeControls(false)}
              </>}
            </div>

            <div className="card-pane-actions">
              <button className="primary" onClick={() => setMode("edit")}>Edit</button>
              {worldScope && <ExportMenu wid={wid} cid={detail.meta.id} vid={vid} />}
              {worldScope && <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>}
            </div>
          </section>

          <CampaignPane worldScope={worldScope} label={campaignLabel} name={name} state={campaignState} />
        </div>
      </div>
    );
  }

  // mode === "edit"
  return (
    <div className="character-editor">
      {taglineQueue.length > 0 && (
        <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                       onSaved={(t) => { setTagline(t); reload(); }}
                       onClose={() => setTaglineQueue((q) => q.slice(1))} />
      )}
      <div className="editor-body">
        <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
        <div className="form">
          {error != null && <div className="banner"><ErrorNote err={error} /></div>}
          <div className="picker">
            <select value={vid} onChange={(e) => loadVersion(detail, e.target.value)} aria-label="Version">
              {detail.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                </option>
              ))}
            </select>
            {(worldScope || !locked) && <button className="subtle" onClick={addVersion}>+ Version</button>}
            {worldScope && <>
              <button className="subtle" onClick={() => versionFileRef.current?.click()}>Import version</button>
              <input ref={versionFileRef} type="file" accept=".json,.png,.charx" hidden
                     aria-label="Import version" onChange={onImportVersion} />
              <ExportMenu wid={wid} cid={detail.meta.id} vid={vid} />
            </>}
            <button className="subtle" onClick={setDefault}>Set default</button>
            {worldScope && <>
              <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
              <button className="subtle" onClick={downloadVersionFromChub}>Download version from URL</button>
            </>}
            {importMsg && <span className="field-hint">{importMsg}</span>}
          </div>

          <div className="avatar-block">
            {hasAvatar ? (
              <img className="avatar" alt="avatar" src={avatarSrc(detail.meta.id, vid, imageTokens.avatar)} />
            ) : (
              <div className="avatar avatar-empty" aria-label="no avatar">no avatar</div>
            )}
            <div className="avatar-actions">
              <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                {hasAvatar ? "Replace" : "Upload"}
              </button>
              {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
              <input ref={avatarRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden
                     aria-label="Upload avatar" onChange={onAvatar} />
            </div>
          </div>

          {worldScope && localizeControls(dirty, "Save your changes before localizing images")}

          <Field label="Name">
            <input type="text" value={card.data.name ?? ""} onChange={(e) => setField("name", e.target.value)} />
          </Field>
          <Field label="Creator">
            <input type="text" value={card.data.creator ?? ""} onChange={(e) => setField("creator", e.target.value)} />
          </Field>
          {worldScope && <>
            <Field label="Birthdate">
              {/* Persist only complete dates: the picker emits "" for every
                  intermediate state, which must never blank the stored value.
                  (Tradeoff: retyping the year with month/day set can briefly
                  persist a transient valid year — accepted, no debouncing.) */}
              <CalendarDatePicker scope={{ kind: "world", id: wid }} value={birthdate}
                                  onChange={(v) => { setBirthdate(v); if (v) saveBirthdate(v); }}
                                  ariaLabel="Birthdate" />
              {birthdate && <button className="subtle" type="button"
                                    onClick={() => saveBirthdate("")}>Clear</button>}
            </Field>
            <Field label="Tagline" hint="one-line identity for the off-scene cast">
              <textarea aria-label="Tagline" value={tagline} rows={2}
                        onChange={(e) => setTagline(e.target.value)} />
            </Field>
            <div className="form-actions">
              <button className="subtle" type="button" disabled={taglineBusy} onClick={regenerateTagline}>
                {taglineBusy ? "Generating…" : "Generate"}
              </button>
              <button className="subtle" type="button" onClick={saveTagline}>Save tagline</button>
            </div>
          </>}
            <Field label="Voice anchor"
                   hint="how they SOUND — absorb checks each scene against this and flags drift; clear it to skip the check">
              {/* Disabled while BUSY as well as while loading: a generation in
                  flight will overwrite this box when it lands, so edits made
                  meanwhile would be silently discarded. */}
              <textarea aria-label="Voice anchor" value={voiceAnchor} rows={5}
                        disabled={anchorState === "loading" || anchorBusy}
                        onChange={(e) => setVoiceAnchor(e.target.value)} />
            </Field>
            {anchorState === "error" && (
              <p className="field-hint">Could not load the voice anchor — reopen this
                character to try again. Saving is disabled so a failed read cannot
                overwrite the stored anchor with a blank.</p>)}
            <div className="form-actions">
              {/* Also disabled while the initial GET is in flight: generating
                  bumps the request token and invalidates that load, so a
                  generation that then fails would strand `anchorState` in
                  "loading" — every control disabled, no error shown, and no way
                  out but reopening the character. */}
              <button className="subtle" type="button"
                      disabled={anchorBusy || anchorSaving || anchorState === "loading"}
                      onClick={regenerateVoiceAnchor}>
                {anchorBusy ? "Generating…" : "Generate"}
              </button>
              {/* Disabled while generating too, not just while loading. A save
                  that lands mid-generation persists the OLD text, and the
                  completion then replaces the textarea with a fresh draft — so
                  the save the user just watched succeed covers a value that is
                  no longer on screen, and the draft they can see is unsaved. */}
              <button className="subtle" type="button"
                      disabled={anchorState !== "ready" || anchorBusy || anchorSaving}
                      onClick={saveVoiceAnchor}>
                {anchorSaving ? "Saving…" : "Save voice anchor"}
              </button>
            </div>
          <Field label="Tags" hint="comma-separated">
            <input
              type="text"
              value={(card.data.tags ?? []).join(", ")}
              onChange={(e) => setField("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
            />
          </Field>
          {TEXT_FIELDS.map((f) => (
            <Field key={f.key} label={f.label}>
              <textarea
                value={(card.data[f.key] as string) ?? ""}
                rows={f.key === "description" ? 6 : 3}
                onChange={(e) => setField(f.key, e.target.value)}
              />
            </Field>
          ))}
          <Field label="Alternate greetings" hint="each greeting may span multiple lines">
            <div className="greeting-list">
              {greetings.map((g, i) => (
                <div className="greeting-row" key={i}>
                  <textarea
                    aria-label={`Greeting ${i + 1}`}
                    value={g}
                    rows={3}
                    onChange={(e) => setGreetings(greetings.map((x, j) => (j === i ? e.target.value : x)))}
                  />
                  <button className="subtle" type="button"
                          onClick={() => setGreetings(greetings.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
              ))}
              <button className="subtle" type="button" onClick={() => setGreetings([...greetings, ""])}>
                + Add greeting
              </button>
            </div>
          </Field>

          {worldScope && bookCount > 0 && (
            <div className="book-import">
              <button className="subtle" type="button" disabled={dirty} onClick={importBook}>
                Import {bookCount} embedded lore {bookCount === 1 ? "entry" : "entries"} to world
              </button>
              {/* The import reads the stored card, so while the form is dirty the
                  entries it would commit are not the ones the editor is showing. */}
              {dirty && <span className="field-hint">Save your changes before importing embedded lore</span>}
              {bookMsg && <span className="field-hint">{bookMsg}</span>}
            </div>
          )}

          <div className="form-actions">
            <button className="primary" onClick={save}>Save version</button>
          </div>
        </div>
      </div>
    </div>
  );
}
