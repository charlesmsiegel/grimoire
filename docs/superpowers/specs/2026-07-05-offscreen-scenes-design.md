# Offscreen scenes (PC-less) — design

**Date:** 2026-07-05
**Status:** Approved

## Goal

Scenes that don't include the player character, for establishing NPC motivations
and actions — the villain plotting, allies talking behind the hero's back, events
the PC never witnesses. The user drives them as a **director**: they can nudge the
scene with out-of-scene steering notes or simply let the NPCs keep going.

## Decisions (from brainstorming)

- **Driving model:** director mode. The user never speaks as a character; they
  optionally type steering notes ("the guard grows suspicious", "skip to
  nightfall") or hit Continue to generate the next passage.
- **Intent is explicit:** a scene-level `pcless` flag, set at creation. Not
  inferred from cast count.
- **Director notes are ephemeral:** they steer exactly one generation and are
  never stored in the script. The transcript stays pure fiction — assistant
  posts only. (Consequence, accepted: past notes are not re-seen by the model
  on later turns, and there is no record of how the scene was steered.)
- **Full greeting support:** greetings gain a `pcless` variant so world authors
  can write canonical offscreen openers.
- **Chooser UX:** mode is picked first ("With your PC" vs "Offscreen — NPCs
  only"), then the chooser shows only matching cards.
- **Campaign PC as reference:** offscreen prompts include the campaign's PC(s)
  as reference — known to the world but **not present**; may be discussed,
  never appears.

## 1. Data model & backend flow

### Scene flag

- New optional scene frontmatter key `pcless: true` (absent → false; no
  migration for existing scenes). Round-trips through
  `store/scenes.py` read/write.
- `POST /campaigns/{cid}/scenes` (create) accepts `pcless: bool = False` and
  stamps it. `list_scenes` / `read_scene` include it in responses.
- The scene body format is unchanged.

### Cast invariant

- A pcless scene never has a `role=player` actor seated. The scene-cast
  endpoints (`post_scene_cast` and the batch variant) reject seating a player
  into a pcless scene with a 400. Characters can only be cast as `npc`.

### Director turn (chat route)

For pcless scenes the chat endpoint changes behavior:

- The user's text is **optional**. Empty input becomes the default instruction
  `"Continue the scene."`.
- The director note is injected as the final user message of that single LLM
  call and is **never appended to the scene file**.
- The streamed reply is split and stored per-speaker exactly as today
  (`split_reply`). The forged-line guard no-ops (no players in cast), and
  `stamp_user_speaker` stays inert.
- Because the script on disk contains only assistant posts, speaker plates,
  `match_name`, and role derivation need zero changes.
- **Empty send in ANY scene** (not just pcless) is the same ephemeral
  mechanism: an unstored "Continue the scene." turn that generates the next
  NPC round. The composer's send button reads **Continue ▶** whenever the box
  is empty, in every scene.

## 2. Prompt assembly (`store/context.py`)

- `_assemble` adds an **"Offscreen scene"** system section when the scene is
  pcless, stating: this scene contains no player character; the user's messages
  are out-of-scene director's notes — follow them, never acknowledge them in
  the fiction, never address the director; the player character(s) listed below
  are known to the world but **not present** — they may be discussed or
  referenced, but must never appear, speak, or act in this scene.
- **PC reference block:** the campaign's player actor(s) (any `role=player`
  appearance in the campaign, regardless of scene membership) render their
  persona block into the offscreen section as reference, clearly marked not
  present. This lets NPCs plot about the hero with full knowledge of who they
  are.
- **Two name sources, kept distinct:** scene `player_names` stays empty (so
  the existing "never write dialogue or actions for: <players>" clause is
  skipped — its job is taken over by the stronger "never appears" rule in the
  Offscreen section). The **campaign-level** PC names (same source as the
  reference block) are what populate the `{{user}}` substitution for pcless
  scenes, so lore/cards referencing `{{user}}` read naturally. The
  `_substitute` mechanism itself is unchanged; only the subs dict it receives
  is built from campaign PCs instead of (empty) scene players.

## 3. Greetings

- Greeting frontmatter gains `pcless: true` (absent → false), parsed and
  serialized by `store/greetings.py`.
- **Availability:** `requires_tags` continues to evaluate against the campaign
  PC's tags — the campaign always has a PC; only the *scene* lacks one. No
  gating changes.
- **Starting:** `start_from_greeting` on a pcless greeting creates the scene
  with `pcless: true`. It already never seats a PC. `{{user}}` in a pcless
  greeting body substitutes to the campaign PC's **name**, so authors can write
  "while {{user}} sleeps, the cult convenes…".
- **Authoring:** the greeting editor form gets an "Offscreen" toggle; the
  read-only view shows it as a chip in the sidebar (per the list/detail
  pattern).

## 4. Frontend UX

- **NewSceneChooser:** a mode step comes first — two cards, "With your PC" and
  "Offscreen (NPCs only)". The chosen mode filters everything after it: the
  ranked greeting cards show only matching greetings, and the LLM suggestion
  prompt gets an offscreen variant (NPC-only premises grounded in campaign
  recaps). Picking anything in offscreen mode creates the scene with
  `pcless: true`.
- **Composer (CampaignView):** in pcless scenes the placeholder reads "Direct
  the scene (optional)…" and the send button reads **Continue ▶** when the box
  is empty. While streaming, the note renders as a dimmed transient 🎬 line;
  it disappears on the post-stream refetch because it was never stored.
- **CastPanel:** player seating is hidden for pcless scenes — no PC section,
  no "as player" role option for characters.
- **SceneInspector & scene list:** a small "Offscreen" badge identifies these
  scenes.

## 5. Edge cases

- Existing scenes and greetings: missing flag reads as `false`; nothing to
  migrate.
- Model writes the PC's lines anyway: handled at prompt level only ("not
  present — never appears"). The forged-line guard is deliberately **not**
  extended: NPCs quoting or discussing the PC as narrator prose is legitimate,
  and the PC is not in the scene cast to match against.
- Empty pcless scene (no messages yet): the existing CastPanel opener
  generation works as-is; the opener prompt path flows through the same
  offscreen-aware `_assemble`.
- Zero pcless greetings authored: the offscreen chooser path still offers
  LLM-suggested premises, so the mode is usable in every world.

## 6. Testing

Backend (pytest, `GRIMOIRE_HOME` isolated via `monkeypatch`):

- `pcless` scene flag round-trips through create/read/list.
- Chat on a pcless scene: user turn is not persisted; empty input sends
  "Continue the scene."; reply posts stored normally.
- `_assemble` on a pcless scene emits the Offscreen section with the PC
  reference block; normal scenes are byte-identical to before.
- Greeting `pcless` parses/serializes; `start_from_greeting` stamps the scene
  flag and substitutes `{{user}}` to the campaign PC name.
- Cast guard: seating `role=player` into a pcless scene → 400.

Frontend (vitest, run from `frontend/`):

- Chooser shows the mode step; offscreen mode lists only pcless greetings and
  creates a pcless scene.
- Composer shows the director placeholder and Continue ▶ on empty input;
  transient 🎬 note appears during streaming and not after refetch.
- CastPanel hides player seating for pcless scenes.
- GreetingEditor: Offscreen toggle in the form, chip in the view sidebar.
