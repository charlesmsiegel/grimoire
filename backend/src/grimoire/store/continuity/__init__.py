"""Continuity: reviewed semantics over the existing narrative ledgers.

`continuity.json` records what a reader has decided about plot threads and
commitments -- that one is a duplicate of another (an alias), how two relate (a
link), which findings not to raise again (a suppression) -- without becoming a
second copy of any of them. plot.json and commitments.json stay the source of
truth; everything here is a projection over them or a decision about them.

Deliberately empty of imports. `scene_refs` and `undo` both have to reach the
storage module, and the readers built on top of it reach `plot`, `events` and
the chronicle; a package ``__init__`` that imported its own submodules would put
every one of those on the path of every importer and close a cycle the import
guard forbids. Importers name the submodule they want:
``from .continuity import doc``, ``from ..store.continuity import effective``.

- ``doc`` -- continuity.json IO and its primitive mutators (the only writer).
- ``candidates`` -- the derived candidate cache (`continuity_candidates.json`);
  rebuildable, never journalled.
- ``pending`` -- what a cached finding means now: its current fingerprint and
  verdict, defined once for the review read, Todo, apply and the persists;
  read-only.
- ``canon`` -- ref grammar, alias-graph resolution, link/candidate ids and
  fingerprints.
- ``effective`` -- the live resolver, the effective records/links every
  current-state reader uses, and the prompt-snippet renders.
- ``involvement`` -- which actors and scenes a record has touched.
- ``review`` -- validated, journalled alias and link writes.
- ``pressure`` -- temporal pressure: one dated item list over events, holidays,
  birthdays and deadlines; read-only, and never imported by ``clock``.
- ``drivers`` -- scene drivers and date anchors composed from ``pressure``,
  ``effective`` and ``involvement``; read-only.
- ``similarity`` -- identity texts and the lexical, structural and embedding
  signals that rank possible duplicates; scores rank, they never write.
- ``identity`` -- which absorb rows would open a new thread or commitment, and
  the stored same-type records each might already be; read-only.
"""
