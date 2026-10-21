/** Scene play: the scene list and transcript, the turn producers, responses
 *  and alternates, dice, the prompt inspector's reads, rolling summaries,
 *  scene breaks and suggestions, message edits and retcon replays. Spread
 *  into `api` by `client.ts`. */
import { notifyShell } from "../notify";
import { draftRun } from "../runs";
import type { ChatEvent, RunHandle } from "../stream";
import { request, streamPost } from "../transport";
import type { Briefing, CascadeReport, Casefile, CastDetail, CheckResolution,
  PassageCharacterDraft, PassageCharacterInput, PassageCharacterSave, PinRule, PromptDiff,
  PromptEntry, PromptFieldStatuses, PromptSnapshot, ProposalRecord, RegenerateOverrides,
  ReplayPreview, ReplaySession, ReplayStarted, ResponseOverride, ResponseRecord, ResponseSwipe,
  RetconReport, RollEntry, RollingSummary, RollingSummaryRefresh, Scene, SceneAlternates,
  SceneBreak, SceneBreakAnswer, SceneCheckActor, SceneContext, SceneIdea, SceneIdeaDraft,
  SceneIntentResult, SceneMeta, ScenePage, SceneRewrite, SceneSuggestion,
  SceneSuggestionsOptions } from "../types";

export const scenesApi = {
  // scenes
  // Never coalesced, like the scene, alternates and proposal reads above — and
  // opted out for every caller rather than at each mutation's call site, which
  // is the same lesson those three record.
  //
  // The scene list decides which sid the URL may name (#87), and `CampaignView`
  // orders its reads by when they were ISSUED so a superseded one cannot
  // install over a newer. A shared read breaks that ordering rather than merely
  // being stale: it is as old as the request it joined, so a read issued after
  // a rename can be handed a promise from before it and still carry the newest
  // sequence number — retiring the genuinely post-rename relist and installing
  // a list with the old id in it. `fresh` at the caller's choice was tried, and
  // left exactly that hole for the one caller that did not pass it.
  //
  // To do also reads this uncapped list for its active scenes. It needs the
  // same freshness: a completed scene must not survive a post-write refresh.
  listScenes: (cid: string) =>
    request<SceneMeta[]>("GET", `/api/campaigns/${cid}/scenes`, undefined, { fresh: true }),
  createScene: (cid: string, title?: string, suggestedDate?: string, pcless?: boolean, focus = "") =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`,
      { title, suggested_date: suggestedDate, pcless, focus }).then(notifyShell),
  // Never shared, like the alternates and proposal reads. `selectScene` is the
  // refresh every mutating path funnels through, and a shared read is as old as
  // the request it joined — so a reroll or swap firing while an earlier refresh
  // was open would pair a fresh alternate set with a PRE-mutation transcript,
  // and the readiness gate cannot tell: the transcript carries the new window
  // token either way. Opted out for every caller rather than at each mutation's
  // call site, because a rule each caller has to remember is one the next
  // caller forgets.
  getScene: (cid: string, sid: string, window?: { limit: number; before?: number }) => {
    const qs = window
      ? "?" + String(new URLSearchParams({
          limit: String(window.limit),
          ...(window.before === undefined ? {} : { before: String(window.before) }),
        }))
      : "";
    return request<ScenePage>("GET", `/api/campaigns/${cid}/scenes/${sid}${qs}`,
                              undefined, { fresh: true });
  },
  renameScene: (cid: string, sid: string, title: string) =>
    request<{ id: string; title: string }>("PUT", `/api/campaigns/${cid}/scenes/${sid}`,
                                           { title }).then(notifyShell),
  deleteScene: (cid: string, sid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}`).then(notifyShell),
  /** Branch a scene from a post into a sibling (play controls III): the
   *  sibling keeps the transcript through `through`. `notifyShell`, because a
   *  new scene is a new open scene on the rail. */
  branchScene: (cid: string, sid: string, through: number, title?: string) =>
    request<{ id: string; scene: ScenePage }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/branch`,
      { through, ...(title ? { title } : {}) }).then(notifyShell),

  // `response` is a one-shot, unpersisted per-turn override (the length chip
  // beside Send) — rides only this call, exactly like regenerate's guidance.
  // `director` (#83) says this turn is a director note rather than a player
  // post: sent, never stored. Omitted from the body unless set, so the server
  // sees the same request every earlier client sent and its own inference —
  // an offscreen scene, or an empty send — still decides those.
  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void,
         response?: ResponseOverride, signal?: AbortSignal, attempt?: string,
         onIndex?: (i: number) => void, director?: boolean, speakerRef?: string) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`,
               { content, ...(response ? { response } : {}), ...(director ? { director: true } : {}), ...(speakerRef ? { speaker_ref: speakerRef } : {}) },
               onEvent, signal, attempt, onIndex),

  // `attempt`/`onIndex` ride every turn producer, not just `chat`. All five are
  // detached server-side, so a call that omits the attempt lets the server mint
  // its own -- and then the id this client recorded names a run the server never
  // had. Stop addresses nothing and recovery asks about an attempt that does not
  // exist, which is precisely the window the registry was built for (codex, P1).
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, response?: ResponseOverride,
          signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`,
               response ? { response } : undefined, onEvent, signal, attempt, onIndex),
  /** Reroll the trailing generation.
   *
   *  `body` is one object rather than a parameter each, and that is not
   *  cosmetic: every field of it is optional and the `signal` sits behind
   *  them, so a plain reroll had to spell out an `undefined` per field to
   *  reach it — and the caller, which decides them independently, needed one
   *  branch per COMBINATION. Two fields was already four branches; the route
   *  override (#77) would have made it eight.
   *
   *  Empty fields are dropped rather than sent, so an untouched popover posts
   *  no body at all — `""` and "unset" mean the same thing for every field
   *  (guidance, response, and the route's provider, model and preset), and
   *  the server reads a missing field as the standing configuration. Falsy is
   *  the test because every field here is a string or an object; a field whose
   *  `false` or `0` meant something would need its own rule rather than this
   *  one, and would be wrong to add without one.
   */
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void,
               body?: RegenerateOverrides, signal?: AbortSignal, attempt?: string,
               onIndex?: (i: number) => void) => {
    const payload = Object.fromEntries(
      Object.entries(body ?? {}).filter(([, v]) => Boolean(v)));
    return streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
                      Object.keys(payload).length ? payload : undefined,
                      onEvent, signal, attempt, onIndex);
  },

  // Reroll alternates: every variant of the generation a reroll would replace,
  // `active` being the one the transcript is showing (null once a reroll's
  // stream died and left the slot empty). Previews only — picking one is what
  // brings its full text back, as transcript.
  // Never coalesced, for the same reason `getRollProposal` is not. `fetchAlternates`
  // stamps the answer with the window token current when it *issued* the read —
  // that is the whole readiness gate — and a shared read is as old as the request
  // it joined. A reroll or swap firing while an earlier GET is still open would
  // otherwise attach that older set to the newer transcript: the counter names
  // the wrong active take, and an arrow promotes a still-valid but wrong id.
  getResponse: (cid: string, sid: string, rid: string) =>
    request<ResponseRecord>("GET", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}`),
  // Never coalesced, like `getAlternates` below: the swipe hook re-reads after
  // every activate, reroll and delete, and a shared read is as old as the
  // request it joined -- the pre-mutation state, naming the replaced take.
  getResponseSwipe: (cid: string, sid: string, rid: string) =>
    request<ResponseSwipe>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/swipe`, undefined, { fresh: true }),
  deleteResponse: (cid: string, sid: string, rid: string) =>
    request<Scene>("DELETE", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}`),
  activateResponseVariant: (cid: string, sid: string, rid: string, vid: string) =>
    request<Scene>("POST", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/variants/${vid}/activate`),
  regenerateResponse: (cid: string, sid: string, rid: string, onEvent: (e: ChatEvent) => void,
    body?: RegenerateOverrides, signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/regenerate`, body ?? {}, onEvent, signal, attempt, onIndex),
  /** Keep writing: continue the trailing response, landing as a new variant.
   *  The same overrides and stream as `regenerateResponse`. */
  extendResponse: (cid: string, sid: string, rid: string, onEvent: (e: ChatEvent) => void,
    body?: RegenerateOverrides, signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/extend`, body ?? {}, onEvent, signal, attempt, onIndex),
  passageCharacterEvidence: (cid: string, sid: string, rid: string, body: PassageCharacterInput) =>
    request<Pick<PassageCharacterDraft, "quotes" | "mes_example">>("POST", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/character-evidence`, body),
  draftPassageCharacter: (cid: string, sid: string, rid: string, body: PassageCharacterInput, signal?: AbortSignal) =>
    draftRun<PassageCharacterDraft>({ at: "campaign", id: cid }, (attempt) =>
      request<{ run: RunHandle }>("POST", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/character-draft`,
        body, { attempt, signal }), { signal }),
  savePassageCharacter: (cid: string, sid: string, rid: string, body: PassageCharacterSave) =>
    request<{ character: string; version: string; name: string }>("POST", `/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/character`, body),
  getAlternates: (cid: string, sid: string) =>
    request<SceneAlternates>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/alternates`, undefined, { fresh: true }),
  pickAlternate: (cid: string, sid: string, id: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/alternates/${id}`),

  // dice rolls
  roll: (cid: string, sid: string, notation: string, label?: string) =>
    request<{ ok: boolean; roll: RollEntry; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/roll`,
      { notation, ...(label ? { label } : {}) }),
  // Never coalesced, for every caller. `CampaignView` orders proposal writes by
  // the order their reads were *issued*, and a shared read is as old as the
  // request it joined rather than as new as the claim it was handed — so a
  // newer claim could be attached to an older answer and outrank a fresher one
  // (#95). Opting the endpoint out restores the invariant that ordering assumes
  // instead of guarding each place the mismatch shows up. It costs nothing:
  // sharing exists for lookups several components fire at once, and this one
  // has a single caller.
  getRollProposal: (cid: string, sid: string) =>
    request<{ record: ProposalRecord | null }>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, undefined, { fresh: true }),
  resolveProposal: (cid: string, sid: string,
                    body: { proposal: string; action: "accept" | "decline";
                            check?: string; actor?: string;
                            difficulty?: number; modifier?: number },
                    onEvent: (e: ChatEvent) => void, signal?: AbortSignal,
                    attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, body, onEvent, signal,
               attempt, onIndex),
  getSceneChecks: (cid: string, sid: string) =>
    request<{ actors: SceneCheckActor[] }>("GET", `/api/campaigns/${cid}/scenes/${sid}/checks`),
  rollCheck: (cid: string, sid: string,
              body: { check: string; actor: string; difficulty?: number; modifier?: number }) =>
    request<{ ok: boolean; resolution: CheckResolution; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/check`, body),
  // A caller asking for a newly composed revision must not join a GET that
  // started before its write. Other consumers retain ordinary GET sharing.
  getSceneContext: (cid: string, sid: string, options?: { fresh?: boolean }) =>
    request<SceneContext>("GET", `/api/campaigns/${cid}/scenes/${sid}/context`, undefined, options),
  // Pins are campaign-scoped with the scene as a parameter: one read returns
  // this scene's rules and the campaign-wide ones they override. `fresh`,
  // because a pin is read straight after being set and a cached list would show
  // the state before the write that prompted the reload.
  getPins: (cid: string, sid: string) =>
    request<{ pins: PinRule[] }>("GET",
      `/api/campaigns/${cid}/pins?sid=${encodeURIComponent(sid)}`, undefined, { fresh: true }),
  setPin: (cid: string, body: { ref: string; mode: "pin" | "exclude";
                                scope?: "scene" | "campaign"; sid?: string; ttl_posts?: number }) =>
    request<{ ok: boolean; pin: PinRule }>("POST", `/api/campaigns/${cid}/pins`, body),
  removePin: (cid: string, ref: string, scope: "scene" | "campaign", sid: string) =>
    request<{ ok: boolean }>("DELETE",
      `/api/campaigns/${cid}/pins?ref=${encodeURIComponent(ref)}`
      + `&scope=${scope}&sid=${encodeURIComponent(sid)}`),
  // `fresh`, like `campaignLedger`: a briefing is a continuity view, and the
  // one moment it is read is right after the previous scene's save — exactly
  // when a cached copy would still be showing the commitment that save resolved.
  sceneBriefing: (cid: string, sid: string) =>
    request<Briefing>("GET", `/api/campaigns/${cid}/scenes/${sid}/briefing`,
                      undefined, { fresh: true }),
  // `fresh`, like `campaignLedger` and `sceneBriefing`: this is re-read on the
  // refreshKey a completed generation bumps, and the turn that generation just
  // captured is the entire reason for the re-read. A shared in-flight GET from
  // before the turn would answer it with a list that cannot contain the new
  // row, leaving Turn history a turn behind until some later refresh.
  listScenePrompts: (cid: string, sid: string) =>
    request<{ entries: PromptEntry[] }>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts`, undefined, { fresh: true }),
  // Deliberately NOT `fresh`: a snapshot is frozen by construction, so two
  // readers of one entry can share an answer that cannot go stale.
  getScenePrompt: (cid: string, sid: string, eid: string) =>
    request<PromptSnapshot>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts/${eid}`),
  // The campaign-level captures (roadmap 01b): decisions asked with no scene,
  // today the continuity reconcile sweep's. The list is `fresh` and an entry
  // is not, for the scene routes' reasons above.
  listCampaignPrompts: (cid: string) =>
    request<{ entries: PromptEntry[] }>(
      "GET", `/api/campaigns/${cid}/prompts`, undefined, { fresh: true }),
  getCampaignPrompt: (cid: string, eid: string) =>
    request<PromptSnapshot>("GET", `/api/campaigns/${cid}/prompts/${eid}`),
  // `against` is another entry id, or "live" for the composition as it stands
  // now — the comparison the feature is named for (#130). `fresh`, unlike
  // `getScenePrompt` above and for the reason that route is not: against "live"
  // only one end is frozen, so the answer moves with the store, and it is
  // re-read on the refreshKey a completed turn bumps precisely because that
  // turn is what moved it. Turn-against-turn could be shared, but one cache
  // rule for one route is worth more than the request it would save.
  getScenePromptDiff: (cid: string, sid: string, eid: string, against: string) =>
    request<PromptDiff>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts/${eid}/diff`
             + `?against=${encodeURIComponent(against)}`, undefined, { fresh: true }),
  // `fresh`, like `sceneBriefing`: the panel re-reads this immediately after the
  // automatic POST commits, which is exactly when a shared in-flight GET issued
  // *before* that write would hand back a pre-write answer — and the read token
  // would then install it as the newest word, hiding a summary that is durable
  // on the server with no later reread scheduled.
  getRollingSummary: (cid: string, sid: string) =>
    request<RollingSummary>("GET", `/api/campaigns/${cid}/scenes/${sid}/rolling-summary`,
                            undefined, { fresh: true }),
  /** Ask the server to refold the summary. Without `force` it is a no-op
   *  unless enough posts have landed, which is why the play loop can fire it
   *  after every turn: the gate is the server's, not the caller's. */
  /** `upto` bounds the fold to a transcript the caller knows was a clean
   *  boundary — the play loop releases the scene before firing this, so a fast
   *  next send can append a player post the fold would otherwise swallow, and
   *  the reply that answers it is only an append, so it would stay out of the
   *  "current" summary until another whole threshold went by. */
  refreshRollingSummary: (cid: string, sid: string, force = false, upto?: number) => {
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (upto !== undefined) params.set("upto", String(upto));
    const qs = params.toString();
    return request<RollingSummaryRefresh>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/rolling-summary${qs ? `?${qs}` : ""}`);
  },
  // `fresh`, like `getRollingSummary` and for its reason: the panel re-reads
  // this immediately after the automatic POST commits, which is exactly when a
  // shared in-flight GET issued *before* that write would hand back a pre-write
  // answer and install it as the newest word.
  getSceneBreak: (cid: string, sid: string) =>
    request<SceneBreak>("GET", `/api/campaigns/${cid}/scenes/${sid}/scene-break`,
                        undefined, { fresh: true }),
  /** Ask the server whether the scene has reached a place to stop. Without
   *  `force` it is a no-op unless the heuristic agrees, which is why the play
   *  loop can fire it after every turn: the gate is the server's, not the
   *  caller's. `upto` bounds the question to a transcript the caller knows was
   *  a clean boundary — same hazard `refreshRollingSummary` documents, since a
   *  question that took an unanswered player post as the scene's END would be
   *  asking about a beat whose reply had not arrived. */
  askSceneBreak: (cid: string, sid: string, force = false, upto?: number) => {
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (upto !== undefined) params.set("upto", String(upto));
    const qs = params.toString();
    return request<SceneBreakAnswer>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/scene-break${qs ? `?${qs}` : ""}`);
  },
  /** "Not here" — retires the proposal and starts the count again from the
   *  scene as it stands, so the same posts cannot re-earn the same suggestion.
   *  Answers with the state it wrote. */
  dismissSceneBreak: (cid: string, sid: string) =>
    request<SceneBreak>("POST", `/api/campaigns/${cid}/scenes/${sid}/scene-break/dismiss`),
  /** One JSON body, every field defaulted, rather than a query string: the
   *  story-pressure controls are ref lists, and when a body is present the
   *  server reads it wholly and ignores the query (§16.3). `after` is sent only
   *  when set, so "no scene to follow" stays absent rather than `""`. */
  sceneSuggestions: (cid: string, opts: SceneSuggestionsOptions = {},
                     signal?: AbortSignal) => {
    const { after, ...rest } = opts;
    const body = {
      offscreen: false, direction: "", rank: true, focus_refs: [], avoid_refs: [],
      must_refs: [], time_mode: "auto", time_anchor_ref: "", time_anchor_relation: "",
      ...rest, ...(after ? { after } : {}),
    };
    return draftRun<{ suggestions: SceneSuggestion[]; greeting_picks?: string[];
                      next_date?: string; prompt_field_statuses?: PromptFieldStatuses }>(
      { at: "campaign", id: cid }, (attempt) =>
        request<{ run: RunHandle }>(
          "POST", `/api/campaigns/${cid}/scene-suggestions`, body, { attempt, signal }),
      { signal });
  },
  sceneIntent: (cid: string, text: string, offscreen: boolean, signal?: AbortSignal) =>
    draftRun<SceneIntentResult>({ at: "campaign", id: cid }, (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/campaigns/${cid}/scene-intent`, { text, offscreen },
        { attempt, signal }), { signal }),
  // The scene ledger (#88). `fresh`, like the continuity ledger: the picker
  // re-reads this precisely when it has just written to it (a save, a dismiss,
  // a restore), and a shared or cached response would answer that with the
  // list from before the write.
  // `greetings=false` declines the composed greeting half. Composing it parses
  // the frontmatter of every greeting in the campaign, and the picker -- the
  // only caller -- renders greetings from its own ranked `availableGreetings`
  // read and drops those rows. A management surface wants them and omits this.
  listSceneIdeas: (cid: string, greetings = true) =>
    request<SceneIdea[]>("GET",
      `/api/campaigns/${cid}/scene-ideas${greetings ? "" : "?greetings=false"}`,
      undefined, { fresh: true }),
  saveSceneIdea: (cid: string, idea: SceneIdeaDraft) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scene-ideas`, idea),
  setSceneIdeaStatus: (cid: string, lid: string,
                       status: "active" | "used" | "dismissed", scene?: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scene-ideas/${encodeURIComponent(lid)}`,
                             { status, ...(scene ? { scene } : {}) }),
  getCastDetail: (cid: string, sid: string, kind: string, id: string) =>
    request<CastDetail>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  // `fresh`, like the ledger and the briefing: this is a continuity view, and
  // the absorb that rewrote every file behind it has just run.
  getCasefile: (cid: string, sid: string, kind: string, id: string) =>
    request<Casefile>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}/casefile`,
                      undefined, { fresh: true }),
  // `restore` writes a stored rewrite's original back (spec 5.1): past the
  // macros and the store phase, and refused 409 `rewrite_stale` unless
  // `content` is exactly the recorded original of the message still there.
  editMessage: (cid: string, sid: string, index: number, content: string,
                opts?: { restore?: boolean }) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`,
                             opts?.restore ? { content, restore: true } : { content }),
  // What the store phase rewrote in this scene, by `response_id` or `post_id`.
  // `rules` are rule ids. `fresh`: it is read on opening a rewrite, to restore.
  getSceneRewrites: (cid: string, sid: string) =>
    request<Record<string, SceneRewrite>>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/rewrites`, undefined, { fresh: true }),
  // Hide a post from context, or return it. Refused on a roll, transition or
  // note line (400 `not_excludable`), an absorbed scene and an open round (409).
  setExcluded: (cid: string, sid: string, index: number, excluded: boolean) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}/excluded`,
                             { excluded }),
  // Cascade post-delete (#75): this post and everything after it, plus the
  // reversal of what the scene wrote. The reply is a report, not an ack — a
  // record the compare-and-swap refused to put back is the one thing the
  // transcript afterwards cannot show, so the caller has to be able to say so.
  deleteMessagesFrom: (cid: string, sid: string, index: number) =>
    request<CascadeReport>("DELETE", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`),
  // Retcon (#78): the same rewrite `editMessage` makes, plus the reversal of
  // what this scene's absorb wrote and the clearing of `done`, so the scene can
  // be extracted again over the text that is now there. The two are separate
  // calls rather than a flag because they are different intentions — a typo fix
  // must not un-absorb a finished scene.
  retconMessage: (cid: string, sid: string, index: number, content: string) =>
    request<RetconReport>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}/retcon`,
      { content }).then(notifyShell),
  // Retcon replay (#79). `fresh` for every read here, like the alternates and
  // proposal reads: the session moves with each step of the walk, and a shared
  // read is as old as the request it joined.
  replayPreview: (cid: string, sid: string, index: number) =>
    request<ReplayPreview>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/replay/preview?index=${index}`,
      undefined, { fresh: true }),
  getReplay: (cid: string, sid: string) =>
    request<ReplaySession | null>("GET", `/api/campaigns/${cid}/scenes/${sid}/replay`,
                                  undefined, { fresh: true }),
  // Answers with the session in the same shape `getReplay` does, plus the
  // cascade's report — the backlog is never on the wire, however this is asked.
  // `branch` replays inside a sibling of the scene, leaving the original
  // untouched; the answer then names the sibling under `branched`.
  startReplay: (cid: string, sid: string, index: number, branch = false) =>
    request<ReplayStarted>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/replay`,
      { index, ...(branch ? { branch: true } : {}) }),
  // Streams like `chat` and for the same reason: it re-posts the player's own
  // words and then generates one reply against the edited history. Rerolling
  // that reply is plain `regenerate` — it is the trailing run.
  replayTurn: (cid: string, sid: string, onEvent: (e: ChatEvent) => void,
               signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/replay/turn`, undefined, onEvent, signal,
               attempt, onIndex),
  acceptReplay: (cid: string, sid: string) =>
    request<ReplaySession | null>("POST", `/api/campaigns/${cid}/scenes/${sid}/replay/accept`),
  // `restore` defaults to putting the unreplayed originals back — sent
  // explicitly so the destructive answer is never the one a dropped field
  // produces.
  cancelReplay: (cid: string, sid: string, restore: boolean) =>
    request<{ scene: string; restored: number; dropped: number }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/replay/cancel`, { restore }),
};
