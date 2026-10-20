# Ledger ownership

Source: [facts.py](../../../../../backend/src/grimoire/store/facts.py), [knowledge.py](../../../../../backend/src/grimoire/store/knowledge.py), [ledger/](../../../../../backend/src/grimoire/routes/ledger/).

- **Grimoire never edits a fact; the user may.** `store/facts.py` is an
  append-with-lifecycle ledger, and the absorb pass's whole vocabulary for a
  fact that stopped being true is `record`, `retire` and supersession — a pass
  that rewrote a row in place would turn the ledger into `state.md` with extra
  keys and lose the supersession history it exists to keep. That was written
  as "facts are not edited", which was always a rule about the WRITER: a
  mistyped fact is not a fact that stopped being true, and retiring it would
  put a correction into the history as though the fiction had changed. So
  `facts.set_text` and `facts.forget` are the user's, reached only from
  `routes/ledger/`, and `test_absorb_writer_guard.py` fails if anything
  under `store/absorb/` calls either. That guard resolves import bindings
  rather than comparing spellings — an alias walked past three earlier
  versions of it.
- **Knowledge overrides are the user's word, and only theirs.**
  `store/knowledge.py` (roadmap 11) holds who knows, suspects or never heard
  what happened in a scene, addressed by scene identity and fingerprinted
  content; a grant stops matching when its text changes, an `unaware` never
  does. Its writers are the ledger routes, the actor and group delete hooks,
  undo and the branch copy -- `test_knowledge_writer_guard.py` fails anything
  else, absorb and continuity included -- and a grant is only stored about
  text the client was shown (`expect`, 409 `moved`). Scene reads for it live
  in `store/knowledge_scene.py`, because `overlay` imports `knowledge`.
- **Hand edits to the ledger go through `routes/ledger/`**, which takes the
  campaign lock the absorb save already holds across its whole sequence and
  wraps every write in `store.undo.journalled` so it lands in the journal as
  `manual` and can be reversed from the play view's Changes panel. Two of the
  ledger's seven sections are deliberately not editable: the relationship
  history and the change log record what happened, and editing a log falsifies
  history rather than correcting state.
