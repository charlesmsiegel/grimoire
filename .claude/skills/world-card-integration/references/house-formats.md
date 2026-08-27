# Entry shapes

Every example here uses invented names — see the privacy rule in `SKILL.md`. Before writing
anything, read two or three neighbouring entries in the World you're editing: these shapes are
conventions the store's own content settled into, not schema the app enforces, and a World may
have its own.

Write through `grimoire.store` (`entities.create_entity`, `greetings.create_greeting`,
`tags.add_tag`, `voice_anchors.write`). It allocates the slug from the name, quotes
frontmatter, bakes `{{char}}`, and writes atomically with the store's newline handling. The
frontmatter shown below is what those calls produce — it is here so you can recognise it while
reading, not so you can hand-write it.

---

## Lore entries

`entities.create_entity(root, "lore", name, body=..., keys=...)`

`keys` is the comma-separated list of strings that should pull this entry into context: the
full name, the short name, any in-world synonym. Single-word entries just repeat the name.

Two shapes are in use.

### Concept entry

```markdown
---
name: The Lamplighters
keys: Lamplighters, Lamplighter
---

A guild of the dead who tend the boundary lights, headquartered in the drowned quarter and
openly contemptuous of what the Assembly has become. Every Lamplighter undergoes the Parting,
which permanently severs memory from appetite. Vaunt, an old and patient wraith, speaks for
them inside the fractured local hierarchy and recruits from the river citadel.
```

One paragraph. A definitional first sentence, then the part specific to *this* World — who
here uses it, where it sits, what it has to do with the existing cast. An entry with no local
hook is a wiki paste; an entry with one is world material.

### Cast entry

A fixed five-line structure, one unwrapped line each. Don't add or reorder lines.

```markdown
---
name: Vaunt Ashgrove
keys: Vaunt Ashgrove,Vaunt,Ashgrove
---

Known to: the Lamplighters only and no one else.
Occupation: Vaunt Ashgrove keeps the boundary lights along the drowned quarter and screens
candidates for the guild. Her arts are Parting, Ember and Reckoning; she can hold a light lit
through a surge that would take a whole street, and she cannot cross running water she has
not herself bridged.
Type: Wraith, Lamplighter, guild officer.
Appearance: 38 years old, long black hair, a featureless face with neither eyes nor nose save
a mouth, tall height (188cm), wears the guild's black robes.
Personality: calm, exacting, patient, unsentimental, tired. She measures people by what they
will become rather than what they are, and says so, which is why the Assembly stopped inviting
her to speak.
```

- **Known to** — the audience inside the setting, phrased as a restriction.
- **Occupation** — what they do, who they serve, what powers they have, what those powers
  cannot do, and how they relate to named others.
- **Type** — template, breed or clan, faction, rank.
- **Appearance** — age, hair, eyes, build, height with the figure in parentheses,
  nationality, dress, then the supernatural second form where there is one.
- **Personality** — a short list of adjectives, then what those adjectives cost them and the
  people around them. Likes and dislikes fold into this prose rather than getting their own
  line.

---

## Groups

`entities.create_entity(root, "groups", name, body=..., keys=...)`

```markdown
---
name: The Lamplighters
keys: Lamplighters, Lamplighter
---

A guild of the dead who tend the boundary lights...

The local chapter is five. Vaunt Ashgrove screens candidates and speaks for them upstairs;
Merrow keeps the ledgers and is the reason the chapter still has a budget; Pike walks the
south bank...
```

Multi-paragraph is right when the group has a roster — the second paragraph is where each
member gets a clause saying what they do. `keys` is optional and many entries omit it.

## Locations and items

`entities.create_entity(root, "locations" | "items", name, body=..., owners=...)`

`owners` is a comma-separated list of `characters:<id>` or `<kind>:<id>` references, and
`verify_manifest` checks every one resolves. Items are normally owned; public locations
normally aren't.

```markdown
---
name: 'Vaunt''s Lantern'
owners: 'characters:vaunt'
---

The shuttered brass lantern Vaunt Ashgrove carries on every crossing, its glass starred where
something struck it and was refused. She has never explained the crack and does not set the
lantern down, which is the whole of what anyone knows about the night she earned it.
```

Describe what the object reveals about its owner, or what a place is *used* for — the rooms
that matter, who is found in each. A location nobody does anything in doesn't need an entry.

---

## Greetings

`greetings.create_greeting(root, name, character, version, body=..., present=[...],
requires_tags=[...])`

```markdown
---
name: The Crossing at Low Water
character: vaunt
version: default
present: vaunt
requires_tags: wraith
predecessor_join: all
pcless: ''
---

Vaunt was already on the causeway when {{user}} came down the steps...
```

- `name` — your title for the scene, not the card's generic label.
- `present` — every character in the scene, usually just the one; verified against the roster.
- `requires_tags` — tag ids from the World's vocabulary, or empty.

`first_mes` becomes the character's base greeting; `alternate_greetings[i]` follow it. The
body is the card's text verbatim, `{{user}}` intact — `create_greeting` bakes `{{char}}` for
you at write time.

## Plot-map edges

`greetings.set_edges(root, gid, leads_to=[...])`

Only greetings that begin or continue a chain appear in the map at all. Both the source and
every target must be real greeting ids, which `verify_manifest` checks.

## Tags

`tags.add_tag(root, "Lamplighter")` → the tag id, slugified from the display name. Read the
vocabulary first with `tags.read_tags(root)`; this is a player-character vocabulary, so only
add a tag when greetings need to gate on a kind of PC the World cannot yet name.

## Voice anchors

`voice_anchors.write(root, char_id, text)`

```markdown
Calm, measured, deliberate; every sentence purposeful.
Balanced appraisals - the admirable quality named, then the flaw that follows from it.
Reaches for long-view judgements: what someone will become, not what they did.
"No one crosses at low water twice." - aphorism as conclusion.
Never hurried; the authority is in the pacing.
```

A few lines of speech habits, one of them a short quoted line from the card that demonstrates
a habit. `voice_anchors.anchorless_ids(root, ids)` lists who still needs one.
