# Script scenes: PC-named messages + per-NPC posts — design

**Date:** 2026-07-02
**Solves (in part):** [#744 — Per-character posts (split one call into per-speaker posts / speaker loop)](https://github.com/charlesmsiegel/grimoire/issues/744)

## Goal

Scene logs become a **script**: every message is stored under the name of who
said it — the PC's name instead of "You", and each NPC (or the Grimoire
narrator voice) instead of a single blob under "Grimoire". Conversation
*role* (user/assistant) is no longer stored; it is **derived** from the cast
whenever the scene is sent to the model or displayed. One LLM call per turn;
the reply is split into per-speaker posts on receipt.

## 1. Storage: pure script (`store/scenes.py`)

Every message is `**<Speaker>:** content`. No role is stored.

- `**Elara Vane:** I draw my blade.` — a PC line
- `**Seraphine Vale:** "You dare?"` — an NPC line
- `**Grimoire:** The hall falls silent.` — narrator / GM voice
- `**You:** …` — user line with no PC recorded (legacy, or zero/multiple PCs)

**Marker grammar.** A marker is `**<label>:**` at the start of the body or
immediately after a blank line (the serializer always writes a blank line
between messages; this confines false-splits from message *content* that
happens to start with a bold-label line). Label: 1–64 chars, no `*`, no
newline.

**Label interpretation on parse:**

| label | role | speaker |
|---|---|---|
| `You` | user | — |
| `Grimoire` | assistant | — |
| `You (X)` / `Grimoire (X)` (legacy parens form) | user / assistant | `X` |
| anything else | derived (see §2) | the whole label |

The parens sub-speaker form is recognized **only** for the two reserved
labels (back-compat on read); it is never written again. Names containing
parens are therefore unambiguous as plain labels.

**Serialization:** write the message's `speaker` as the label if present,
else `You` (user) / `Grimoire` (assistant). A speaker is only ever stamped
if it fits the label grammar (≤64 chars, no `*`/newline); otherwise the
message is written unstamped.

## 2. Role is derived, not stored

A message is **user-side** iff its speaker is `You` or matches the name of a
`role=player` actor in the scene's cast (PCs, or characters cast as
players). Everything else is assistant-side.

- New helper `appearances.player_names(cid, sid) -> list[str]`: display
  names of the scene's `role=player` cast, resolved from the campaign
  copies (`pcs.read_persona` / `characters.read_card` at the locked
  version); unresolvable actors are skipped.
- `scenes.read_scene` keeps returning `role` on every message (many callers
  branch on it — regenerate, absorb, context) but computes it by the rule
  above. `appearances` is imported lazily inside `read_scene` to avoid an
  import cycle (mirrors `appearances.suggestions` lazily importing
  `scenes`).
- Existing scenes need **no migration**: `You` → user, `Grimoire` →
  assistant regardless of cast.

Known edge (accepted): an NPC whose name exactly equals a player's name
would have their lines derived user-side.

## 3. Writes

- **User turn** (`routes.post_chat`): speaker = the sole player's name when
  the cast has exactly one `role=player` actor; unstamped (`You`) when zero
  or multiple. Before appending, **backfill**: `scenes.stamp_user_speaker(
  cid, sid, name)` rewrites every speakerless user message in the scene to
  that name (only called when the scene has exactly one player).
- **Assistant turns** written by the app itself (openers, `*The scene moves
  …*` / `*Time passes…*` transitions, partial-stream salvage) stay
  `Grimoire`.

## 4. Per-NPC posts from a single call (#744)

**4a. Prompt — "Response format" system section.** `context._assemble`
appends a final labeled system section instructing script output, naming
the cast:

> Write your reply as a script. Each character who acts or speaks gets
> their own block starting with `**<Name>:**` on its own line, e.g.
> `**Seraphine Vale:**`. Use `**Grimoire:**` for narration, scene
> description, and any voice that isn't a named character. Never write
> dialogue or actions for: `<player names>`.

Included whenever the scene is generated for (also the opener path);
`context_sections` exposes it in the token breakdown automatically.

**4b. Response splitting.** New `scenes.split_reply(text, player_names) ->
list[dict]`: splits the full reply on the marker grammar of §1 into
`{speaker, content}` segments.

- Text before the first marker, or a reply with no markers at all →
  `Grimoire` (models won't always comply).
- A segment whose label matches a player's name is **reassigned to
  `Grimoire`** (never store a forged PC line); its content is kept.
- Unknown names are stored verbatim — new minor NPCs are legitimate.
- Empty segments (marker with no content) are dropped.

`routes._chat_stream` replaces its single `append_message(...,
"".join(parts))` with one `append_message(cid, sid, "assistant", seg["content"],
speaker=seg["speaker"])` per segment — in both the success and the
partial-stream error paths. Still exactly **one LLM call per turn**; one
turn may yield several log entries.

**4c. History projection carries speakers**, so attribution survives the
round trip. In `context._assemble`:

- Each history message's content is prefixed with `**<speaker>:** ` —
  assistant-side always (using `Grimoire` when speakerless), user-side only
  when a speaker is stamped (plain `You` lines stay bare).
- Consecutive same-role messages are merged into one API message
  (`\n\n`-joined) so providers that expect role alternation are happy.
- The `recent_text` world-info scan window and `{{user}}`/`{{char}}`
  substitution behave as before (over content).

## 5. Reroll regenerates the whole turn

One turn now produces a *run* of assistant messages.

- New `scenes.remove_trailing_assistant_run(cid, sid)`: drops trailing
  messages while they are assistant-side (at least one). Replaces
  `remove_last_message` in `post_regenerate`; refuse (400, "cannot
  regenerate the opening post") when the trailing run starts at index 0.
- `remove_last_message` is deleted if nothing else uses it.
- Frontend reroll: the button stays on the last message when it is
  assistant-side and not the opener; the optimistic slice removes the whole
  trailing assistant run before streaming.

## 6. Frontend (`CampaignView.tsx`, `api/client.ts`)

- **Cast names:** `appearances.scene_cast` entries gain `"name"` (resolved
  from the campaign copy, falling back to the id), so
  `GET …/scenes/{sid}/cast` carries names. Client `Cast` type updated.
- **Spine label:** `m.speaker ?? (m.role === "user" ? playerName ??
  labels.user : labels.assistant)` where `playerName` is the sole
  `role=player` cast entry's name (fetched with the scene, refreshed with
  `ctxKey`), else `null`. This is the render-time fallback for unstamped
  `You` lines.
- **Streaming:** during the stream the raw reply renders in one bubble
  (`**Name:**` headers render as bold names — already script-like). On
  completion (and on error-with-partial) the scene is **re-fetched**
  instead of appending the accumulated text locally, so the split,
  attributed messages replace the bubble.
- `msg user` / `msg assistant` CSS classes keep keying off the derived
  role the API returns.

## Testing

**pytest**

- `scenes`: arbitrary-label parse + blank-line rule; legacy `You` /
  `Grimoire` / `Grimoire (X)` parse; serialization round-trip;
  `stamp_user_speaker` rewrites only speakerless user lines;
  `split_reply` (leading unlabeled → Grimoire, no markers → Grimoire,
  PC-name reassignment, empty segments dropped);
  `remove_trailing_assistant_run` (run of 3, refusal on opener).
- `appearances`: `player_names` resolves both PC and character players;
  `scene_cast` includes `name`.
- `routes`: chat with one PC stamps the speaker and backfills; zero/two
  players leaves `You`; a scripted fake-client reply is stored as multiple
  messages under their names; regenerate drops the whole trailing run;
  context includes the Response format section.
- `context`: history projection prefixes speakers and merges consecutive
  same-role messages; roles derived from cast (PC-named line → user turn).

**vitest** (`CampaignView.test.tsx`)

- Spine renders the stored speaker; unstamped user line renders the sole
  player's name; no player → configured user label.
- After a stream completes, the scene is re-fetched (`api.getScene` called
  again) rather than locally appended.
- Reroll on the last assistant message still works.

## Out of scope

- Multi-PC speaker picker (storage and role derivation are already
  multi-PC-ready; new user messages in a multi-player scene stay `You`).
- Completion-mode prompting (send the raw script as one prompt) — enabled
  by this storage, not built.
- Editing a message's speaker in the UI; migrating legacy parens markers on
  disk.
