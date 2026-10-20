# Revision ownership

Source: [revision.py](../../../../../backend/src/grimoire/store/revision.py), [main.py](../../../../../backend/src/grimoire/main.py).

- **A campaign carries a write token; a route owning its stamps says so.**
  `store/revision.py` gives each campaign an opaque value that changes whenever
  the app records a write to it — the primitive `POST /advance` compares an
  expected state against and `POST /fork` keys a repeat on (#409). It is
  stamped by default in one place, the activity middleware in `main.py`, so a
  route added tomorrow is covered without anyone remembering. What stamps for
  itself on top of that always has the same reason in a different shape — the
  response line is not the moment. `clock.advance` bumps inside the lock hold
  that covers its commit (a check the token has not moved under is not binding).
  Everything a *detached* run writes bumps where it writes, since the run
  outlives the response the middleware stamps — `routes/scenes._under_review_lock`
  for a review's terminal write, `_rolling_commit`, `_break_commit` and
  `_break_title_commit` for the follow-ups a landed turn schedules (#397), and `routes/streaming._turn_settled`
  at each of a turn's terminal points, which is not the same thing as "a post
  landed": a closed roll fence writes a proposal and no post at all. The
  continuity sweep's two persists (`reconcile.persist_found` and
  `persist_proposals`) stamp the same way, since the request that started the
  sweep (a 202, or End Scene's save) never waits for them; one that would leave
  the candidate cache as it was stamps nothing. A prompt
  capture (`routes/common._record_prompt`) bumps after its write and under the
  lock that covers it, because the one route reaching it while persisting
  nothing else is the greeting opener, which is `@computes_only` — and a token
  minted before the write is one a reader can hold while that write is still
  landing. A multi-campaign write reached from a
  world or module route stamps every campaign it wrote, inside its `hold_all` —
  nothing under `/api/campaigns/` runs for one, and `module_edit`'s sheet
  migration is the one part of a module edit that writes a campaign's *own*
  file rather than what it inherits. A write answered **non-2xx** stamps for
  itself too, since the middleware only sees success: `proposals.project` is
  reached by a recovery that heals a transcript and then answers 409, and a
  continuity review apply or dismiss that an I/O error stops after one of its
  writes landed answers 500 naming the parts that did (`partial_apply`,
  `partial_dismiss` in `routes/continuity.py`), and a thread, commitment or
  event delete whose continuity clean-up fails after the delete landed answers
  500 `partial_delete` (`routes/ledger.forget_or_partial`). A route
  that mutates a *different*
  campaign than the one in its path says so with `@leaves_campaign_unchanged`
  (`POST /fork`, whose source is never written to) — otherwise a fork would
  invalidate the very expectation the caller took it to protect. What the token
  cannot see, and why a mismatch is a re-price rather than an error, is in that
  module's docstring. `docs/reference/storage.md` introduces the locking boundaries.
  `PUT /campaigns/{cid}/settings` uses `@stamps_campaign_write` instead: its
  store writer stamps activity and revision inside the campaign lock only after
  publishing a changed setting, so an unchanged save moves neither. The marker
  delegates both success and exception stamping to that writer; it does not
  mean that the route only computes or writes to a different campaign.
