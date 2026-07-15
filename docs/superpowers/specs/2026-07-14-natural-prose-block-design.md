# Natural-prose block — anti-AI-ism defaults in every scene prompt

## Problem

Scene narration drifts into recognizable AI-isms: a fixed pool of invented
names (the "Elara" problem), stock phrases ("voice barely above a whisper"),
tic constructions ("not X, but Y", rhetorical question-then-answer, reflexive
rule of three), and a narrow set of physical beats (leaned, nodded, murmured).
Nothing in the prompt corpus pushes back on any of this today.

## Sources

Two inputs, both included in full (merged, deduplicated, tiered):

1. **The "AI-isms of Writing-Bible"** (community doc) — 47 patterns with
   severity ratings: em dashes, "delve"/formal transitions, metaphor clichés
   ("tapestry of", "a testament to"), "not X but Y", rhetorical Q&A, rule of
   three, bullet points/headers in narrative, emphasis overload, dramatic
   fragments, telling-not-showing, redundant adjective pairs, romance clichés.
2. **Antislop (Paech et al., 2025, arXiv:2510.15061)** — measured
   over-representation vs human baselines across 67 models. Word list
   (Table 4): flickered/flicker/flickering, leaned, muttered, gaze, grinned,
   gestured, murmured, nodded, glint, hesitated, whispered, blinked, hummed,
   faintly, unreadable. Trigram list (Table 5): "voice barely (above a)
   whisper", "said, voice low", "air thick with scent", "took a deep breath",
   "smile playing on lips", "voice barely audible", "couldn't shake the
   feeling", "eyes never leaving", "casting long shadows", "something else
   entirely", "heart pounding in chest", "spreading across face", "couldn't
   help but feel", "one last time". Also: "Elara" at 85,513× human frequency;
   an appendix of "not X, but Y" surface variants.

The paper warns that prompt-level ban lists have limited efficacy and can
backfire (the pink-elephant problem). Mitigation here: keep the block compact,
tier it by severity, and pair every "avoid" with a positive direction. True
suppression (sampler/finetune) is out of scope — grimoire generates through
OpenRouter.

## Decisions (from brainstorming)

- **Placement**: an always-on scene section — every chat/retry/regenerate/
  director/opener turn gets it. Not per-style, not store-configurable.
- **Content**: all categories — names, stock phrases, constructions/rhythm,
  beat words. Both source sets included.
- **Names**: two tiers. Hard-avoid for the AI-default pool (Elara, Kael,
  Voss…). Soft "vary, don't ban" for over-rotated token-diversity names
  (Chen, Okonkwo…) — no origin is banned; the instruction is variety within
  and across origins.
- **Strictness**: defaults with an explicit precedence hierarchy (see below).
  Repetition and stock phrasing are absolute; rhythm/punctuation devices (em
  dashes, ellipses, fragments, italics) are "ration and vary", since styles
  like noir-detective and gothic-horror legitimately use them.

### Precedence hierarchy (adversarial-review finding)

One explicit ordering, stated in the block itself so models don't improvise:

1. **Response format and established facts** — the `**<Name>:**` script
   markers, `{{user}}`/`{{char}}`-resolved identities, and every name that
   already exists in the scene, cast, or world always win. The block must
   never cause a model to rename, avoid, or fail to attribute an existing
   character (the repo's own fixtures include an Elara Vane and a Winifred
   Vance).
2. **The prose style guide** — may override the *rhythm* tier (em dashes,
   ellipses, fragments, italics, sentence shape).
3. **Natural-prose defaults** — the phrase, construction, and
   name-at-invention rules hold regardless of style.

Corollaries baked into the block text: the names guidance applies **only at
the moment of inventing a new name**, never to reproducing existing ones; and
the "no section headers in narration" rule explicitly exempts the required
speaker markers, which are format, not prose.

## Design

### Wiring

New template `templates/scene/sections/natural_prose.j2` — var-less, always
renders. Included from `templates/scene/system.j2` immediately **after** the
`prose_style` include, so "the prose style above wins" reads literally in
context order. Jinja auto-reload makes the block live-editable like every
other prompt.

One backend change (adversarial-review finding): `context.py`'s `_SECTIONS`
list mirrors `system.j2` for the Context-inspector token breakdown; it gains
`("Natural prose", "scene/sections/natural_prose.j2", False)` in the same
position (after "Prose style"). Without it the inspector would silently
undercount every scene prompt.

`templates/README.md` gains a line documenting the section in the `scene/`
entry.

### Block text

The full content of `natural_prose.j2` (headed `# Natural prose`, matching
the sibling sections' `# Section name` convention):

```
# Natural prose

Defaults that keep the writing from sounding machine-generated. Precedence:
the reply format and established facts always win — never rename, avoid, or
misattribute anyone or anything that already has a name. The prose style
guide, when one is set, overrides the rhythm guidance below. Everything else
here holds regardless.

**Names — only when inventing someone or something new.** Names that
already exist in this scene, cast, or world are fixed; reproduce them
exactly, even if they appear below. When you do invent a name, make it fit
the setting and vary in sound and origin. Never reach for the stock AI
pool: Elara, Lyra, Kael, Aria, Seraphina, Selene, Thorne, Voss, Vance,
Blackwood, Ashford, or a tavern called The Gilded-or-Rusty Anything. Don't
solve variety by rotating the same few names either (a Chen, an Okonkwo, a
Kowalski) — vary within an origin, not just across origins.

**Phrases — never use.** A voice barely above a whisper / barely audible;
said in a low voice as a reflex tag; the air thick with (scent, tension,
anything); a smile playing on lips; eyes never leaving; couldn't help but;
couldn't shake the feeling; heart pounding or hammering in a chest or
against ribs; casting long shadows; something else entirely; spreading
across her face; one last time; a deep breath as filler; a testament to; a
tapestry, symphony, or dance of anything; ministrations; the ghost of a
smile; shivers down the spine; knuckles whitening; the smell of ozone; an
unreadable expression; lips swollen with kisses; foreheads pressed
together as the default tender gesture; delve; nestled; moreover,
furthermore, indeed, albeit.

**Beat words — ration.** Flickered, leaned, murmured, muttered, nodded,
gaze, grinned, gestured, glinted, hesitated, whispered, blinked, hummed,
smirked, faintly. Ordinary words, but they are your reflexes: not every
line of dialogue needs a lean, nod, or murmur. When a beat repeats, replace
it with something specific to this character and this moment, or cut it.

**Constructions — never use.** "Not X, but Y" in every disguise ("it
wasn't just X — it was Y", "she didn't X; she Y'd", "no longer X; now Y").
A rhetorical question you immediately answer ("The result? Chaos."). The
reflexive rule of three ("he stopped, stared, listened") — three-part lists
only when the content is genuinely three things. Redundant adjective pairs
("dark and brooding") — pick the stronger word. Explaining an emotion you
just showed ("...which surprised him, because..."). Metaphors that decorate
rather than clarify.

**Rhythm.** Em dashes, ellipses, italics, and one-word dramatic fragments
are seasoning, not structure — if the last paragraph used one, the next
doesn't. Vary sentence length and paragraph shape; let some moments pass
without a dramatic beat. In narration, write lists as prose, no bullet
points or headings — the required speaker markers are reply format, not
headings, and always stay.
```

### Testing & verification

- `backend/.venv/Scripts/python.exe scripts/verify_templates.py` — the
  wiring harness; a var-less section must not break the documented contract.
- New backend test: "Natural prose" appears in `context_sections()` output
  (and therefore in the context endpoint's token total), positioned after
  "Prose style".
- `backend/.venv/Scripts/python.exe -m pytest backend -q` — check no test
  pins the assembled system-message text; adjust any that do.
- Manual: render a scene prompt (or eyeball via an existing context test)
  and confirm the section lands after the prose style and before card
  system prompts.

### Rollout & efficacy (adversarial-review finding)

The review asked for a multi-model efficacy evaluation before making the
block always-on. Deliberately declined as disproportionate: grimoire is a
single-user app, "sounds less like AI" is a subjective per-user judgment,
and there is no eval harness to piggyback on. What the design provides
instead:

- **Trivial, live rollback**: the block is a plain template file with no
  vars. Emptying (or gutting) `natural_prose.j2` disables it instantly —
  `system.j2` drops empty sections and jinja auto-reloads — with the
  `_SECTIONS` entry harmlessly rendering empty. No code change to back out.
- **Visible cost**: the Context inspector shows the section and its exact
  token count on every scene (hence the `_SECTIONS` requirement above), so
  the recurring cost is never hidden.
- **In-use evaluation**: the acceptance judge is the user reading their own
  scenes, with retry/regenerate as the natural A/B. If the pink-elephant
  effect shows up in practice (banned phrases surfacing *more*), the fix is
  editing the live template — trim the ban list, keep the positive guidance.

## Out of scope / follow-ups

- Other LLM calls (`scene_suggestions`, `tagline`, `dossier`, `absorb`) —
  the names guidance could matter for suggestion text; revisit if suggested
  scenes keep proposing Elaras. The section is an include, so reuse is one
  line when wanted.
- Sampler-level suppression (logit bias, Antislop-style backtracking) — not
  available through OpenRouter chat completions.
- Per-model tailoring (the paper shows slop fingerprints cluster by model
  family) — one generic block is the right first cut.
