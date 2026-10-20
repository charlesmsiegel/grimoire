# World plan schema and example

Source: [create_world.py](../../../../../backend/scripts/create_world.py), [test_create_world.py](../../../../../backend/tests/test_create_world.py).

### 3. Write the plan

A complete example, with every key shown. Omit anything you don't need.

```json
{
  "world": "Saltmarch",
  "calendar": {"provider": "gregorian", "region": "US"},
  "module": "pool-basic",
  "tags": ["Outsider", "Guild Member"],
  "characters": [
    {"name": "Seraphine",
     "description": "The harbourmaster of Saltmarch, ...",
     "personality": "dry, exacting, tired",
     "scenario": "",
     "voice_anchor": "Short declaratives; never answers a question with a question.\n\"The tide doesn't wait. Neither do I.\" - the clipped close."},
    {"name": "Mara", "description": "A tide-reader for the guild, ..."}
  ],
  "pcs": [
    {"name": "Winifred", "tags": ["Outsider"], "pronouns": "she/her",
     "summary": "A stranger off the packet boat.", "description": "..."}
  ],
  "locations": [
    {"name": "The Counting House", "body": "Where the guild meets ...",
     "keys": ["Counting House"], "fields": {"climate": "temperate-coastal"}}
  ],
  "lore": [
    {"name": "The Salt Pact", "body": "...", "keys": ["Salt Pact", "pact"]},
    {"name": "The Ledger", "body": "...", "owners": ["characters:Seraphine"],
     "secrecy": "secret"}
  ],
  "groups": [
    {"name": "Salt Circle", "body": "...", "keys": ["Salt Circle", "guild"],
     "fields": {"leader": "characters:Seraphine",
                "members": ["characters:Mara", "pcs:Winifred"],
                "headquarters": "locations:The Counting House"}}
  ],
  "items": [
    {"name": "Salt Knife", "body": "...", "owners": ["characters:Seraphine"],
     "fields": {"holder": "characters:Seraphine"}}
  ],
  "creatures": [],
  "greetings": [
    {"name": "Saltmarch Eve", "character": "Seraphine", "location": "The Counting House",
     "body": "{{char}} looks {{user}} over at the quay ...",
     "leads_to": ["The Reckoning"]},
    {"name": "The Reckoning", "character": "Mara", "present": ["Mara", "Seraphine"],
     "requires_tags": ["Outsider"], "predecessor_join": "all",
     "body": "..."}
  ]
}
```

**References.** Anywhere a record names another one, write its **name**: a greeting's
`character`, `present`, `location`, `requires_tags`, `leads_to` and `excludes`, a PC's
`tags`, and an entry's `owners` and ref `fields`. Both are spelled `<kind>:<name>`:
`owners` takes `characters`, `pcs` or `locations` (the only things that can be
present in a scene), and each ref field takes the kinds `store/entity_schema.py`
lists for it (a group's `leader` is a character or PC, its `headquarters` a
location). A name the plan creates works anywhere in the same plan, in any order.
An existing record's id also works, but a name always wins. A reference that is
one record's name and another record's id (the leftover from a rename in the app)
is refused as ambiguous rather than guessed.

**Per kind:**

| kind | keys a plan may set |
|---|---|
| `characters` | `description`, `personality`, `scenario`, `first_mes`, `mes_example`, `alternate_greetings`, `tags`, `creator_notes`, `system_prompt`, `post_history_instructions`, `nickname` (the V3 card's `data`), plus `voice_anchor` |
| `pcs` | `tags`, `pronouns`, `summary`, `birthdate`, `goals`, `player_notes`, `description` |
| `locations` `lore` `items` `groups` `creatures` | `body`, `keys` (list), `owners` (list), `secrecy` (`public`/`secret`/`gm-only`), `fields` |
| `greetings` | `body`, `character`, `present`, `location`, `requires_tags`, `predecessor_join` (`all`/`any`), `pcless`, `phase`, `sequence`, `optional`, `leads_to`, `excludes` |

`fields` are the per-kind typed fields in `store/entity_schema.py`: a location's
`climate`/`persistence`/`weather_zone`, an item's `item_type`/`rarity`/`holder`, a
group's `group_type`/`leader`/`members`/`headquarters`, and a creature's
`creature_type`/`threat`/`habitat`. Lore has none. Game stats are not fields. They
are sheets, owned by the bound module (`create-mechanics-module`).

A group's `members` is an explicit list of character/PC refs; its `leader` is
independent and need not be listed as a member. Keep the prose roster for what
each actor does, and record membership in `fields.members`; prose is not mined.


Put the NPCs' opening scenes in **greetings**, not in a card's `first_mes`. A world
greeting carries its cast, location, tags and plot-map edges, and a card's
`first_mes` carries none of them.

For the *prose* of each record, [house formats](../../../world-card-integration/references/house-formats.md)
shows the entry shapes the author's existing Worlds settled into: a concept entry,
a five-line cast entry, a group with its roster, and a voice anchor. A new World
can follow them or set its own, but it should be consistent with itself.

**Patch semantics.** A key the plan gives is written, a key it omits is left as it
is, and a record the plan doesn't mention is never touched. Nothing is ever
deleted. Re-applying the same plan changes nothing (the World row reads `found`,
every other row `unchanged`), so iterate on the plan freely: fix it, re-apply with
`--world-id`, re-check. A character's card fields are written to its default
version, and a greeting is re-pointed only when the plan names a different
character, so a version chosen in the app survives a re-apply.
