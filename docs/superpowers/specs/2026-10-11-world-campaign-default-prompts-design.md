# World and campaign default prompts, with persistent scene focus

Date: 2026-10-11

Status: design approved by the owner on 2026-10-11; spec-to-planning adversarial
review approved on the same date with no material findings. The review checked
the supplied diff against public repository contracts; a Windows sandbox failure
prevented exact local-checkout inspection. Frequent checkpoint commits are
authorised. Implementation still requires the reviewed implementation plan
and its execution handoff.
Repository conventions and review gates are owned by `CLAUDE.md` and
`CONTRIBUTING.md`, not restated here.

## 1. Intent

Give a world and a campaign their own optional standing prompt: a concise
statement of shared setting premises and persistent steering that does not
need to be repeated in every character card or activated lore entry.

- **World default prompt:** the baseline for play in this world, shared by
  campaigns bound to it.
- **Campaign default prompt:** additional premises, explicit exceptions and
  standing instructions for this particular campaign.
- **Scene focus:** an optional statement of what this scene is exploring or
  gently moving towards, retained throughout play rather than only in its
  opener. Generate it alongside the rest of a generated scene draft, let the
  author review it before creation, and retain it in the scene's frontmatter.
  This is direction, not another setting-rule override.

All three reach the narrator and individual NPC calls. None depends on the
current cast, location, keyword scan or conversation topic. World/campaign
defaults establish standing context; scene focus suggests opportunities without
making an outcome mandatory. Guaranteed inclusion is not guaranteed fulfilment.

The principal benefit is one authoritative place to maintain shared rules.
Context savings are possible when an author consolidates semantically repeated
material and removes the redundant copies. Saving text in one place does not
make its tokens free: the text still occupies context in every applicable call.
Merely adding defaults while leaving existing copies intact increases context.
Scene focus primarily supplies continuity of purpose, not a token-saving claim.

This spec makes no measured token-saving or model-quality claim. Repository
examples and tests use the existing placeholder set only; no private library
content, names or measurements belong in the implementation artifacts.

## 2. Existing facilities and why this is separate

The relevant current contracts are:

- `store/context/assemble.py` assembles both scene narration and actor-scoped
  calls. A narrator can receive several character descriptions; an individual
  NPC call is narrowed to that NPC's card. Consolidation therefore does not
  remove several card copies from every kind of call.
- The global system prompt is installation-wide. Putting setting rules there
  would also affect unrelated worlds.
- The `world_overview` section renders the world's genre, tone, themes and
  description. It is `BACKGROUND`, so packing can drop it. An overview remains
  descriptive framing, not a protected standing-instruction slot.
- Lore has activation, ownership and secrecy rules. Its section can shed
  individual entries. Always-active, kept lore already provides a way to retain
  core facts, but also retains the lore-record machinery around those facts.
- Author's notes have campaign, scene and character scopes, with cadence and
  insertion depth in history. Their positioning and trimming behavior differ
  from a permanent system-prompt section. A scene author's note can already
  approximate a goal; scene focus adds explicit gentle-direction semantics and
  budget-protected inclusion without depth or cadence controls.
- The scene-start premise is handed to the opener box
  (`frontend/src/components/SceneConfirmForm.tsx`); it is not an independently
  retained scene goal in subsequent play prompts. Keep premise and focus
  separate: an opening situation is not itself an ongoing objective.
- `store/suggest.py` and the scene suggestion/intent templates already produce
  metadata before the confirmation form creates a scene. Extend those existing
  responses with focus rather than adding a second generation after creation.
- `store/frontmatter.py` is a dependency-light, string-scalar format, not full
  YAML. It supports one physical line per value. Scene focus fits as a compact
  paragraph; world/campaign defaults retain separate multiline text files.

Do not repurpose any of these fields, change their tiers, or migrate their text
automatically. In particular, neither `world.md`'s description nor
`campaign.md`'s pitch becomes a default prompt implicitly.

## 3. Scope and non-goals

Included in the first version:

- Read and edit one multi-line world default, one campaign default, and one
  optional single-paragraph focus per scene.
- Generate focus with scene suggestions and typed-scene metadata, carry it
  through saved ideas and confirmation, and persist the reviewed value in the
  initial scene write.
- Resolve world and campaign defaults and the current scene's focus for play.
- Render three identifiable, budget-protected prompt sections, with separate
  framing for scene direction rather than standing rules.
- Expose their actual rendered text and token contribution through existing
  context inspection and prompt capture.
- Preserve defaults and scene focus through the applicable copying, branch,
  bundle and backup workflows.
- Leave old stores and unset fields play-prompt-compatible without migration.

Not included:

- Automatic extraction of defaults from existing library content, semantic
  deduplication, card rewriting, or library cleanup. Enabling the feature must
  not silently remove existing instructions.
- Per-character, per-location or generic per-scene setting-default fields.
  Cards, activated lore and author's notes keep their existing jobs; scene focus
  is a narrow direction field, not a second scene fact sheet.
- Secret/GM-only variants, activation keys, frequency, history depth, priority
  sliders, or a new prompt hierarchy engine.
- A separate per-campaign copy or replacement of the inherited world default.
- Retrospective goal extraction from existing scenes, success detection,
  progress scores, goal-driven scene closure, or a model call to decide whether
  a focus was achieved.
- Automatic injection into every LLM utility. Summaries, absorb/audit passes,
  image prompts, voice/tagline drafts, decisions and embeddings keep their
  existing inputs. Scene suggestions and intent drafts are the narrow creation
  exception in section 5; no other utility is changed by implication.
- Full YAML, multiline frontmatter values, a separate scene-focus file, or an
  additional model call solely to generate focus.
- A promised improvement in provider caching, cost, or adherence.

## 4. Data and storage

World and campaign defaults use optional UTF-8 plain-text sidecars:

```text
<world root>/default_prompt.md
<campaign root>/default_prompt.md
```

Markdown is convenient for authoring, but the entire file is prompt text:
there is no frontmatter schema and no parsing of headings into separate rules.
These multiline defaults must not be squeezed into single-line frontmatter.
The files live with their world/campaign, not in global configuration or a cache.

Scene focus is the optional string-scalar key `focus` in the addressed scene's
existing Markdown frontmatter:

```text
---
title: A quiet conversation
focus: Offer Mara and Seraphine chances to rebuild trust without forcing reconciliation.
---
```

This uses the existing parser and serializer, including their quoting rules;
it does not introduce YAML block scalars (`|`/`>`), lists, or a new dependency.
The field belongs to the scene file itself, not an external record keyed by
scene ID or identity. Reads and focus-only saves need not mint an identity for
a legacy scene. Existing scene identity and branch protections remain unchanged.

The dedicated editing routes use the API field `text`. For world/campaign
defaults, nonblank saves preserve authored text apart from ordinary transport/
newline handling; rendering trims outer whitespace as other sections do.
Empty or whitespace-only saves clear a default by atomically publishing an
empty sidecar. A missing sidecar is also empty; reads create nothing.

Focus is deliberately one paragraph. At the store boundary, replace every line
boundary recognized by `frontmatter.LINE_BREAKS` with a space and trim outer
whitespace, without rewriting the remaining prose. Apply the same normalization
to generated drafts, creation requests, and later edits, and return the stored
normalized value. The editor makes this contract visible and shows normalized
pasted text before submission; soft visual wrapping is not a stored newline.
A blank save removes the `focus` key while keeping the scene. Missing/blank
focus reads as empty and creates or rewrites nothing. Scene creation omits the
key when the reviewed focus normalizes to empty.

Use existing root resolvers and safe-ID checks. A write requires an
existing world/campaign and, for focus, the addressed scene; it must not create
a new scope as a side effect. Campaign and scene-focus writes take
`locks.campaign_lock(cid)` and belong to the declared lock domain. All publication
goes through `store.atomic`. A focus edit holds the lock across the complete
scene read-modify-write, preserving the transcript body and all other metadata
under existing serialization conventions. It must not rewrite an unreadable,
unfenced or unterminated scene as a new focus-only record. A newly created scene
receives focus in its initial atomic scene-file publication, not a follow-up
PUT. No new dependencies or desktop-only path assumptions are needed.

Use shared store-owned readers, writers and resolution for these fields, with
focus owned by the scene store; routes, editors and prompt assembly must not
invent separate inheritance or scene metadata rules. These are scope-level
settings, not entities in the generic character/lore overlay or its sync
manifest. Existing entity reads continue to go through `store.overlay` unchanged.

An unreadable or undecodable default sidecar costs that scope's prompt, not the
entire generation. Preserve an explicit read status so an editor or inspector
can distinguish a read failure from an intentionally empty field. A failed
focus metadata read is likewise not an empty focus; omit that optional section
with a warning only if the surrounding composition can otherwise proceed.
This does not make an unreadable scene transcript usable or suppress existing
scene-read errors. Do not log the prose. An editor must not initialise a failed
read as an empty, saveable draft: retry first, or repair the file outside it.

Writes replace the whole field, not an inferred merge. Concurrent saves have
last-successful-publication behavior; this adds no compare-and-swap or
cross-device transaction guarantee. A failed publication leaves the previous
file intact. Activity stamping follows existing scope conventions, with a
post-publication stamp failure treated as secondary rather than a failed save
that encourages an unnecessary retry. Campaign-scoped saves participate in the
existing campaign revision-token behavior. A focus-only edit does not stamp
the scene's transcript `updated` time or modify transcript posts, response
captures, cast, location, or clock; it is an authored setting change.

## 5. Resolution, inheritance and meaning

For each fresh play-prompt composition:

1. Resolve the campaign's bound world through the existing campaign/world
   resolver, including its safe handling of an absent or unavailable world.
2. Read that world's default prompt, if available.
3. Read the campaign's own default prompt.
4. Read `focus` from only the addressed scene's frontmatter.
5. Render each nonempty, enabled section once.

The world default is **live inherited**, as the world overview is today.
Changing it affects future compositions in all campaigns bound to that world;
creating a campaign does not copy it. A campaign without a world, or whose world
is unavailable, still uses its own default. Never fall back to another world,
the store root, or the campaign directory as a substitute world.

The campaign default is **additive**, not a replacement. An empty campaign
default means no additional instructions; it does not suppress the world
default. There is no automatic comparison, subtraction or deduplication between
the two texts. Each scope renders once even if they contain identical prose.

The shipped templates communicate these meanings explicitly:

- World defaults describe shared baseline facts and standing behavior.
- Campaign defaults supplement that baseline. An explicit campaign-specific
  exception takes precedence over the corresponding world rule in this
  campaign; everything else in the world default continues to apply.
- Character and location descriptions supply specific details. They do not
  acquire permission to reverse a shared setting premise simply because their
  block appears later. Intended exceptions should be authored explicitly.
- Defaults are not permissions to disregard the app's actor/POV, response
  format, handoff or mechanics contracts. Existing immediate director guidance
  and author's-note behavior remain available; this feature does not implement
  a new deterministic conflict resolver across all instruction sources.

This is a natural-language model instruction, not server-side enforcement of
fictional rules. Conflicting prose remains something the author must resolve.

All default-prompt text is shared with **every prompt reader**, including NPC
calls. This is not a narrator-only vault and has no secrecy or ownership gate.
Editors must warn against placing hidden plot facts, another actor's private
knowledge or credentials here. Putting secret lore in a shared default does
not inherit that lore record's secrecy protection.

### Scene focus: soft direction, not a required outcome

Scene focus is an editable, optional paragraph about the scene's purpose,
dramatic question, desired opening for interaction, or gentle direction. For
example, using the repository's placeholders:

> Give Mara and Seraphine opportunities to rebuild trust, without making
> reconciliation inevitable.

The shipped scene-focus template must frame the text as authorial guidance:

- Offer natural opportunities when the current interaction supports them;
  do not force the stated outcome or manufacture an implausible event to get it.
- Preserve established characterization and player agency. Characters may
  refuse, fail, disagree, or take the scene in a different direction.
- Do not rush, fast-forward, invent a player's decision, or repeat the same
  invitation merely to make progress towards the focus.
- If play has already addressed the focus, do not keep manufacturing further
  attempts to achieve it. Follow the resulting situation and immediate player
  input instead. This is a model instruction, not automatic success detection.
- Treat the text as guidance for the writer, never as dialogue, an established
  event, a character's motivation, or knowledge the character automatically has.

Scene focus does not override world/campaign facts, explicit exceptions,
current established events, or the app's actor/POV and response contracts.
Immediate user direction takes precedence over this optional focus. Neither
the section's protected tier nor its position gives it stronger narrative
authority. Failure to reach the goal is a valid scene outcome, not a failed
generation or grounds for an automatic retry.

The field is shared authorial guidance in narrator and NPC calls, with the same
no-secret-content boundary as defaults. Phrase a focus without disclosing hidden
facts; there is no GM-only variant in this version. The field must not cause
extra lore to activate, grant actor knowledge, or widen secrecy filters.

Focus can be set before the first generated opener and edited or cleared during
play. It persists until manually changed or its scene is deleted; achieving a
goal does not rewrite it, close the scene, or set a completion flag. An ended
scene retains its focus as an authored setting. A new, unrelated scene starts
with its own reviewed draft focus, or empty when none was supplied. It never
inherits another scene's focus merely because it follows immediately or uses
the same cast/location. Do not backfill existing scenes or silently copy their
premises, greetings or author's notes into focus. Deliberate branching and
campaign forking preserve authored focus as specified in section 9.

### Creation: generated with the scene draft

Focus is part of drafting a scene, not a separate generation after the scene
has been created. Extend the existing flows in `store/suggest.py`, the scene
routes, draft conversions and confirmation form:

- Every generated scene suggestion, including offscreen suggestions, returns
  a string `focus` alongside title, premise, cast, location and date. Ask for a
  concise dramatic question or opportunity compatible with its proposed scene,
  not a promised result, plot synopsis, hidden revelation, or character order.
  Open-ended play may deliberately return an empty focus.
- Typed-scene intent extraction returns `focus` in the same existing metadata
  call and structured response schema. Phrase a gentle focus from the purpose
  expressed or reasonably implied by the user's description; do not invent
  a required outcome, new events, secret facts, or a purpose where none is
  supported. Return empty when uncertain or explicitly asked for open-ended
  play. Keep the user's original premise intact.
- Carry `focus` through suggestion parsing, response payloads, frontend types
  and `SceneDraft` conversions. Saved scene ideas retain their draft focus in
  `scene_ideas.json` and its existing save/list APIs so saving and later picking
  an idea does not discard it. This string is distinct from the existing
  continuity-driver `focus_refs` controls; do not conflate their semantics.
- `SceneConfirmForm` shows the proposed focus as an editable single-paragraph
  field beside the other scene details. The author can revise or clear it
  before Create. Changing the premise/cast/location does not silently regenerate
  focus or overwrite an edit; the displayed focus is what will be saved.
- `POST /campaigns/{cid}/scenes` accepts the reviewed optional string `focus`
  (default empty) and passes it to scene creation for the initial publication.
  Creation itself makes no model call. Focus is already present before cast/
  location setup and any generated or adapted opener; later setup failures
  follow the existing cleanup/salvage behavior and do not quietly lose focus.

The two creation-draft calls resolve the current world and campaign defaults
through the same store-owned inheritance logic. Include their nonempty, enabled
standing context using the same default-section framing and existing macro
rules available to those builders, without inventing a scene-specific actor
binding. Respect explicit layout disables; label-only/reordering choices remain
properties of the play composer. Do not use a previous scene's focus as a draft
goal or grant new lore access. This is the narrow input extension for creation,
not permission to change every LLM utility. Template wording must distinguish
creating new dramatic opportunities from fulfilling a fixed objective and
warn that focus is shared with narrator and NPC prompt readers.

Missing/non-string focus in an older or imperfect model reply becomes empty
without discarding an otherwise valid scene draft or starting a focus-only
repair call. New structured schemas describe focus as a string, allowing empty.
Existing saved ideas without it also read as empty; no backfill is performed.
If the existing intent call fails or no model is configured, preserve the
current manual creation fallback with an empty, editable focus and its normal
notice. Blank and verbatim-greeting drafts without a metadata-generation call
remain model-free; they offer manual focus but do not invent one from a title,
greeting body or ranking result. Transcript imports are not retrospective
goal-extraction jobs. Cancelling confirmation creates no scene or scene focus.
Saving an idea is a separate explicit action; cancelling confirmation does not
undo an idea the author already chose to save. Creation-generation templates
and response schemas change intentionally even when standing defaults are unset;
the byte-compatibility promise concerns existing play prompts with unset fields.

## 6. Prompt assembly and "always-on"

Add three catalog sections to `store/context/assemble.py`:

| ID | Default inspector label | Tier | Template |
|---|---|---|---|
| `world_default_prompt` | World default prompt | `LOCK_IN` | `scene/sections/world_default_prompt.j2` |
| `campaign_default_prompt` | Campaign default prompt | `LOCK_IN` | `scene/sections/campaign_default_prompt.j2` |
| `scene_focus` | Scene focus | `LOCK_IN` | `scene/sections/scene_focus.j2` |

Default catalog placement is immediately after `global_system_prompt`: world,
then campaign, then scene focus, then the existing prose/model/card sections.
Each template emits its own scope heading and concise framing from section 5,
only when its text is nonblank. No new framing, separators or empty headings
reach the model when all three fields are unset. All prompt wording belongs in
Jinja templates.

Route-specific composition must not be the source of inclusion. Carry the
resolved values through the shared assembly/render/pack path so they apply to:

- Scene narration and narrator calls in group play.
- Every individual NPC call, including subsequent actors in a round.
- Fresh director/offscreen/continue compositions.
- Generated and adapted openers that use the scene composer.
- Fresh retry/regeneration and roll-resume compositions, where the existing
  workflow recomposes rather than replaying a stored prompt.
- Context previews produced by the same composer.

Verbatim greetings do not become a model call. A provider fallback, profile
variant or resend uses the composed prompt rather than fetching these fields
again. A group round can contain separately composed calls; editing a default
or focus between those compositions can affect the later call. This feature
adds no whole-round snapshot or edit exclusion. A focus save changes no
transcript posts; like standing notes it may land during a live generation,
which continues using its already composed text. Scene-specific writes must
still honour the existing closed-branch checks under the campaign lock.

"Always-on" means **no activation condition, no cadence and no automatic budget
shedding while the section is enabled**. It does not mean defeating an explicit
user opt-out: the existing prompt-layout editor can reorder, relabel or disable
any of the three sections, just as it can the global system prompt. No separate
per-record enable switch is added. New IDs appear enabled in older saved layouts
through the existing catalog-merge rule. Disabling `world_overview` must not
disable `world_default_prompt`, or vice versa. A layout disable is made visible
in the corresponding default/focus editor; its help text must not claim the
disabled text is sent. Turning custom layout application off restores ordinary
catalog inclusion.

Defaults and focus use the existing, single-pass prompt macro expansion after
Jinja rendering. User text is template data, never evaluated as a Jinja program
or a new macro language. Macro context and unresolved-token behavior remain the
scene composer's existing contract; do not invent a different `{{char}}` binding
for a shared prompt. Inspector and frozen snapshots show the same expanded bytes
the generation received, not a second macro draw.

### Packing and context limits

Use `LOCK_IN` so the packer never drops or partially truncates an enabled
default or scene focus. Count the rendered heading, framing and expanded text
once through the existing section accounting; do not also inject it as lore or
history.

These tokens can displace droppable context. If protected content alone exceeds
the configured packing budget, retain defaults and focus and report the
over-budget result using existing context accounting. This does not make the
configured budget a hard ceiling or guarantee that the provider's context window
fits. Existing model/provider refusals remain authoritative; never solve an
overflow by silently stripping these sections. For scene focus, `LOCK_IN`
protects its availability, not fulfilment of the goal.

Do not impose a new arbitrary character cap or automatically compress the text
in this version. The editor encourages a compact core and points to actual
rendered token counts in the scene inspector. Larger bodies and scene-specific
details belong in ordinary lore where appropriate.

## 7. Frozen responses and observability

Existing response snapshots are the source of truth for replay. Reroll and
Keep writing paths that replay a frozen prompt use the defaults and scene focus
frozen into that snapshot, even if those fields have since been edited or
cleared. Old snapshots receive no retroactive default/focus block. Paths that
deliberately recompose, such as a roll resume, use current defaults and focus
and freeze the resulting prompt under their existing snapshot contract.

Each rendered default and scene focus has its own inspector row, with stable
section ID, effective label, `LOCK_IN` tier, rendered text and normal token count.
Prompt logs and context comparisons include those sections through the existing
capture/breakdown path. Their ordinary absence when empty is not an error.

Read failures must be visible as a scope-specific warning with zero sent
tokens, without warning prose being inserted into the model prompt. Layout
disables must be discoverable in the editor and layout view, without a disabled
section appearing in the inspector as if it had been sent. Additional status
metadata is not another rendered prompt section and is not counted as text.

No synthetic baseline counter or "tokens saved" badge is added. The inspector
reports what this composition actually sent; it cannot infer what an author
would otherwise have repeated.

## 8. API and editors

Use dedicated routes, with the normal `/api` prefix:

- `GET /worlds/{wid}/default-prompt`
- `PUT /worlds/{wid}/default-prompt`, body `{ "text": "..." }`
- `GET /campaigns/{cid}/default-prompt`
- `PUT /campaigns/{cid}/default-prompt`, body `{ "text": "..." }`
- `GET /campaigns/{cid}/scenes/{sid}/focus`
- `PUT /campaigns/{cid}/scenes/{sid}/focus`, body `{ "text": "..." }`

The world payload is `{text, status}`, with `status` one of `ok`, `empty` or
`unreadable`. The campaign payload includes the same fields for its own text,
plus `world: {id, text, status}` for the resolved world default. `world.id` is
the safely validated bound world ID or null; an unbound/unavailable world uses
status `unavailable` and empty text. No filesystem paths or exception prose
belong in these status fields.

The scene-focus payload is `{text, status}` with the same `ok`, `empty` and
`unreadable` states. `text` is the normalized `focus` frontmatter value; the
route addresses the existing scene by `sid` and accepts no filesystem path or
opaque identity token. Existing scene metadata responses also expose `focus`
when present through their ordinary frontmatter shape.

PUT returns the same payload shape as GET after a successful save. The campaign
PUT writes only its own field, never the world field. No model call is made by
any of these routes. The focus PUT changes only the addressed scene's field,
never an author's note or campaign default; a closed-branch save returns the
existing 409 refusal without writing. Unknown scopes/scenes return 404,
missing/non-string `text` is an invalid request, and publication failures return
an error without claiming a save. Request models remain plain,
Android-compatible pydantic fields.

Extend the existing scene-creation body with optional `focus: str = ""`, and
the existing scene-idea save/read shapes with optional focus defaulting empty.
Suggestion and intent responses carry the string focus described in section 5;
their generation calls remain on their existing routes, tasks, run/capture and
usage-accounting paths. Old clients omitting focus still create an empty-focus
scene. An explicitly non-string creation/edit value is an invalid request,
not prose coerced from JSON. An empty focus must remain empty even when the
draft once contained a generated proposal; no server-side re-inference.

World editing belongs in the existing World overview, beside About, as a
**World default prompt** section. Campaign editing belongs in the Campaign hub
Settings panels as **Default prompts**, with:

- A read-only **World default prompt** preview labelled as inherited live,
  plus a link to the world's overview when it is available. Editing the world
  is deliberately a world-level action, not a hidden campaign-panel write.
- The campaign's own **Campaign default prompt**, read-only until Edit.

Scene focus editing belongs in the existing scene inspector beside Author's
notes, as **Scene focus**, available both before an opener and during play.
It follows the same explicit view/edit/save/cancel pattern, with an empty hint
such as "What should this scene explore? Leave blank for open-ended play."
It is not another turn-composer mode, a hidden director note, or a new goal
checklist. The same single-paragraph focus field appears in scene confirmation,
pre-filled from generated or saved draft metadata when available. Show saved
focus as text; the context row shows its separately framed and expanded prompt
representation. Focus and the opener premise remain separately labelled and
editable, including when the reader chooses to post no opener.

World/campaign defaults use Markdown display and a multi-line textarea. Focus
uses a soft-wrapping paragraph editor with explicit help that pasted line breaks
become spaces; it is not a multiline Markdown document. All use explicit Edit,
Save and Cancel. Empty states say that the default/focus is unset, not that
loading failed. Show load/save errors, retain a failed save's draft, and guard
against stale reads or save completions landing on another world/campaign/scene
after navigation.
Invalidate the relevant client reads after a save; never use a remembered
inherited preview as the server's next prompt input.

Help text covers four things: shared with all actors, compact standing context,
world edits affect future play in every bound campaign, and existing repeated
copies are not automatically removed. Scene-focus help also states that this
is gentle direction rather than a required outcome, is not character knowledge,
and should be revised or cleared manually when it no longer fits. All three
editors show a warning/link when the effective prompt layout has disabled the
corresponding section. If layout status cannot be read, say inclusion status is
unknown rather than promising inclusion or displaying an editable off switch
of its own.

## 9. Compatibility and content lifecycle

- Existing stores need no migration. Missing/empty defaults and focus preserve
  existing assembled play-prompt bytes, including with a saved custom layout.
- World forks deep-copy the world's sidecar; later edits of the source do not
  change that fork. Campaign forks copy the campaign sidecar as an independent
  setting and remain bound to the same live world under current fork semantics.
  A historical campaign fork does not rewind a manually edited campaign default
  to the fork point: it copies the current campaign setting.
- Scene renames, date-driven renames and campaign numbering repads retain focus
  in the scene file's metadata. Add `focus` to the branch store's explicitly
  copied metadata keys so a sibling receives the current focus in its initial
  scene write; subsequent edits in either scene are independent. An absent
  focus remains absent. A cut or retcon within one scene preserves the current
  focus rather than pretending to reconstruct an earlier version of it.
- Campaign forks copy focus with each retained scene file, independently of
  the source campaign. A historical fork copies current authored focus for
  those scenes; it does not rewind focus to the selected transcript point.
  Scenes removed by fork cuts lose their focus through ordinary deletion.
- Scene deletion removes focus with the scene file; no separate focus cleanup
  or orphan record exists. Branch failure cleanup uses the ordinary deletion
  path. Ending or absorbing a scene does not delete its focus, copy it into
  another scene, or promote it to campaign scope. A new scene reusing a deleted
  scene's `sid` gets only its own supplied focus, never that deleted field.
- World bundle export/import carries the sidecar as world content, including
  the existing text-URL rewriting if the imported world gets a different ID.
  No bundle-format bump is required merely to add optional plain Markdown.
  Importing into an older build preserves the file but cannot make that build
  use a feature it does not understand.
- Store backups/restores include the default sidecars and scene frontmatter.
  Existing story exports remain story exports; these instructions are not
  appended to EPUB/HTML/transcript output. Standalone character-card exports
  remain cards and do not silently embed world/campaign instructions.
- Missing focus in old scenes, saved ideas, drafts or client requests is empty.
  No migration, identity backfill or generation is triggered merely by reading
  or editing focus. Older builds can ignore the scalar and retain it through
  ordinary metadata round trips, but their branch copy lists do not promise
  to preserve a field they do not understand. Copying raw scene Markdown keeps
  its metadata; story-only exports do not gain a focus appendix.
- Defaults are not promoted or merged by character/lore sync operations.
  World or campaign deletion removes its own sidecar with its existing scope
  lifecycle; it does not clear another scope's default.

Content consolidation is an author-controlled follow-up: place a compact shared
rule here and remove only the redundant setting explanation from the appropriate
cards/lore. Preserve distinctive characterization, local details, deliberate
exceptions and standalone-card portability where needed. No consolidation is
performed by this feature or as part of implementing it.

## 10. Acceptance and verification

Use isolated stores and existing placeholder names/fakes. Required coverage:

1. Absent, empty and whitespace-only defaults/focus send no new text or
   separators; baseline prompts remain byte-identical in narrator, NPC and
   opener paths, including an older saved layout.
2. World-only, campaign-only and both-scopes cases render exactly once each in
   default order. Clearing either leaves the other intact. A duplicate sentence
   in both scopes is not silently deduplicated.
   Scene-focus-only and all-three cases render each applicable section once;
   clearing focus leaves defaults intact, and clearing defaults leaves focus.
3. Fresh narrator, individual NPC, group-round, director, offscreen, continue
   and generated/adapted-opener paths include the applicable defaults and focus
   even with no lore matches or a changed location/cast. Disabled layout sections
   do not render; turning the custom layout off restores inclusion.
   Focus is only the addressed scene's, never the most recent scene's or another
   member of its branch group. Changing the cast does not change its ownership.
4. World changes reach subsequent compositions in multiple bound campaigns
   without local copies. Campaign changes affect only that campaign. Unbound,
   missing and invalid world references never read an unrelated path; the
   campaign default continues to render.
5. A tiny positive packing budget drops ordinary context before defaults/focus;
   all three survive an unavoidable over-budget result. Counts include framing
   and expansion exactly once, and preview/capture reflect the sent text.
6. Frozen reroll/Keep writing snapshots retain their original defaults/focus; old
   snapshots gain none. Freshly recomposed retry/roll-resume paths read current
   defaults/focus. Macro expansion is not repeated for inspection or variants.
7. Read failures degrade only the failed scope, appear as warnings, and never
   become model-prompt prose or an empty saveable draft. Interrupted/failed
   writes preserve the previous text. Nonexistent scopes and unsafe IDs create
   nothing. Campaign mutations satisfy the lock/path/atomic/import guards.
8. APIs validate required string text, clear correctly, return saved/resolved
   content, and never write the inherited world via the campaign route. Focus
   creation/edit requests normalize every supported line boundary to a space;
   quotes, colons and fence-like text round-trip as scalar content and cannot
   inject metadata keys or terminate a frontmatter block. Clearing removes only
   focus, preserving transcript and other metadata. Failed publication leaves
   the whole scene unchanged, and a focus-only save does not move `updated`.
9. World, campaign and scene-focus editors cover view -> edit -> save/cancel,
   failed reads and saves, disabled-layout warnings, inherited live previews,
   and navigation during pending reads/saves. Scene confirmation displays,
   permits editing/clearing, and submits the reviewed paragraph; changing other
   details or choosing no opener does not restore a cleared/generated focus.
10. World fork/bundle round trips and campaign forks preserve the right sidecar
    and independence. Old-store/frozen-fixture compatibility is preserved
    without regenerating the frozen fixture's `home/`.
11. Focus follows rename/repad, copies independently on a scene branch or campaign
    fork, survives cut/retcon/scene end, and is dropped on scene deletion or branch
    failure. Reused scene IDs never inherit deleted focus; unrelated new scenes
    receive only their own reviewed draft focus or stay empty. Legacy reads and
    focus-only saves mint no identity, and an unreadable or malformed scene
    cannot be replaced with a focus-only record.
12. Scene-focus prompt/eval assertions require the whole soft-direction framing:
    opportunities rather than guaranteed outcomes, player/character agency,
    no invented player decisions or knowledge, no rushing/repeated pursuit, and
    no continued pressure once addressed. Existing actor/POV contracts remain
    present and lore secrecy/activation is unchanged. No focus save/read starts
    a goal-check call, sets completion state, or closes a scene.
13. Standard/offscreen suggestion and typed-intent generation return focus in
    the same call as their existing metadata. Templates and intent schemas ask
    for a gentle, shared-safe paragraph or empty when unsupported/open-ended;
    parsing tolerates missing/non-string focus without losing a valid draft.
    Creation builders resolve applicable standing defaults, respect their
    layout disables, and never inherit previous-scene focus. Existing task,
    usage/capture, cast-reference and lore-access contracts remain intact.
14. Focus survives suggestion -> save idea -> list -> pick -> confirmation ->
    create, as well as direct suggestion and typed-intent paths. Creation
    publishes the reviewed value in the initial scene write before any opener;
    a failed initial publication creates no scene without the requested focus.
    Cancelling writes nothing. Failed/unavailable intent generation preserves
    manual creation, old ideas/clients read empty, and blank/greeting paths add
    no focus-only model call. Opener generation never overwrites saved focus.

Extend `scripts/verify_templates.py`, the offline prompt-section eval coverage,
and `templates/README.md` alongside implementation. A synthetic consolidation
case can demonstrate that replacing several repeated blocks with one compact
default reduces the composed input; it must also test that simply adding a
default increases it. That is an accounting test, not evidence of model quality.

Implementation verification uses the repository gate and isolated end-to-end
procedure documented by `CONTRIBUTING.md` and the verify skill. Real model calls
or private-library cleanup are not authorised by this spec-writing task.

## 11. Decisions proposed for approval

The deliberately small first version is two multiline standing defaults in
Markdown sidecars plus one single-paragraph scene `focus` frontmatter field,
live world inheritance plus an additive campaign prompt, and scene-local soft
direction. Focus is generated with scene draft metadata, reviewed in the
confirmation form, and saved in the initial scene publication; blank/manual
paths remain available without extra calls. All three use `LOCK_IN` play
sections and the existing layout's explicit opt-out. The brainstorming
distinction is binding: standing defaults establish context; scene focus
suggests opportunities, never a compulsory outcome. It offers no automatic
content migration, goal checker, or secret-content variant. Accepting this
design approves those contracts, not a claim that defaults by themselves shrink
prompts or that persistent focus guarantees model adherence or success.

The owner has approved this design and authorised frequent checkpoint commits.
The spec-to-planning review approved this document with the inspection limitation
recorded above. The owner also approved the [implementation plan](../plans/2026-10-11-world-campaign-default-prompts.md)
and Native execution on 2026-10-11. The first plan review requested an explicit
captured-warning handoff through the suggestion hook; the amended plan covers
that handoff, scope resets, stale responses and confirmation tests. Re-review
approved implementation with no material findings. That review used the supplied
amended diff and public-main contracts after the same Windows sandbox limitation
prevented exact-checkout inspection. Before feature implementation began, the
owner chose to pause it for repairs to a reproducibly failing backend baseline.
