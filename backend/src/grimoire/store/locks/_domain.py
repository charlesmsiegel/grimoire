"""The declared lock domain and its documented frozen legacy backlog."""

from __future__ import annotations

# --- the lock domain, declared ----------------------------------------------
#
# Enforced by `tests/test_lock_domain_guard.py`, which walks the package's ASTs
# and works out for itself which modules mutate campaign-scoped state and which
# of those serialize. Every such module must appear in exactly one of the three
# names below, so a newly-written mutator cannot land outside the exclusion
# without somebody saying so in code -- the #255 failure, where `rolls` ran a
# private lock registry and no test could tell.
#
# The unit is a module rather than a function because that is the granularity a
# reader reasons about ("are appearances serialized?"), and because a module's
# private helpers run under whatever their callers hold. A member module may
# still exempt one function with a `# lock-domain-ok: <reason>` comment; the
# guard caps how many of those exist.

# The unit is the module the mutators actually live in, so splitting a module
# into a package re-keys its entry onto the submodules that inherited its
# functions. That is a rename, not a reclassification: the guard re-derives the
# verdict from the code either way, and every function each old entry covered is
# still covered by the entries that replaced it.

#: Modules whose public campaign-scoped mutators all take `campaign_lock(cid)`.
DOMAIN_MODULES: frozenset[str] = frozenset({
    # Presentation writes preserve the same campaign boundary as their posts.
    "store.scenes.post_identity",
    "store.scene_history.records",
    "store.expression_library.write",
    "store.expression_library.lifecycle",
    "store.expression_library.character_pack",
    # #73 settings validate and replace campaign.md, stamping only a real
    # change while holding the same lock as every other campaign writer.
    "store.campaign_settings",
    # Art append/reuse and guarded mutations resolve and publish campaign
    # placements under the same campaign hold as their overlay metadata.
    "store.record_art.write",
    # A campaign's standing prompt is its own whole-text setting, not an
    # overlay record. Validate its scope and publish under the same lock as
    # other campaign settings; the wid-only writer remains world-scoped.
    "store.default_prompts",
    # Reroll alternates read the transcript, decide against it, and write a
    # sidecar beside it -- and `promote` then drives two `scenes` mutators. All
    # of that has to be one critical section with the scene writes it brackets,
    # or a concurrent reply lands between the decision and the swap.
    "store.alternates",
    # The campaign and scene field layers (`tracker.json`, `tracker/<identity>/
    # fields.json`) sit beside `campaign.md` and are rewritten whole, and the
    # scene one is validated against the campaign's effective list -- so the read
    # of that list and the write have to be one hold, or a concurrent campaign
    # layer edit lands between them.
    "store.tracker.fields",
    # `write_campaign` reads the world's, global and connection rule ids the
    # campaign's file must not collide with (or switch off something that is
    # not there), validates against them and writes `regex.json` -- one hold, or
    # a concurrent campaign write lands between the check and the file.
    "store.regex.layers",
    # The store phase's record of what it rewrote: one whole file per scene,
    # read-modify-written beside the transcript write it describes and inside
    # that write's hold -- two unserialized landings would lose one record.
    "store.regex.rewrites",
    # The tracker's per-scene records: a snapshot file per post plus an
    # `index.json` that is read-modify-written whole on every status or flag
    # change -- two unserialized updates landing at once would lose one entry.
    # Held across the snapshot-then-index pair so a reader never sees the index
    # ahead of the file it describes. `store.tracker.walk` is deliberately NOT
    # listed: it writes only through these mutators, so this guard does not
    # count it as mutating, and its writers (`flag_edited`, `flag_after`,
    # `prune`) take the same reentrant lock around deciding which keys to touch
    # from the transcript and the write itself -- or a cut or a swipe lands
    # between the two and the flags go to keys that are no longer there.
    # `store.absorb.publish` is unlisted for the same reason: it writes only
    # through `sync.promote`, and takes `campaign_lock(cid)` itself anyway.
    "store.tracker.records",
    # The steering log is a read-modify-write of one whole file, appended
    # beside `alternates.archive` inside the regenerate route's lock hold
    # (reentrant, so its own acquire is free) — two unserialized rerolls
    # would lose one of the two appends.
    "store.steering",
    # `provenance.record` is a read-modify-write of one whole file, so two
    # unserialized callers lose one of the two writes. Its only caller
    # (`absorb.apply.apply_edits`, under `PUT /chronicle`) already holds this
    # lock, and the lock is an RLock, so taking it inside `record` costs a
    # reentrant acquire and buys the module a place in the domain rather than
    # another entry on `UNREVIEWED`'s frozen backlog.
    "store.provenance",
    # The `store.scenes` submodules that mutate. Each public mutator in
    # them either wears the package's `@locking._serialized` -- `campaign_lock`
    # around the whole body -- or delegates to a private one that does, which is
    # how `create_scene` and `set_datetime` resolve a calendar before the lock.
    "store.scenes.write",
    "store.scenes.moment",
    "store.scenes.lifecycle",
    "store.scenes.focus",
    # `attempts` records whether a send's post is still in the transcript, and
    # every one of its mutators is called from inside a campaign-lock hold that
    # brackets the scene write it describes -- `remember` beside the append,
    # `forget` inside the rollback that removes it. That bracketing is the
    # point: written outside the hold there is a window where the transcript
    # and the record disagree, and a recovery landing in it discards the only
    # surviving copy of what the player typed.
    "store.attempts",
    "store.responses",
    "store.response_snapshots",
    "store.appearances.transitions",
    # `ensure_identity` is a read-modify-write of a whole scene file like every
    # other mutator in the package, and wears the same `@locking._serialized`.
    # Two unserialized callers would each mint an identity and one would win,
    # handing the loser a token that no longer matches what is on disk -- which
    # is the publish fence failing open in exactly the case it exists for.
    "store.scenes.identity",
    # The end-of-scene review held between generating it and saving it (#396).
    # Every mutator takes the lock, and the bracketing is the point rather than
    # a formality: `publish` has to land inside the same hold as the run's
    # cancellation check (or a Cancel is undone by the run it cancelled), and
    # `merge` has to read, fold and write inside one (or two retries answering
    # at once lose one phase). New module, so it starts inside the exclusion
    # rather than joining the frozen `UNREVIEWED` backlog.
    "store.pending_reviews",
    # `quick_replies.json` (the campaign's quick-reply set) is rewritten whole
    # from a set the client read, so the digest check that refuses a stale
    # `expect` and the write have to be one hold -- or two tabs' override and
    # hide edits each pass the check and one is lost. New module, so it starts
    # inside the exclusion rather than joining the frozen `UNREVIEWED` backlog.
    "store.quick_replies",
    # `knowledge.json` (roadmap 11 overrides) is rewritten whole by every
    # mutator, and the ledger routes validate a subject against the scene and
    # write it in one hold -- two unserialized writes would lose one statement,
    # and a delete hook landing between a check and a write could resurrect a
    # deleted actor's grant. New module, so it starts inside the exclusion.
    "store.knowledge",
    # `commitments.json` is rewritten whole by `set_movement` and
    # `repoint_scenes`, exactly like `plot.json` -- but this module is new
    # (#115), so it starts inside the exclusion rather than joining the
    # `UNREVIEWED` backlog `plot` sits in.
    "store.commitments",
    # continuity.json (the capstone's aliases, links and suppressions) is
    # rewritten whole by every mutator in `continuity.doc`, so two unlocked
    # read-modify-writes lose one decision. New module, so it starts inside the
    # exclusion rather than joining the frozen `UNREVIEWED` backlog.
    "store.continuity.doc",
    # continuity_candidates.json (the reconcile sweep's derived findings) is
    # rewritten whole by `write` and `drop` in `continuity.candidates`, so two
    # unlocked read-modify-writes lose one -- a dropped finding comes back, or
    # a sweep's persist is undone. New module, so it starts inside the
    # exclusion rather than joining the frozen `UNREVIEWED` backlog.
    "store.continuity.candidates",
    # The campaign's cover image (`<campaign>/assets/cover.<ext>`). A new
    # module mutating campaign-scoped state, so it starts inside the exclusion
    # rather than joining the frozen `UNREVIEWED` backlog: `put_cover` and
    # `delete_cover` take the lock around the publish-then-clean sequence, and
    # `delete_cover` verifies the removal under it.
    "store.covers",
    # The campaign's own image library (`<campaign>/assets/images/`), the same
    # shape as the cover at a higher cardinality (#376). `put_image` and
    # `delete_image` take the lock around the publish-then-clean sequence, and
    # `delete_image` verifies the removal under it. New module, so it starts
    # inside the exclusion rather than joining the frozen `UNREVIEWED` backlog.
    "store.campaign_images",
    # `relationship_history.json` is rewritten whole by every append, and the
    # append that loses the race loses the only account of a standing that has
    # already changed -- `relationships.json` keeps the new value and nothing
    # left says what it replaced, which is the loss this store exists to stop.
    # New module (#63), so it starts inside the exclusion rather than joining
    # the frozen `UNREVIEWED` backlog.
    "store.relationship_history",
    # `journal.json` is rewritten whole by every append, and an append that
    # loses the race loses the only record of a write that already landed --
    # which is worse than a stale panel, because the reversal it carries is the
    # only way back. New module (#31), so it starts inside the exclusion rather
    # than joining the frozen `UNREVIEWED` backlog `changes` sits in.
    "store.journal",
    # Host membership lease RMWs take the same campaign domain (spec07).
    "store.group_proposals",
    # `clock.json` is rewritten whole by every advance -- the moment and the log
    # row that explains it land in one write, so two unserialized advances can
    # leave the clock at a moment whose reason was never recorded. New module
    # (#100), so it starts inside the exclusion rather than joining the frozen
    # `UNREVIEWED` backlog. Only the write is inside: resolving the campaign's
    # calendar provider and computing the digest run that provider's
    # (user-authored) code, which must not run under this lock -- the same cut
    # `scenes.moment.set_datetime` and `scenes.lifecycle._date_hint` make.
    "store.clock",
    # events.json is rewritten whole by every create, edit and fire, and a lost
    # write here is not a stale panel: `fire` stamps that the clock reached a
    # scheduled event, and losing that stamp means the same event fires again on
    # the next advance that re-crosses its day. New module (#101), so it starts
    # inside the exclusion rather than joining the frozen `UNREVIEWED` backlog.
    # As in `clock`, only the read-modify-write is inside -- normalizing a date
    # runs the campaign's (possibly user-authored) calendar provider, which must
    # not happen under this lock.
    "store.events",
    # notices.json is rewritten whole by `mark` and `forget`, and a lost write
    # here is a warning the reader dismissed coming back -- or, on the `forget`
    # side, one they asked for staying gone. `mark` also evicts past
    # `LEDGER_LIMIT` inside the same read-modify-write, so two unlocked callers
    # can drop a row neither of them was writing. New module (#106), so it
    # starts inside the exclusion rather than joining the frozen `UNREVIEWED`
    # backlog. Only the read-modify-write is inside: `pending` resolves the
    # campaign's (possibly user-authored) calendar provider and enumerates a
    # window of its holidays, which must not run under this lock -- the same cut
    # `clock` and `events` make.
    "store.notices",
    # `facts.json` the same (#114), and with one reason of its own on top of
    # the whole-file rewrite: `record` retires the superseded fact and writes
    # its replacement in one read-modify-write, so an unlocked pair can also
    # leave a fact retired by an id that never landed.
    "store.facts",
    # `prompts/index.json` is read-modify-written by every capture (allocate an
    # id, append a row, evict past the retention depth), so two concurrent turns
    # would otherwise lose one row and leak its payload. New module (#157), so
    # it starts inside the exclusion rather than joining the `UNREVIEWED`
    # backlog -- the same call `store.commitments` made.
    "store.prompt_log",
    # scene_ideas.json is rewritten whole by `add`, `set_status` and
    # `repoint_scenes`, exactly like facts.json -- and `add` allocates the
    # idea's id from the keys it just read, so two unlocked saves can pick the
    # same slug and one of the two ideas is simply gone. New module (#88), so
    # it starts inside the exclusion rather than joining the `UNREVIEWED`
    # backlog -- the same call `store.commitments` and `store.facts` made.
    "store.scene_ideas",
    # pins.json the same, and with a consequence of its own on top of the
    # whole-file rewrite: the file holds the reader's manual overrides of what
    # reaches the model, so a lost write is not a stale panel but a pin the
    # reader set, watched land, and then finds the next turn ignoring. New
    # module (#129), so it starts inside the exclusion rather than joining the
    # frozen `UNREVIEWED` backlog.
    "store.pins",
    # authors_notes.json the same: rewritten whole by every save, so two
    # unlocked saves lose one note -- and a lost write here is a steer the
    # player set and then finds the next turn ignoring, the reason `pins`
    # gives. Reads stay lock-free: they sit on every compose path, the
    # opener's included, which runs in a worker where a lock wait would
    # hold the turn being built.
    # New module (play controls V), so it starts inside the exclusion rather
    # than joining the frozen `UNREVIEWED` backlog.
    "store.authors_notes",
    # Forking copies one campaign's whole directory into another and, for a
    # retrospective fork, cuts the copy back to an earlier scene. It is the
    # only mutator in the package whose critical section spans TWO campaigns,
    # so it is also the only one outside `module_edit` and the world-module
    # rebind route that goes through `hold_all` -- the source's lock so the
    # copy is not taken across somebody else's write, the fork's so nobody
    # sees the branch between `copytree` publishing `campaign.md` and the cut
    # finishing. New module (#72), so it starts inside the exclusion rather
    # than joining the frozen `UNREVIEWED` backlog.
    "store.fork",
    # Branching a scene builds a sibling out of a transcript copy and one copy
    # helper per record module, then cuts it -- all inside one campaign-lock
    # hold, so a turn cannot append to the source mid-copy and nobody sees the
    # sibling between its file landing and its records following. New module
    # (play controls III), so it starts inside the exclusion.
    "store.branch",
    "store.sheets.tally",
    "store.sheets.writer",
    "store.audit.baselines",
    "store.proposals",
    "store.rolls",
    # `played.json` is read-modify-written by `mark_greeting` (scans every
    # scene for one stamping `gid` before clearing an orphaned mark) and by
    # `start_from_greeting`'s recheck-and-mark. Both were unlocked until
    # #318 made a played greeting unavailable and made a replay raise --
    # which is what turned "two racers can both pass the guard" from a
    # relabeling into a duplicate play, and what turned the clearing rule's
    # scan into a real TOCTOU against a start mid-flight. `start_from_
    # greeting` deliberately does NOT hold this lock across `context_
    # macros.expand_macros`, which resolves the campaign's calendar
    # provider and runs its (user-authored) code -- same reason `scenes.
    # lifecycle._date_hint` resolves its calendar before `_create_scene`'s
    # lock rather than inside it.
    "store.playing",
    # `replay.json` holds a retcon replay's backlog -- the ONLY copy of the
    # posts its cut removed -- and every step is a read-modify-write of that
    # file PAIRED with a transcript write: stage appends the originals it then
    # marks staged, accept reads the transcript's length to record the mark,
    # cancel cuts and re-appends under the same hold. Splitting any of those
    # pairs is how a session comes to describe a transcript that is not there.
    # New module (#79), so it starts inside the exclusion rather than joining
    # the frozen `UNREVIEWED` backlog.
    "store.replay",
    # A campaign's subject tags live on the shared image objects under
    # `campaign:<cid>`, and `strip_campaign` / `copy_campaign` sweep every
    # object to remove or copy that scope through `image_store.update` -- one
    # hold across the sweep, so a tag written in the campaign cannot land
    # between a delete's strip and the tree going, or a fork's copy. The
    # unlocked `strip`/`copy` take no `cid` and run under their callers'
    # holds. New module, so it starts inside the exclusion.
    "store.image_scopes",
})

#: Modules deliberately outside the exclusion, with the reason. An entry here is
#: a decision someone made and can defend, not merely the status quo.
OUTSIDE_DOMAIN: dict[str, str] = {
    "store.image_subjects": (
        "copy_to_character takes a character id as cid and a root, not a "
        "campaign identity. It is a root-level image-reference primitive; "
        "campaign callers own campaign exclusion. Recognizing assets.link_in "
        "as publication makes this existing character-id writer visible to "
        "the guard's cid approximation, without making cid a campaign id."
    ),
    # One `store.campaigns` entry until the module became a package; the reason
    # below is that entry's, split across the three submodules its mutators
    # landed in. Nothing was reassessed and nothing changed hands.
    "store.campaigns.lifecycle": (
        "Known gap, not a considered exclusion -- recorded here so it stops "
        "being invisible. `rename_campaign`, `set_campaign_response` and "
        "`set_campaign_budget` each read-modify-write the campaign meta file, "
        "so two concurrent ones lose "
        "an edit (a rename dropped by a `touch` that read the older "
        "frontmatter -- see `store.campaigns.read`). `delete_campaign` is the "
        "exception: it moves the tree aside, strips the image scope and "
        "removes the tree under one hold of the campaign lock "
        "(`worlds.staging.remove_published`), so a failed strip can put the "
        "tree back without a holder's write in the way. "
        "`ensure_campaign_slim` now holds the campaign lock from its meta "
        "read through tombstones, manifest reservations, pruning and the "
        "`_finish` meta rewrite (spec 07). Its earlier hold covered only the "
        "two tombstone call sites; strict identity preflight must precede "
        "every associated effect in the same hold. Lazy migration can still "
        "answer `CampaignBusy`. The ordinary frontmatter writers above stay "
        "outside the domain; fixing those is a separate concurrency change, "
        "which is why this guard classifies "
        "them rather than closing them. `set_campaign_routing` (#142) writes "
        "the same file and does NOT join them: it takes the lock, as does "
        "`set_campaign_tracker` (the scene tracker's switch). Inheriting a "
        "known gap and adding to it knowingly are not the same thing, and this "
        "module stays out here for the mutators above rather than for that one."
    ),
    "store.campaigns.read": (
        "`touch` read-modify-writes the campaign meta file unlocked, so it can "
        "drop a concurrent `rename_campaign`, `set_campaign_response` or "
        "`set_campaign_budget` by "
        "publishing frontmatter it read before that edit landed. It runs from "
        "`appearances` on every actor change (`transitions.appear`, "
        "`versions.pick_version`, `versions.import_version`), which is what "
        "makes the overlap reachable. Same known gap as "
        "`store.campaigns.lifecycle`, whose mutators it races."
    ),
    "store.revision": (
        "A considered exclusion, not a gap. `bump` publishes a freshly minted "
        "token and reads nothing first, so there is no update for a second "
        "writer to lose: two concurrent bumps leave one of two tokens on disk "
        "and either is a correct answer, since the only property the value "
        "carries is `different from what an earlier reader holds`. Taking the "
        "campaign lock would buy nothing and cost the thing that matters -- "
        "this fires from the activity middleware after every campaign-scoped "
        "mutating request, so it would queue behind whatever holds that "
        "campaign (a minutes-long absorb, a turn) to write a value that was "
        "already correct before the wait. A counter would need the lock; that "
        "is one of the reasons the token is not a counter (`store/revision.py`)."
    ),
    "store.usage": (
        "A considered exclusion, not a gap. `repoint_scenes` is the only public "
        "cid-taking mutator here, and it does not read-modify-write anything: "
        "each rename is a single `O_APPEND` line (`atomic.append_line`), so "
        "concurrent callers interleave rows instead of losing them -- the same "
        "reasoning `record` already documents for the generating path it runs "
        "on. Taking the campaign lock would buy nothing and cost two things "
        "that matter: it would stall a turn behind whatever else holds that "
        "campaign, and this ledger is home-scoped, so most of its writes belong "
        "to no campaign at all (a tagline, a connection test) and have no lock "
        "to take. The file is append-only by design -- nothing here ever "
        "rewrites a row -- which is what makes the unlocked write safe rather "
        "than merely convenient."
    ),
    "store.usage_rollup": (
        "A considered exclusion, not a gap. Nothing here is a source: the file "
        "is a byte bookmark into the ledger, derived from it and deletable at "
        "any moment, so a lost update costs one reader a rescan and can lose no "
        "record of anything. Every write is a COMPLETE snapshot through "
        "`atomic.write_text`, so the loser of a concurrent write is replaced by "
        "a snapshot that is equally true rather than by half of one. `forget` "
        "takes a cid but mutates no campaign-scoped state -- it drops a cached "
        "figure ABOUT a campaign that has just been deleted. And the lock is "
        "the wrong instrument in the other direction too: this is read from the "
        "rail on every navigation, so taking it would queue a badge behind a "
        "minutes-long absorb, which is the cost the whole module exists to "
        "avoid paying."
    ),
    "store.inference.migrate": (
        "A considered exclusion, not a gap: the caller holds the lock. "
        "`campaign` migrates one campaign's `campaign.md` to the new layout and "
        "is the step two holders compose -- `ensure`, one campaign at a time "
        "under `campaign_lock_nowait(cid)` (a busy campaign is skipped and "
        "finished later), and the first new-layout write to an unmarked "
        "campaign, inside the hold that covers that write (spec 11.1). Taking "
        "the lock itself would let it run without the hold that makes "
        "migrate-then-write one step, so it refuses with RuntimeError unless "
        "`locks.holds_campaign(cid)` -- a check this guard cannot read, which "
        "is why the module is declared here rather than in DOMAIN_MODULES."
    ),
    "store.inference.retire": (
        "A considered exclusion, not a gap, for `store.inference.migrate`'s "
        "reason: the caller holds the lock. `retire_campaign` retires one "
        "campaign's `campaign.md` (an unmarked one migrated in the same write) "
        "and is run by `migrate.ensure`'s retirement pass, one campaign at a "
        "time under `campaign_lock_nowait(cid)` taken by the caller -- a busy "
        "campaign is left for the next start. It refuses with RuntimeError "
        "unless `locks.holds_campaign(cid)`, a check this guard cannot read, "
        "which is why the module is declared here rather than in "
        "DOMAIN_MODULES."
    ),
    "store.campaigns.paths": (
        "`write_manifest` republishes the whole campaign manifest from a dict "
        "its callers read a moment earlier -- `overlay`, `sync`, `migrations` "
        "and `appearances.versions` all read-modify-write it that way -- so "
        "two concurrent callers lose one of the two edits. It was inside the "
        "single `store.campaigns` entry before the split, covered by that "
        "module-level declaration without being named in its prose; it is "
        "named here rather than promoted, because nothing about it was "
        "reviewed."
    ),
}

#: Modules that mutate campaign-scoped state without serializing and have never
#: been assessed for lost-update risk. Not an endorsement -- a frozen backlog.
#: The guard forbids it from GROWING, so this list can only shrink as modules
#: are examined and moved into one of the two above. Anything new mutating
#: campaign state fails the guard until it is classified.
#: Two entries are re-keyed rather than new (see the note above DOMAIN_MODULES):
#: `store.appearances` became a package and its mutators landed in `paths`,
#: `transitions` and `versions`; `store.modules` became a package and
#: `set_campaign_module` landed in `binding`. Same functions, same absence of a
#: review -- the backlog did not grow, it was spelled out at the granularity the
#: code now has.
UNREVIEWED: frozenset[str] = frozenset({
    "store.appearances.paths",         # was store.appearances
    "store.appearances.versions",      # was store.appearances
    "store.assets",
    "store.campaign_climate",
    "store.changes",
    "store.characters",
    "store.chronicle",
    "store.commits",
    "store.dossiers",
    "store.modules.binding",           # was store.modules
    "store.overlay",                 # identity-ledger RMW/lifecycle locked; remaining writers unreviewed
    "store.playstate",
    "store.plot",
    "store.relationships",
    "store.sync",                    # advance/publication locked; remaining writers unreviewed
    "store.taglines",
    "store.weather.overrides",
})
