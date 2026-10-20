# Greetings and plot map

Source: [greetings.py](../../../../../backend/src/grimoire/store/greetings.py).

### 5. Greetings and the plot map

A greeting is a scene opener. Whether a campaign can start it is decided by
`greetings.availability`, and it is startable when **all** of these hold:

- every tag in `requires_tags` is one the player character carries;
- its predecessors (the greetings whose `leads_to` names it) have been played: all
  of them under `predecessor_join: "all"`, at least one under `"any"`;
- no played greeting `excludes` it, and it excludes nothing already played;
- it hasn't been played already.

So the classic authoring bug is a World where **no greeting is startable by a fresh
campaign**: every opener gated on a tag, or every one downstream of another. Make
sure at least one opening needs no tag and has no predecessor, unless every player
character the World offers carries the tag it needs.

Add `leads_to` only where a later scene really depends on an earlier one. A wrong
edge silently forces a story order nobody wanted. `excludes` is for branches: taking
one path closes the other. The plot map must be acyclic (a cycle means none of its
greetings can ever start), and both `apply` and `check` refuse one.

`pcless: true` is an offscreen opener, an NPC-only scene with no player character.
The scene picker offers it only to an offscreen scene, so it is never an opening for a
player character: a World whose only startable greetings are offscreen gives its PCs
nothing to begin with.
`phase`, `sequence` and `optional` only order the "what next" recommendations
(`greetings.recommendations`) and never change what is startable.

The body keeps `{{user}}` for the player character. `{{char}}` is replaced with the
greeting's own character's name when the greeting is written, so use it or write
the name out, whichever reads better.
