# Entities, tags, greetings, graph and voices

## Step 5: Groups, locations, items

Take these from the description *and* the greetings — greetings are usually where a setting's
furniture actually appears, with a name and a reason to exist.

The bar is load-bearing, not merely mentioned. A recurring workplace, a signature possession,
a site with a job attached: yes. A shop named once in passing: no, unless a cast entry already
leans on it.

Prefer one rich group entry naming the whole cell and what each member does over one thin
entry per faction — the group entry is where a reader learns how these people relate. And
extend existing entries the new material touches: if a card reveals that an established
organisation ran a second programme, that belongs in the organisation's entry.

## Step 6: Tags

Read the tag vocabulary. If every new greeting presupposes the player character is something
the vocabulary has no word for, add one tag and gate the greetings on it. Don't add tags for
things a single greeting mentions — this is a player-character vocabulary, not a keyword
index.

## Step 7: Greetings

One greeting per `first_mes` and per entry in `alternate_greetings`. The body is the card's
text **verbatim** — keep `{{user}}`, keep the character's voice, keep the typos inside
dialogue. Fix only extraction artifacts: stray backslashes, doubled whitespace, `\r`.

Titles are yours. Cards routinely label every intro the same generic thing; the World names
greetings for what happens in them. Use the card's own scenario blurb to understand the scene,
then title it the way the World's existing greetings are titled.

## Step 8: Plot-map chains

Add a chain only where a later greeting explicitly refers back to something an earlier one
established — "the one assigned to my mentorship", "after last night", "rookie lesson number
one". Two greetings featuring the same character is not a chain.

Be conservative on purpose: a wrong edge silently forces a story order the text doesn't
support, and nobody notices for months.

## Step 9: Voice anchors

Every new character needs one — `voice_anchors.anchorless_ids(root, ids)` tells you who is
missing. Follow the shape the World's existing anchors use; typically a few lines of speech
habits with one short quoted line from the card, chosen because it demonstrates a habit rather
than because it is memorable.

Aim at what someone would need to *reproduce* the voice — sentence length, what they open
with, what they refuse to say, where an accent surfaces — not at their personality, which the
cast entry already covers.
