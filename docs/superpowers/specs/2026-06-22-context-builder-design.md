# Context Builder — Design

> Builds the per-turn prompt assembler that finally injects campaign state into the LLM:
> SillyTavern-faithful assembly of player personas (`{{user}}`), in-scene NPC cards (`{{char}}`),
> ST card fields, and **keyword-activated world-info** (lore + locations) — behind a swappable
> retrieval seam. This is the long-deferred "prompt injection" milestone.

**Status:** Design — not yet implemented
**Date:** 2026-06-22
**Branch:** `context-builder` (off `pcs-tags-actors`)
**Builds on:** [`2026-06-21-pcs-tags-actors-design.md`](2026-06-21-pcs-tags-actors-design.md)
(`scene_cast`, `players_in_scene`, roles) and
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (V3 cards).
**Supersedes:** the "Context builder" half of
[`2026-06-21-greetings-context-builder-decisions.md`](2026-06-21-greetings-context-builder-decisions.md)
(greetings/plot-maps remain in that doc as a later spec, 2b; lorebook import is spec 2c).

## Purpose

Every prior spec deferred prompt injection: scene chat sends raw turns with no campaign context.
The PCs spec records a `player` role but consumes nothing. This spec builds the **context
builder** — a single, isolated assembler that turns a scene's cast + the campaign's world-info
into the message list sent to OpenRouter, faithful to how SillyTavern V3 cards expect to be used.

## Non-goals (this iteration)

- **No lorebook import.** Standalone lorebook files and a card's embedded `character_book` import
  as keyed Lore entries with per-entry category routing — that is **spec 2c**. This spec only
  *consumes* `keys` that already exist on entries (author them by hand to exercise the builder).
- **No greetings / plot maps** (spec 2b).
- **No token budget / truncation / summarization.** The whole assembled context is sent as-is.
  (A real budget is future work; the activation seam is where it will eventually live.)
- **No smarter-than-keyword retrieval.** v1 is keyword match; the `activate()` seam exists so
  embeddings/semantic retrieval can replace it later without touching the assembler.
- **No configurable prompt template.** The ST-faithful structure is built in (a user-editable
  template is future frontend/config work).
- **No group "active speaker".** Multi-NPC scenes inject all NPC cards; there is no turn-taking
  or per-message speaker selection.

## Expected tuning points (validate by playtest)

The architecture isolates the choices most likely to change once the prompts are actually run, so
each is a cheap, localized edit:

- The **block ordering** within the system message.
- **Keyless ⇒ always-on** lore (could become an explicit `always_on` flag instead).
- **Multi-NPC** `system_prompt` / `post_history_instructions` concatenation order.
- The **`context_scan_depth`** default (8).
- The **keyword match** rule (whole-word, case-insensitive).

All of these live inside `store/context.py`; none are spread across the codebase.

## Schema additions

- **`keys` on entity entries** (lore + locations). Optional frontmatter, comma-joined whole-word
  triggers (string-scalar friendly, like the PC `tags` field). Added to the generic entity
  read/create/update path; **only lore and locations are activatable** world-info. A keyless
  activatable entry is **always-on**.
- **`context_scan_depth` in `config.md`** — integer, default `8`: how many of the most recent
  scene messages the keyword scan considers. Unparseable/missing ⇒ default 8.

Entities otherwise keep their shape (`name` + body + now optional `keys`); characters/PCs/scenes
are unchanged.

## Assembly — `build_messages(cid, sid) -> list[dict]`

Reads the **campaign** store (everything is campaign-scoped at play time) and returns the full
OpenRouter `messages` list. Order mirrors SillyTavern:

1. **System message** (omitted entirely if it would be empty), concatenating, in order:
   - **`system_prompt`** of each in-scene NPC card (cast order);
   - **NPC character block(s)** — `description`, `personality`, `scenario` per in-scene NPC card;
   - **player persona block** — for each player-role actor: a PC persona
     (name/pronouns/summary/description) or, for a character-cast-as-player, its name +
     card `description`/`personality`;
   - **`mes_example`** of each NPC card (example dialogue);
   - **activated world-info** — the selected lore/location entry bodies (see "Activation").
2. **Chat history** — the scene's user/assistant turns, in order.
3. **Final system message** — concatenated **`post_history_instructions`** of the NPC cards, if
   any (ST's post-history placement, right before the model responds).

**Actors come from `scene_cast(cid, sid)`** (Spec 1): `role == player` actors (kind `pcs` or
`characters`) feed the persona block; `role == npc` actors (always kind `characters`) feed the
NPC card blocks. Each actor's card/persona is read from the campaign root at its
`locked_version`. A character cast as **player** never appears as an NPC. "Cast order" means
`scene_cast`'s sort — `(kind, id)`, deterministic but **alphabetical by slug**, not narrative
insertion order (`appearances.json` records no first-seen index). If narrative order ever
matters, that index is a later addition — a tuning point.

**Token substitution.** Compute once: `{{user}}` → comma-joined player names, `{{char}}` →
comma-joined NPC names. Replace both tokens (case-insensitive, literal `{{user}}`/`{{char}}`)
across **every** assembled message content — system blocks, world-info, and history turns alike.

**Empty context.** If there are no NPC cards, no players, and no activated/always-on world-info,
no system message is produced and history is sent exactly as today — preserving current chat
behavior for a bare scene.

## Activation (pluggable retrieval seam)

```python
activate(entries, recent_text, depth) -> list[entry]
```

- `entries` = the campaign's **lore + location** entries (each `{name, body, keys}`).
- `recent_text` = the concatenated content of the last `depth` scene messages.
- **v1 keyword strategy:** an entry is selected if it has **no keys** (always-on) **or** any of
  its keys matches as a **whole word, case-insensitive** within `recent_text`.
- Returns the selected entries; the builder injects their bodies (after substitution) as the
  world-info block. Replacing this one function with a semantic/embeddings strategy is the entire
  future-proofing surface.

## Backend modules

```
backend/src/grimoire/store/
  context.py     # NEW — build_messages(); ST-faithful assembly; token substitution; activate()
  entities.py    # EXTENDED — optional `keys` on read/create/update
  config.py      # EXTENDED — context_scan_depth (default 8)
```

`context.py` is pure beyond reading the store. Sketch:

```python
build_messages(cid, sid) -> list[dict]          # the full OpenRouter messages list
activate(entries, recent_text, depth) -> list   # v1 keyword strategy; the swap seam
_persona_block(actor) -> str ; _npc_block(card) -> str ; _substitute(text, names) -> str
```

Helpers reused from Spec 1: `appearances.scene_cast`, `appearances.locked_version`;
`characters.read_card`, `pcs.read_persona`; `entities.list_entities`/`read_entity`.

## Route wiring

`routes.py` chat/retry change only how `messages` is built; the SSE streaming, persistence, and
error contract are unchanged:

- `POST /campaigns/{cid}/scenes/{sid}/chat` — append the user turn to the scene (as today), then
  `messages = store.context.build_messages(cid, sid)` and stream that.
- `POST /campaigns/{cid}/scenes/{sid}/retry` — `messages = store.context.build_messages(cid, sid)`
  (no new turn), then stream. The existing "nothing to retry" `400` still applies when the scene
  has no messages.

Entity create/update routes accept an optional `keys` field (forwarded to `entities`).

## Error handling / edges

- An appeared actor whose card/persona is missing in the campaign contributes nothing (skipped,
  no crash).
- A character cast as **player** injects through the persona block, never the NPC block.
- No players/NPCs but always-on lore exists ⇒ the system message still carries that lore.
- A short transcript simply yields a shorter `recent_text`; `depth` larger than the message count
  is harmless.
- The chat `409` missing-key contract and SSE error events are unchanged.

## Testing (backend, pytest, temp `GRIMOIRE_HOME`, fake OpenRouter)

`build_messages` cases:
- single NPC → one system message with `description`/`personality`/`scenario` in order;
- multi-NPC → both cards present, `{{char}}` substitutes to the comma-joined names;
- a player PC and a **character-cast-as-player** → persona block present, `{{user}}` substitutes;
- `post_history_instructions` lands in a **final** system message, after history;
- `mes_example` included in the top block;
- **activation**: a key in the last `depth` messages ⇒ entry injected; the same key only outside
  `depth` ⇒ omitted; a **keyless** entry ⇒ always injected;
- **substitution** rewrites `{{user}}`/`{{char}}` in card text, world-info, and history;
- **empty context** ⇒ no system message (messages equal today's raw turns);
- `context_scan_depth` read from config; missing ⇒ 8.

Route: chat with a fake OpenRouter asserts the assembled system message is the first sent message;
the SSE/persistence/error contract matches the current tests.

## Phasing (for the plan)

1. **`keys` schema** on entities + config `context_scan_depth`.
2. **`activate()`** keyword strategy (unit-tested in isolation).
3. **`build_messages`** assembly + token substitution.
4. **chat/retry wiring** + entity-route `keys` passthrough.

## What's next

- **Spec 2b — Greetings & plot maps** (captured decisions doc), which reuses this builder for the
  world-informed opener generation.
- **Spec 2c — Lorebook / world-info import** (standalone lorebooks + `character_book` → keyed Lore
  entries, per-entry category routing) that populates the `keys` this builder consumes.
- Future builder layers: token budget, summarization, and a semantic `activate()` strategy.
