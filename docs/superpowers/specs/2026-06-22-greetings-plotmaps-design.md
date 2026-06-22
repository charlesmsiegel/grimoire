# Greetings & Plot Maps — Design

> Greetings become **first-class world objects** sourced from character cards by explicit import,
> wired into a **plot map** of directed `leads_to` edges and mutual exclusions, gated by world
> tags against a campaign's player actors. A scene can be **started from a greeting** (seeds the
> first post + appears the character) or from a **world-informed prompt** (an ephemeral,
> character-less generated opener, offered to save).

**Status:** Design — not yet implemented
**Date:** 2026-06-22
**Branch:** `greetings-plotmaps` (off `context-builder`)
**Builds on:**
[`2026-06-22-context-builder-design.md`](2026-06-22-context-builder-design.md) (`build_messages`,
the `activate()` seam, entity `keys`, token substitution),
[`2026-06-21-pcs-tags-actors-design.md`](2026-06-21-pcs-tags-actors-design.md) (tags, PCs, the
recorded `player` role, `appear()`), and
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (V3 cards,
`first_mes`/`alternate_greetings`).
**Supersedes:** the greetings/plot-maps half (sections B–F) of
[`2026-06-21-greetings-context-builder-decisions.md`](2026-06-21-greetings-context-builder-decisions.md);
its "Open questions" are resolved below.

## Purpose

The context builder injects a scene's cast + world-info into the prompt, but a scene still starts
empty. This spec delivers **how scenes begin**: a library of authored greetings, a plot map that
branches and gates them, tag gating against the campaign's players, and the two start paths
(from a greeting, or from a generated opener). It is the first consumer of the context builder's
world-informed assembly for generation.

## Non-goals (this iteration)

- **No greeting sync.** Greetings are **world-only** objects referenced by id; the only campaign
  state is the played set. `sync.py` is untouched — greetings do not copy-on-create and do not
  appear in the incoming/accept/reject flow.
- **No plot-map graph editor / greeting UI** (deferred with the rest of the frontend).
- **No tags on characters-cast-as-player.** Tag gating consumes PC tags only (faithful to the
  PCs spec). Recorded as a deliberate limitation below.
- **No token budget / truncation** for the opener generation (inherits the context-builder stance).
- **No auto-derivation of edges** on import — imported greetings carry no plot edges; the user
  wires them afterward.

## Storage — `~/.grimoire/`

```
worlds/<wid>/
  greetings/<gid>.md            # frontmatter: name, character, version, requires_tags,
                                #   predecessor_join ; body: the greeting text
  plotmap.json                  # the directed edges + exclusions, keyed by greeting id
campaigns/<cid>/
  played.json                   # ["<gid>", …] — greetings started-from in this campaign
```

- **`greetings/<gid>.md`** — string-scalar frontmatter only (reuses the existing writer):
  - `name` — display name.
  - `character` — the world character id this greeting belongs to.
  - `version` — that character's version id (locked into the scene on start).
  - `requires_tags` — comma-joined world tag ids (like the PC `tags` field); empty ⇒ no gate.
  - `predecessor_join` — `all` (default) or `any`: whether **all** or **any** plot-map
    predecessors must be played to unlock this greeting.
  - body — the greeting text (may contain `{{user}}`/`{{char}}`).
- `<gid>` is `slugify` + `uniquify`, no date prefix (as elsewhere).
- **`plotmap.json`** — the greeting graph is nested data, so it lives in a JSON sidecar (per the
  string-scalar-frontmatter convention), keyed by greeting id:

  ```json
  { "rescued-at-sea": { "leads_to": ["the-reckoning"], "excludes": ["captured"] } }
  ```

  Edge data is stored **on the source node** only. A missing key, or a greeting absent from the
  map, reads as `{"leads_to": [], "excludes": []}`. Deleting a greeting removes its own entry and
  prunes its id from every other node's `leads_to`/`excludes`.
- **`played.json`** — a campaign-level JSON list of greeting ids; the set grows when a scene is
  started from a greeting. Missing file ⇒ empty.

## Modules

```
backend/src/grimoire/store/
  greetings.py   # NEW — world greeting CRUD, plotmap IO, import-from-card, pure availability()
  playing.py     # NEW — campaign played-set IO, available_greetings(cid), start_from_greeting()
  context.py     # EXTENDED — build_opener_messages(cid, sid, prompt)
```

### `greetings.py` (pure over a world root)

```python
list_greetings(root) -> [{id, name, character, version, requires_tags:[...], predecessor_join}]
read_greeting(root, gid) -> {meta:{id,name,character,version,requires_tags,predecessor_join}, body}
create_greeting(root, name, character, version, body="",
                requires_tags=[], predecessor_join="all") -> gid
update_greeting(root, gid, *, name=None, body=None, requires_tags=None,
                predecessor_join=None) -> None
delete_greeting(root, gid) -> None          # also prunes plotmap entry + inbound refs
import_from_character(root, char_id, vid) -> [gid, …]   # first_mes + each alternate_greetings

read_plotmap(root) -> {gid: {"leads_to":[...], "excludes":[...]}}
set_edges(root, gid, leads_to=None, excludes=None) -> None   # writes plotmap.json

availability(world_root, plotmap, played: set, player_tags: set) -> [
  {"id", "name", "available": bool, "reasons": [str, …]}     # pure; reasons explain a lock
]
```

- `import_from_character` reads the character's locked-version card
  (`characters.read_card(root, char_id, vid)`); creates one greeting from `first_mes` (if
  non-empty) and one per entry in `alternate_greetings`. Names: `"<Character name>"` for the
  first, `"<Character name> (alt N)"` for alternates; uniquified. No edges, no tags.
- **`availability`** is pure (no store/campaign coupling) so it is unit-testable in isolation.
  A greeting G is **available** iff **all** of:
  - **gate:** with `preds = {h : G in plotmap[h].leads_to}` and `join = G.predecessor_join`,
    either `preds` is empty, or (`join == all` ⇒ `preds ⊆ played`) / (`join == any` ⇒
    `preds ∩ played ≠ ∅`).
  - **not excluded:** no played greeting P with `G in plotmap[P].excludes`, and G does not list a
    played greeting in its own `excludes` (exclusion treated **symmetrically**).
  - **tags:** `set(G.requires_tags) ⊆ player_tags`.
  - `reasons` lists which checks failed (for UI/debugging); empty when available.

### `playing.py` (campaign-coupled)

```python
read_played(cid) -> set[str]               # from played.json; missing ⇒ empty
_mark_played(cid, gid) -> None             # idempotent add + write

player_tags(cid) -> set[str]               # union of tags of appeared player-role PCs
available_greetings(cid) -> [ {id,name,available,reasons} ]   # greetings.availability bound to campaign

start_from_greeting(cid, sid, gid) -> None
```

- `player_tags` reads `appearances.roster(cid)`, keeps `role == player` **and** `kind == pcs`,
  and unions each PC's campaign-side tags (`pcs.read_pc(croot, pid)["meta"]["tags"]`). Characters
  cast as players contribute nothing (no tag model) — the recorded limitation.
- **`start_from_greeting(cid, sid, gid)`** (order matters):
  1. Scene must exist and be **empty** — else `AppearError`-style guard surfaced as `409`
     (a greeting seeds the *first* post).
  2. Greeting must be **available** in this campaign — else `409`.
  3. `appearances.appear(cid, sid, "characters", G.character, G.version, "npc")` — locks the
     character's version into the scene (same `409` rules as elsewhere if already locked to a
     different version).
  4. `_mark_played(cid, gid)`.
  5. Substitute `{{user}}`/`{{char}}` in the greeting body using the now-current cast
     (`context`'s substitution: `{{char}}` → comma-joined NPC names, `{{user}}` → comma-joined
     player names; empty token stays literal), then append it as the **first assistant** message
     (`scenes.append_message(cid, sid, "assistant", text)`).

### `context.py` — opener assembly

```python
build_opener_messages(cid, sid, prompt) -> list[dict]
```

- Reuses the world-info path (`_world_info`) with `recent_text = prompt` (the prompt drives
  keyword activation; always-on lore is always included) and the **player persona blocks** for
  any players already cast in the scene — but **no NPC card blocks** (the opener is
  character-less).
- Assembles a single **system** message: an authoring instruction
  (e.g. *"Write the opening narration for a new scene. Set the scene vividly in second person.
  Do not speak for the player."*) + the player persona block(s) + the activated world-info, with
  `{{user}}`/`{{char}}` substituted (player names known; `{{char}}` → empty ⇒ literal).
- Followed by a single **user** message carrying the (substituted) `prompt`.
- The whole thing is sent to OpenRouter and streamed back; **nothing is persisted to the scene**
  (ephemeral). Saving the result is a normal `POST /worlds/{wid}/greetings` from the frontend.

## Route wiring (`routes.py`)

Literal routes are declared **before** the generic `/{kind}` catch-alls (as with characters/PCs).

```
# World greetings (before generic /worlds/{wid}/{kind})
GET    /worlds/{wid}/greetings                          → list
POST   /worlds/{wid}/greetings   {name, character, version, body?, requires_tags?, predecessor_join?}  → {id}
GET    /worlds/{wid}/greetings/{gid}                    → {meta, body}
PUT    /worlds/{wid}/greetings/{gid}  {name?, body?, requires_tags?, predecessor_join?}  → {ok}
DELETE /worlds/{wid}/greetings/{gid}                    → {ok}
PUT    /worlds/{wid}/greetings/{gid}/edges  {leads_to?, excludes?}   → {ok}
POST   /worlds/{wid}/greetings/import  {character, version}          → {greetings: [id, …]}

# Campaign play (before generic /campaigns/{cid}/{kind}; "greetings" is not an entity kind)
GET    /campaigns/{cid}/greetings/available             → [{id, name, available, reasons}]
POST   /campaigns/{cid}/scenes/{sid}/start-from-greeting  {greeting}  → {ok}
POST   /campaigns/{cid}/scenes/{sid}/opener  {prompt}   → SSE stream (ephemeral; reuses the
                                                          chat stream shape but never appends)
```

- The opener stream reuses the existing SSE event contract (`{delta}` … `{done}` / `{error}`)
  via a **non-persisting** variant of the chat stream helper (no `append_message`). The `409`
  missing-key contract (`_require_key`) applies exactly as for chat.
- `requires_tags` is accepted/returned as a list in the JSON body but stored comma-joined.

## Error handling / edges

- `GreetingNotFound` → `404`. Unknown character/version on create or import → `404`.
- `start-from-greeting`: non-empty scene → `409`; unavailable greeting → `409`; character version
  lock mismatch → `409` (defensive; `appear`'s existing rule).
- Importing a character with an empty `first_mes` and no `alternate_greetings` → `{greetings: []}`
  (no error).
- A greeting referencing a character/version deleted from the world: it still lists/reads, but
  `start-from-greeting` fails at `appear` (`404`/`409`); availability is unaffected (graph-only).
- `plotmap.json` / `played.json` missing or empty read as empty structures (no crash).
- Name collisions on greeting ids auto-uniquify (never an error).
- The opener `409` missing-key and SSE error events match the current chat tests.

## Testing (backend, pytest, temp `GRIMOIRE_HOME`, fake OpenRouter)

**`greetings.py`:**
- greeting CRUD round-trip; frontmatter scalars + body survive; `requires_tags` list ↔ comma-join.
- `import_from_character` → one greeting from `first_mes` + one per `alternate_greetings`; empty
  card → `[]`; each greeting references the right `character`/`version`.
- plotmap `set_edges` round-trip; `delete_greeting` prunes the node **and** inbound `leads_to`/
  `excludes` references.
- **`availability` (pure):** no-predecessor greeting is available; `all`-join locked until **all**
  predecessors played, `any`-join unlocked by **one**; a played excluder locks the target
  (and symmetrically); `requires_tags` ⊄ player_tags locks it; `reasons` populated on a lock.

**`playing.py`:**
- `read_played`/`_mark_played` round-trip; idempotent.
- `player_tags` unions appeared player-PC tags; ignores NPCs and character-cast-as-player.
- `available_greetings(cid)` reflects played + player tags end-to-end.
- `start_from_greeting`: seeds the first **assistant** post, marks played, `appear`s the
  character (locked version, `npc` role); `{{user}}`/`{{char}}` substituted in the seeded text;
  a **second** start on the same scene → `409`; starting an **unavailable** greeting → `409`.

**`context.build_opener_messages`:** a `system` message with the authoring instruction + activated
world-info + player persona (no NPC block) and a `user` message with the prompt; `{{user}}`
substitutes to the player name; always-on lore present, off-prompt keyword lore absent.

**Routes (fake OpenRouter):**
- world greeting CRUD + import; `/edges`; `/greetings/available`.
- `start-from-greeting` happy path + `409`s; the seeded scene reads back with one assistant post.
- `opener` streams deltas and **does not** append to the scene (scene stays empty); missing key →
  `409`.

The suite is green at 129 before this work; every task keeps it green.

## Phasing (for the implementation plan)

1. **`greetings.py`** — greeting CRUD + plotmap IO + `import_from_character`; world routes.
2. **Availability** — pure `availability()` (unit-tested) + `playing.py` played-set & `player_tags`
   & `available_greetings(cid)`; the `/greetings/available` route.
3. **Start-from-greeting** — `playing.start_from_greeting` + route (appear/seed/played/substitute).
4. **Opener** — `context.build_opener_messages` + the ephemeral `opener` SSE route.

## What's next

- **Spec 2c — Lorebook / world-info import** (standalone lorebooks + `character_book` → keyed Lore
  entries with per-entry category routing) — populates the `keys` the context builder consumes.
- **Frontend** — greeting editor, the plot-map graph editor, start-from-greeting and
  generate-opener UIs, alongside the rest of the deferred frontend.
