# Character voice in the scene prompt — the anchor stops being judge-only

## Problem

Character speech comes out same-y: two NPCs in a scene produce lines that
could be swapped without anyone noticing. This is a different complaint from
"the prose is robotic", and it has a different cause. The prompt fights
robotic prose at length in `scene/sections/natural_prose.j2` — a ~500-word
block of banned phrases, rationed beat words, and forbidden constructions.
It fights same-y-ness nowhere at all.

Four findings. The first, third and fourth explain the symptom directly; the
second is a latent defect that appears only once a context budget is set, and
is fixed here because the same change fixes it for free.

### 1. The voice anchor feature is inert, and would not help if it weren't

**Anchors are unpopulated, and nothing populates them.** No import path, no
absorb step and no migration writes a `voice_anchor.md`; the only way one
exists is if someone typed it into the character editor. A roster that has
never had that done to it has none — and with no anchor,
`templates/voice_drift/` does not run and
`templates/scene/voice_correction.j2` does not render, so the subsystem is
switched off by default rather than by choice.

More importantly, it would not address this complaint even fully populated.
`context/cast.py:276` reads the anchor only to *validate* an outstanding
drift flag, and there is no `Section(...)` for it in `context/assemble.py`'s
catalog. The anchor — the one artifact per character that says exactly how
that person sounds — is judge-side only. It can flag same-y speech after a
scene has been played; it can never prevent it during one.

`store/voice_anchors.py`'s module docstring states this as a deliberate
choice: the anchor "is never sent as part of a scene — which is what lets it
describe a voice". This spec reverses that decision deliberately; §7 sets
out what it costs and what is done about it.

### 2. The strongest available voice signal sits in the third-dropped tier

Of the voice-bearing card fields, `mes_example` is the one most often filled
in; `personality` is much less common, and `system_prompt` and
`post_history_instructions` rarer still. Many cards have no `mes_example`
either — but for those that carry any voice signal at all, and for
SillyTavern-style cards especially, example dialogue is frequently the only
one.

`assemble.py:454` places it at `pack.BACKGROUND`; `pack.py:86` reads
`DROP_ORDER = (RECALLED, ARCHIVE, BACKGROUND, SPOTLIGHT)`. Example dialogue
is dropped **third** — after recalled lore and the archive, but ahead of
character state, relationships, plot threads and the current setting.
Promotion to `SPOTLIGHT` moves it to the last droppable tier; it does not
make it undroppable, and this spec claims nothing more.

**This is a latent defect, not necessarily a current cause.**
`context_budget` defaults to `"0"` (`config.py:25`), which `pack.py`
documents as unbounded — nothing counted, nothing dropped. On an install that
has never set it, no section is dropped at all and `mes_example` reaches the
model in full, so the tier explains nothing there; findings 1, 3 and 4 do.
What the tier does is guarantee the problem appears, worst-first, the moment
a budget is set — which is the natural response to a context-window error,
i.e. it gets set precisely in the large-cast campaigns where voice matters
most.

### 3. The card blocks are unattributed blobs

`character_descriptions.j2` joins every present NPC's `description` /
`personality` / `scenario` with blank lines and **no name attached**.
`message_examples.j2` does the same for `mes_example`. Three NPCs on scene
means three personality paragraphs and three dialogue samples the model must
attribute by inference.

For cards in the SillyTavern idiom this is worst: examples lean on `{{char}}`
and unattributed narration, so once concatenated there is genuinely nothing
tying a sample to the character it came from.

### 4. `natural_prose.j2` is entirely prohibitive

It bans phrases, rations beat words, forbids constructions, prescribes
rhythm. It never says *these characters must not sound like each other*. A
shared ban list cannot produce distinct voices, and plausibly works against
them: every character is narrowed toward the same surviving register.

### Scope note: this spec does not address "robotic"

The two complaints arrived together and are treated separately. Both changes
would target the same visible surface, so landing them together makes it hard
to attribute an improvement to either — and robotic-ness is partly
voicelessness, since a character with no distinct register falls back to the
model's house style.

**This separation buys attribution, not measurement.** There is no baseline
corpus, judge, or metric here, and none is proposed; the assessment is a
human reading played scenes. Sequencing means that when the prose work
starts, whatever same-y-ness remains is known not to be voicelessness.

One trap is worth recording for that later work: `natural_prose.j2` states
that a prose style guide, when set, overrides its Rhythm paragraph — the part
aimed most directly at robotic cadence. So a campaign with a style set is not
receiving the same instructions as one without, and any comparison between
them has to account for it. It explains nothing about campaigns that have no
style set, which is the default.

## Design

### 1. Three sections replace one

In `assemble.py`'s `SECTIONS` catalog, remove:

```python
Section("message_examples", "Message examples",
        "scene/sections/message_examples.j2", pack.BACKGROUND),
```

and add, in this order, immediately after `character_descriptions` and before
`character_state`:

```python
Section("voice_policy",   "Voice · the rule",
        "scene/sections/voice_policy.j2",   pack.LOCK_IN),
Section("voice_anchors",  "Voice · how they sound",
        "scene/sections/voice_anchors.j2",  pack.SPOTLIGHT),
Section("voice_examples", "Voice · example dialogue",
        "scene/sections/voice_examples.j2", pack.SPOTLIGHT),
```

**Why three, and why these tiers.** They are three different kinds of thing,
and `pack.py`'s tier semantics already name the difference:

- `voice_policy` is **fixed-length instruction text** — the differentiation
  rule and the precedence order. That is `LOCK_IN`'s own description: "the
  instructions that define the reply itself… losing them does not cost the
  model information, it costs the model its brief." It is bounded by
  construction (it is a constant string), so pinning it costs a known amount.
- `voice_anchors` and `voice_examples` are **per-character information**,
  which is `SPOTLIGHT`'s description ("who is present and what they know").
  Both are cast-sized and therefore unbounded in aggregate, which is exactly
  what must not go in `LOCK_IN`.

**Anchors are NOT guaranteed to outlive examples, and an earlier draft of
this section claimed they were.** `pack.py:39` drops the largest *actual*
section within a tier, not the one with the larger per-item cap. One NPC with
a full 1,200-character anchor and an example of `"Yes."` inverts the order;
so does a cast where ten characters have anchors and one has an example; and
a **pinned** examples section never drops at all, since `pack.py` says a pin
outranks every tier. What the split actually buys is that the packer *can*
drop one without the other, and that the inspector prices them apart — the
usual case, where examples are several times larger, does drop examples
first. That is a tendency, not an invariant, and nothing in this spec may
depend on it.

**An earlier revision put anchors at `LOCK_IN` and added an aggregate
character cap to bound them. That was wrong and is withdrawn.** The cap
immediately produced its own defects: a character elided by the aggregate cap
would still be judged against the anchor the generator never saw; a single
elided anchor could suppress the very section meant to announce the elision;
and the cap's scope contradicted its own test. All of it was compensating for
putting unbounded per-cast content in a tier the packer may not reclaim.
Letting the packer do its job removes the cap, the omission line, the
`anchors_omitted` count and the reserve arithmetic together.

**Each section carries its own heading. There is no shared heading and no
macro.** This is a deliberate departure from `off_scene_cast_active.j2` /
`off_scene_cast_known.j2`, which share one via a macro that tier 3 suppresses
`if not offscene_active`. **That precedent is broken**: `offscene_active` is
data (`assemble.py:221`), not render state, while `layout.py` lets a user
disable, reorder and relabel any section. Disable `off_scene_cast_active` and
tier 3 still suppresses the heading, so the shipped directory renders with
none; reorder them and the heading lands mid-block. Any data-derived "am I
first?" test has this defect, because the question is about the merged,
enabled layout and the data cannot see it.

So the three open `# Voice`, `# Voice — how they sound`, and `# Voice —
example dialogue`, each independently correct under every layout.

*(The off-scene heading bug is pre-existing and out of scope. File separately
rather than fix in passing.)*

**Merged layout order** follows `layout.py`'s `_ordered`, whose loop walks
catalog order and whose docstring states each newcomer "is findable as the
next one's predecessor once inserted" — so the three land in catalog order
after `character_descriptions`. They land after it whether it is enabled or
disabled (`_ordered`'s anchor search reads `out`, "which holds the DISABLED
sections too") and wherever a layout repositioned it. All three cases are
pinned by test.

**Layout migration is a real migration, not an accepted loss.** `layout.py`'s
rule would retire the `message_examples` id silently, discarding its stored
position, its label, and — most importantly — an explicit `enabled: false`,
which would return as three enabled sections and change what the model
receives. An earlier revision accepted this loss on the grounds that a layout file
may well not exist. That reasoning does not survive contact with an install
where it does, so instead:

> A stored entry with id `message_examples` is rewritten to `voice_examples`,
> preserving its `enabled` flag and position. Its stored label is dropped,
> since the section's meaning has narrowed. `voice_policy` and
> `voice_anchors` enter as newcomers by the ordinary rule.

Four details the algorithm needs, because without them it does not close the
hole it exists to close:

- **The remap runs on `stored` before `_ordered`**, not inside `merge`.
  `layout.py` deliberately shares `_ordered` between `merge` and `describe`,
  so a remap applied to only one of them would let the renderer honour a
  disable while the editor shows a new enabled section — and reverses it the
  moment the user saves.
- It is a **read-time compatibility alias, not a persisted rewrite**, unless
  a save happens to persist it; nothing in this spec writes the layout file.
  "One-time" was the wrong word.
- If `stored` somehow contains **both** ids, `voice_examples` wins and the
  legacy entry is discarded — the newer id is the one the user's editor
  produced.
- The dropped label is dropped in **both** paths, so `describe()` offers the
  catalog label as the editable override rather than the legacy one.

**This makes the order claim above conditional**, which the earlier draft
missed: a migrated `voice_examples` keeps the legacy *position* — after
`absent_players` in the shipped catalog — while `voice_policy` and
`voice_anchors` land after `character_descriptions`. That is intended (the
user placed it there) but it is not "all three in catalog order", and the
order test must exclude a migrated `voice_examples`. Local edits to `message_examples.j2`
are still lost — `prompts.py` loads `templates/` from disk so a user may have
edited it — and that one is genuinely accepted: a template body cannot be
migrated into two differently-shaped files.

### 2. The data contract: one resolved cast structure

No template performs storage IO. `_assemble` builds one variable, where the
cast entry (`a["id"]`), the card data and `cid` are all in hand. It replaces
`npc_cards` as the input to all four affected sections:

```
cast_blocks:     [ {"name": str,        # "" when the card has none
                    "description": str, # description/personality/scenario joined
                    "anchor": str,      # effective anchor, §3
                    "example": str},    # capped mes_example, §3
                   ... ]                # one per PRESENT NPC, cast order
named_npc_count: int   # entries with a non-empty name
```

**Every present NPC whose card can be READ gets an entry, and nothing is
filtered out beyond that.** The card read is the pre-existing filter — a
present NPC whose card or locked version is missing already drops out of
`npc_cards`, and cannot contribute a block it has no data for. An earlier
revision dropped entries whose anchor and example were both empty, which
silently defeated the cast-size rule below — two NPCs with neither would have
produced an empty list and no differentiation instruction. Filtering is each
template's business, per-block.

- `name` is `card_data["name"]`, **stripped**; a whitespace-only name is
  `""`. Stripping is not a detail: an unstripped `"   "` is truthy, so it
  would raise `named_npc_count`, trip the day-one rule and render a blank
  heading. `anchor` and `example` are stripped for the same reason, matching
  what the existing templates already do with these fields.
- A nameless card **keeps its entry** so `character_descriptions` does not
  regress (it renders those descriptions today); the voice sections skip
  nameless entries, because an unattributed anchor or sample is exactly
  Problem §3's defect.
- **A name shared exactly with another present card is BLANKED**, which
  suppresses that character's anchor and example blocks while their
  description still renders, headerless. Comparison is case-folded equality.

  Two earlier revisions of this bullet were wrong, and both mistakes are worth
  keeping because each looks right. It first required `scenes.confusable`;
  implementation showed that predicate cannot be satisfied by disambiguation
  at all — `confusable("Winifred #1", ["Winifred #1", "Winifred #2"])` stays
  `True` — and that its argument convention is inverted for this question. It
  then required a cast-order ordinal, `Winifred #1` / `Winifred #2`, and
  review showed that reaches past the prompt: the transcript identifies
  speakers by card name and nothing else, so a model copying a heading into
  its `**<Name>:**` marker persists a synthetic label into the scene, which is
  the one artifact in this app that cannot be regenerated.

  Nothing was lost by withdrawing it. `match_name("Winifred", ["Winifred",
  "Winifred"])` is already `None`, so the duplicate case was never routable;
  the ordinals would only have made an unroutable label synthetic as well.
  Blanking matches what the codebase already does here — `_voice_notes`
  suppresses a corrective addressed to such a name, and the absorb stage
  reports the clash rather than judging it.

  Exact duplication rather than `confusable` for a second reason: `confusable`
  reports "Winifred Vance" beside "Winifred Vale" as colliding, but those are
  the headings that render and `match_name` resolves each exactly. Only the
  bare "Winifred" is ambiguous and nothing writes it, so blanking them would
  cost two distinguishable characters their voices to prevent nothing.
- `anchor` is the **effective anchor** (§3) of
  `overlay.voice_anchor_record(cid, a["id"])["text"]`. The overlay resolver
  makes a campaign anchor override the world's, and a campaign tombstone
  (`disabled: true`, empty body) resolve to `""` rather than falling through.
- `example` is the capped `card_data["mes_example"]` (§3).

`named_npc_count` is what the cast-size rule reads: a count, not a length, so
per-block filtering cannot move it.

`{{user}}` substitution is applied generically per rendered section at
`assemble.py:572`, so all sections inherit it unchanged.

### 3. What the sections render, and the caps

**`voice_policy.j2`** renders when **any** of: a **named** entry has an
`anchor`; a **named** entry has an `example`; `named_npc_count >= 2`.

```
# Voice

Each character present must be distinguishable by their dialogue alone. If
two of their lines could be exchanged without a reader noticing, the reply
has failed, whatever else it got right. Differences in diction, rhythm,
formality and what a person will not say are the point, not decoration.

Where a character's voice description and their example dialogue disagree,
the description wins — it is maintained, the examples are a snapshot. Where
either disagrees with a general prose rule elsewhere in this prompt, the
character's voice wins: those rules exist to stop generic writing, not to
flatten a specific person. An outstanding voice correction outranks all of
it — it is the most recent and most specific feedback about this character.
```

This is **canonical wording, not a placeholder** — §9 requires it verbatim in
an eval, so leaving it to the implementer would make an arbitrary first draft
a permanent contract. Change it here, deliberately, and update the eval.

It refers to "a character's voice description" rather than "this section",
because `voice_anchors` is `SPOTLIGHT` and may legitimately be absent when
the policy is not. The precedence order is stated in terms of the kinds of
claim, not their location, so it stays true under any layout and any drop.

"Named" in the first two conditions is load-bearing: §2 has the voice
sections skip nameless entries, so a condition counting *any* entry would let
one nameless NPC with an anchor render a policy section whose subject matter
is then entirely suppressed.

**`voice_anchors.j2`** renders when at least one named entry has an `anchor`:

```
# Voice — how they sound

## <Name>
<anchor text>
```

**`voice_examples.j2`** renders when at least one named entry has an
`example`, in the same shape under `# Voice — example dialogue`.

**`character_descriptions.j2`** switches from `npc_cards` to `cast_blocks`,
rendering `## <Name>` above each entry's `description` and omitting the
heading (not the description) when `name` is `""`. It stays at `LOCK_IN` — a
rendering fix, not a re-tiering.

**Caps.** `voice_anchors.write` enforces no length limit and `read_record`
accepts arbitrary prose, so both per-character values are capped at render:

- **`voice_anchors.effective(text)`** — a new pure function applying
  `VOICE_ANCHOR_CAP` (1,200 characters, ~300 tokens). It is used by **both**
  `_assemble` and the drift-judge prompt builder. One transformation, two
  consumers: otherwise the generator receives a truncated anchor while the
  judge reads the full one, so a rule past character 1,200 would be
  invisible to the writer and still enforced against it.

  Capping is therefore **not** a source of generator/judge divergence: both
  copies come from this one function. The divergences that do remain —
  `{{user}}` substitution applied per rendered section (`assemble.py:572`),
  a packer drop, a layout disable, an edit between play and absorb, a local
  template edit — are listed in §7, along with the advisory framing that
  makes them documented limits rather than defects.
- **`VOICE_EXAMPLE_CAP`** — 3,000 characters, chosen so that a card's
  examples have room for several full exchanges before anything is cut. It is
  a ceiling on the outliers, not a target: a typical card's examples pass
  through untouched, and the cap exists so one unusually long card cannot
  dominate the section. Tune it against real prompts once the section is
  live rather than treating the number as settled.

**Truncation** takes the longest prefix at or under the cap ending on a
boundary — a `<START>` marker or a blank line, whichever occurs **latest**
within that prefix — cutting *before* a chosen `<START>` so the partial block
it opened is discarded. Two fallbacks, both required:

- If the prefix contains no boundary, it is **a hard character cut and may
  land mid-line**. The rev-2 claim that a sample "never ends mid-line" is
  withdrawn; a 2,500-character single-line example falsifies it.
- If applying the boundary rule would keep **less than half the cap**, the
  hard cut is used instead. "Near-empty" was left undefined in an earlier
  draft, which made a boundary at position 1, 100 or 500 indeterminate; the
  threshold is now a number. The motivating case is an example whose only
  boundary is a `<START>` at position 0, which would otherwise truncate a
  5,000-character sample to nothing.
- A chosen **blank-line** boundary cuts *after* the last non-blank line and
  the trailing blank is not retained; a chosen **`<START>`** cuts immediately
  *before* the marker. Both are exact slice positions so that the prompt's
  copy and the judge's copy — which come from the same function — cannot
  drift apart on an off-by-one convention.

All truncation happens while `cast_blocks` is built, so the inspector's token
breakdown measures what was actually sent.

### 4. All four sections share §2's rules

Because `character_descriptions` and the three voice sections read one
`cast_blocks`, disambiguation and nameless handling are defined once and
cannot diverge. This is the reason for a single structure rather than a
voice-only one: an earlier revision asserted that `character_descriptions`
followed the voice rules while leaving it on `npc_cards`, which supplies
neither a resolved display name nor a version label — an implementer could
not have satisfied it.

### 5. The anchorless flag

`store/characters.py:322`'s `list_characters` gains **`has_voice_anchor:
bool`** beside `tagline`, meaning `bool(world-level anchor text)` — `true`
means the character **has** an anchor.

derives `anchorless = chars.filter(c =>
c.has_voice_anchor === false)`, beside the existing `untagged` (line 263), and
surfaces its count in **world scope only**, as `untagged` does.

| world `voice_anchor.md` | `has_voice_anchor` | in `anchorless` |
|---|---|---|
| absent | `false` | yes |
| present, non-empty body | `true` | no |
| present, `disabled: true`, empty body | `false` | yes |

The tombstone row is not a special case: `voice_anchors.read_record`
documents that at world level a tombstone and an absence are the same state,
and tombstones carry meaning only in a campaign, which this world-scope
backlog does not cover.

**Deliberately no bulk-generate button**, unlike the tagline backlog.
Generating a whole roster's anchors unattended would write inferred voices —
which now steer every scene rather than only judging one — into the prompt
with the same authority as hand-written ones, at a volume nobody will review
after the fact. The app reports the gap and stops.

**One read per character.** `list_characters` is already the expensive
listing (~200ms on a large world per the tagline route's docstring, which
attributes it to `list_images` and the per-version `_card_summary` calls, not
to `taglines.read`). The lookup adds exactly one file read and returns a
boolean, never the body.

### 6. Out of scope: writing the anchors

This spec ships the machinery. Populating a roster is content work for a
separate planning conversation after the code lands: on any established
library the volume is too large to approve in one pass, and an anchor is
easier to judge against a real played scene than in the abstract. The first
batch should be scoped to the cast of actively-played campaigns.

Nothing here depends on that work. With zero anchors the change still
delivers the differentiation rule and precedence order (§3), name-labelled
descriptions and examples (§3), and examples promoted out of the third
droppable tier (§1).

### 7. The judge stays approximate, and stops pretending otherwise

Once the anchor is sent to the generator, the drift check is no longer
comparing a played scene against a reference the writer never saw. **The
decision is to send it anyway**: a voice system that can only report failure
after a scene is played does not solve the problem that prompted this work.
The player wants different voices, not a report that they were the same. What
remains as an independent check is the player reading the scene, which is
what surfaced this issue in the first place.

**What this spec does NOT do is claim the judge measures compliance.** An
earlier revision said the verdict should be read as "did the model follow the
brief it received", and specified machinery to make that true: a per-scene
record of characters whose anchor failed to reach a turn, suppressing their
judgement. That is withdrawn. The claim was the problem, not the machinery —
once the spec asserts exactness, every ordinary divergence becomes a defect
requiring more mechanism, and there are five:

- `{{user}}` is substituted in the prompt copy only;
- the packer may drop `voice_anchors` under a budget;
- the layout may disable it;
- the anchor may be **edited between playing the scene and absorbing it**, so
  the judge reads text no turn ever used;
- `templates/` is user-editable, so a local edit to `voice_anchors.j2` can
  omit a character while the section still renders and reports delivered.

Closing all of them means snapshotting the delivered brief with every
accepted turn, co-committed under the same campaign lock as the transcript
append, plus render-produced per-character delivery metadata. That is a large
amount of machinery to make a secondary quality loop — one that has never
once run in this library — exact.

**So the judge keeps doing what it does today**: it compares a scene against
the character's *current* anchor, and its verdict is advisory. No new
persistence, no per-scene record, no new campaign-lock-domain module, no
change to how `_stage_voice_drift` decides what to judge. The honest framing
is that drift detection is a heuristic second opinion, and a `drift` verdict
means "worth looking at", not "provably violated an instruction the model
was given". `store/voice_anchors.py`'s rewritten docstring says exactly that,
so the next reader does not reconstruct the stronger claim from the fact that
the anchor is now in the prompt.

**One real bug is fixed here regardless of framing: the judge must see the
standing correction.** §3 tells the generator that an outstanding voice
correction *outranks* the anchor, but `voice_drift/user.j2` supplies only
`name`, `anchor` and `transcript`. Given an anchor saying "never uses
contractions" and a standing correction saying "use contractions, the last
scene was too stiff", `voice_correction.j2` tells the writer to obey the
correction "before anything else in this reply" — and the judge then flags it
for obeying. The flag mints another correction, so the false positive is
self-reinforcing. This contradiction is created by this spec and ships fixed
in it.

The fix is three coordinated changes, and an earlier revision got it wrong by
attempting only the second:

1. **`system.j2` does need a rewrite.** It currently defines drift
   *absolutely* against the anchor — drift is a register "the anchor rules
   out", and lines "consistent with the anchor" are `in_voice`. Adding an
   overriding correction to the user message alone produces a contradictory
   evaluator: the system message says anchor-forbidden contractions are
   drift, the user message says prefer the correction that requires them. It
   must instead define the standard as **the anchor as modified by any valid
   outstanding correction**, with the correction controlling where the two
   conflict.
2. **`user.j2` gains an optional `correction`**, rendered in its own block
   and omitted entirely when absent — the existing three-variable contract is
   extended, not replaced, so a character with no correction produces the
   same **user message** it does today. Not the same *prompt*: `system.j2`
   is rewritten for every invocation, correction or not, so no character's
   drift prompt is byte-identical to today's. The unchanged half is the user
   message alone.
3. **`build_prompt` takes the correction as a parameter, and the CALLER
   validates it.** `voice_drift.py` states a "prompt/parse only" boundary, so
   it must not read the flag itself.

**Only a correction that would actually be in force may reach the judge.**
`_stage_voice_drift` already reads `prior, prior_fp = flag["note"],
flag["anchor"]` (`routes/scenes.py:1966`) and hands `prior_fp` to
`stage_edit` — but it passes neither to `build_prompt` at line 1967. So the
provenance rule lives downstream of the prompt, and a correction fingerprinted
to a *replaced* anchor would be presented to the judge as current. Concretely:
anchor A forbids contractions and a flag fingerprints A; the user replaces it
with anchor B saying contractions are habitual. `context/cast.py:282` already
suppresses that flag for the generator via `fingerprint_matches`, so the
writer sees anchor B and no correction — while the judge would be told the
retired "avoid contractions" note overrides B, and would mint a fresh flag
against it. The same `fingerprint_matches(prior_fp, record["text"],
record["id"])` check `_voice_notes` applies must therefore run **before**
`build_prompt`, and a correction that fails it is omitted.

### 8. Known limits, stated

- **Neither voice section is bounded in aggregate.** Per-character caps do
  not bound cast-sized content — a large enough present cast multiplies the
  per-character ceiling without limit. This is deliberate and safe *because* both
  sections are `SPOTLIGHT` — the packer reclaims them under a budget, largest
  first. It is only unbounded when `context_budget` is `0`, where by
  definition nothing is bounded and the whole prompt is sent as-is.
- **Sections are dropped whole.** Under pressure every character's examples
  go together; `pack.py` has no per-item granularity and adding it is not
  attempted here.
- **Nameless cards lose their example dialogue**, which today reaches the
  model unattributed. Judged an improvement, but it is a removal.
- **Nameless cards' descriptions stay unattributed** — they keep their entry
  and render without a heading, which is Problem §3's defect preserved rather
  than fixed for those cards. There is no name to attribute them to, and
  synthesising one from the character id would put a slug in the prompt as if
  it were a name. A card reaching play with no `name` is arguably the real
  defect and belongs upstream in import/authoring.
- **Inferred anchors are not distinguished from authored ones.** No
  `inferred` flag ships, because no generator ships that would set one.
- **The drift judge is approximate and its verdicts are advisory** (§7). It
  compares against the character's *current* anchor, which may differ from
  what any turn actually received — substituted, dropped under budget,
  disabled in the layout, edited between play and absorb, or omitted by a
  locally-edited template. (Capping is *not* on that list: §3 applies the
  same cap to both copies.) A `drift` verdict means "worth looking at".
  Making it exact is a real option, priced in §7; it is not taken.
- **A locally-edited `voice_correction.j2` has the same asymmetry as
  `voice_anchors.j2`.** The judge is now told a standing correction is
  authoritative (§7); a user who edits that correction out of the generation
  template leaves the judge weighing an instruction the writer never got.
  Accepted on the same advisory grounds, recorded so it is not a surprise.

### 9. Documentation and guards that move with the code

- **`store/voice_anchors.py`'s module docstring** asserts the anchor "is
  never sent as part of a scene" and builds a paragraph on why it differs
  from `mes_example`. Both are falsified. The rewritten distinction is
  *describe* vs *demonstrate*, and it must record §7's outcome — that the
  anchor now steers generation, and that the drift check is an approximate
  second opinion rather than a proof, so a later reader does not reconstruct
  the stronger claim from the anchor's new position.
- **`natural_prose.j2`'s precedence paragraph must be amended, and this is
  not optional.** It ends "Everything else here holds regardless", after
  excepting only the reply format, established facts and the prose style
  guide. §3's canonical text says a character's voice overrides general prose
  rules. Left as-is the prompt carries two contradictory instructions: an
  anchor requiring frequent murmuring, a signature repetitive construction,
  or the word "indeed" collides head-on with a block saying its bans hold
  regardless. The amendment adds the voice policy to that exception list.
- **`templates/voice_drift/system.j2` and `user.j2`, and
  `voice_drift.build_prompt`'s signature** — the judge is given the
  character's valid outstanding correction, and `system.j2` redefines the
  standard as the anchor *as modified by* that correction rather than the
  anchor absolutely (§7). All three move together; changing only `user.j2`
  produces an evaluator whose two messages disagree.
- **`routes/scenes.py:1967`** — validates the correction's provenance with
  `voice_drift.fingerprint_matches` before passing it to `build_prompt`,
  mirroring `context/cast.py:282`. `voice_drift.py` keeps its "prompt/parse
  only" boundary, so the caller does the store read, not the builder.
- **`CharacterEditor.tsx:2198`'s field hint** ("absorb checks each scene
  against this and flags drift") becomes incomplete: the anchor now steers
  every turn. The editor should also warn above `VOICE_ANCHOR_CAP`, which is
  a Python constant — **it reaches the frontend on the existing
  `GET /config` payload** (`routes/config.py:192`), which this UI already
  reads, rather than being duplicated as a TypeScript literal that can drift
  from the backend's truncation. **The unit must be stated with the number**:
  Python counts Unicode code points and JavaScript's `.length` counts UTF-16
  code units, so an anchor of 1,100 astral characters is under the backend's
  cap and reads as 2,200 in a naive frontend check. The editor counts
  `[...text].length`, and a test covers an astral-character anchor rather
  than only asserting the threshold value.
- **`evals/run.py`** pins §3's policy through `grade_prompt_section`,
  alongside the budget, reply-format, roll-protocol, active-speaker and
  available-art sections. Note what that grader actually buys, because this
  line previously overstated it: it renders the template and requires the
  result in the assembled prompt, so both sides move together and it cannot
  catch a **reword**. What it catches is the section ceasing to be
  DELIVERED — emptied, switched off, dropped, or its feeding variable broken.
  Attached to the several-NPCs fixture rather than required suite-wide,
  because the policy renders conditionally and a global check would reject
  the single-bare-NPC prompts it is designed not to clutter.
- **`scripts/verify_templates.py`** must agree with the new templates.
- **`CLAUDE.md`** gains a line recording that the anchor is now a prompt
  input and not only a judge input.

## Testing

- **`cast_blocks` construction**: one entry per present NPC and **none
  filtered**; nameless entries keep their description and lose their voice
  blocks; a whitespace-only name counts as nameless and does not raise
  `named_npc_count`.
- **Disambiguation terminates and is unique among non-empty names**: a
  confusable pair gets `#1` and `#2` (both members); a card literally named
  `Winifred #2` sharing a scene with two `Winifred`s ends with three distinct
  names; **two nameless entries are legal and both keep `""`**.
- **Render conditions read the same filtered set as the blocks**: one
  nameless NPC with an anchor, alone, renders no voice section at all — not a
  policy block with nothing under it.
- **The day-one rule survives filtering**: two named NPCs, no anchors, no
  `mes_example` — `voice_policy` still renders. This is the regression an
  entry-count check reintroduces.
- **Policy reaches an examples-only scene**: one named NPC with a
  `mes_example` and no anchor — `voice_policy` renders.
- **Headings are independent**: each section renders its own heading with the
  others disabled, reordered, or separated by another section.
- **Caps**: an over-cap value truncates at the latest boundary in the capped
  prefix, cutting *before* a chosen `<START>`; a single-line over-cap example
  hard-cuts mid-line; **an example whose only boundary is a leading `<START>`
  hard-cuts rather than truncating to empty**.
- **One effective anchor**: an over-cap anchor is capped identically for the
  prompt and for the judge, so a rule past the cap is enforced against
  neither.
- **The judge sees a valid standing correction**, and the two prompt halves
  agree. Written around the contradiction pair (anchor forbids contractions,
  correction requires them), and it must assert **both directions**:
  positively, that the assembled prompt states the correction supersedes a
  conflicting anchor line; and negatively, that `system.j2` no longer defines
  drift against the anchor *absolutely* — the phrases "the anchor rules out"
  and "consistent with the anchor" must be gone. A positive-only assertion
  passes an implementation that bolts a precedence sentence onto the existing
  absolute wording, which is the contradictory evaluator this fix exists to
  prevent.
- **A stale correction never reaches the judge**: a flag fingerprinted to a
  replaced anchor is omitted from the drift prompt, exactly as
  `_voice_notes` omits it from the scene prompt. Same fixture, two consumers,
  one expectation.
- **No correction, no user-message change**: a character without a valid
  flag produces the same drift *user message* as today, so the extended
  contract costs nothing in the ordinary case. The system message differs by
  design, so the assertion is scoped to the user half — asserting the whole
  prompt is unchanged is unsatisfiable and would have to be deleted later.
- **Tiering**: `voice_policy` survives a budget that drops both other
  sections. The examples-before-anchors ordering is asserted **only** for a
  manufactured case where the examples section is genuinely larger — it is a
  tendency of the packer's largest-first rule, not a guarantee, and the test
  must not read as one.
- **Layout migration**: a stored `message_examples` entry becomes
  `voice_examples` **preserving `enabled: false` and position**; the other
  two enter as newcomers after `character_descriptions`, including when that
  predecessor is disabled or repositioned.
- **`list_characters`**: `has_voice_anchor` per §5's table; exactly one added
  read per character.
- **`GET /config`** carries `VOICE_ANCHOR_CAP` and the editor's warning
  threshold matches it.
- **Frozen campaign**: `snapshot.json` **will** move — `sweep.py:197-198`
  captures `context.build_messages` and the inspector's section rows, and
  this removes a section id and adds three. The new text must be read and
  reviewed before committing; a blind regenerate is what the fixture exists
  to prevent. `home/` is never regenerated.
