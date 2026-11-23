"""Sprint 116's Time adapter: a campaign's long activities (074), offscreen faction plans (076) and quests and
promises (077), the director's, on campaign time beside 055's clock; and where every Wave 9 slice is on screen.
Called as ``time.<op>``; see docs/TIME.md.

Only Sprint 116 edits this package. Each op is ``handler(ctx, args) -> dict`` over the existing domain service
(``DowntimeLibrary``, ``OffscreenLibrary``, ``CommitmentLibrary``, all over ``campaigns/<id>/scope.json``), built
over the app's open library with the owner's trusted handle; nothing here keeps a record of its own. All three are
director-facing canon, so every op but ``coverage`` refuses ``director_only`` unless ``audience`` is ``director``,
and the table never reads any of it.

- ``overview`` reads the clock, the chosen chat's cast and each domain's section (one refused section answers its
  refusal and leaves the others). Like every op it runs inside the app's one serialized call, under the library's
  writer lock, so nothing writes between its reads; each section still answers the etag it read, and the page
  reviews a domain's change against that section's own etag.
- Each write is a pair: ``<domain>_plan`` builds the domain's change against the etag the page last read and answers
  what it would do and the files it would write, writing nothing; ``<domain>_commit`` builds the same change again
  (the same client operation id, the same expected etag) and commits it. Anything that changed the scope in between
  makes the commit stale (``changed``); the same commit retried finds the domain's own receipt and writes nothing
  again (``already_applied``); an id reused for another request is the domain's ``operation_mismatch``.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.calendarstore import CalendarLibrary
from grimoire.core import DIRECTOR

from . import commitments, common, coverage, downtime, offscreen
from .coverage import FEATURES


def _overview(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    common.director(args)
    scope = common.campaign(args)
    clock = CalendarLibrary(ctx.store, ctx.handle).read(scope)
    found = common.chat(args, scope, required=False)
    scene = None if found is None else common.scene_of(ctx, found)
    names = common.Names(ctx, scene)
    controls = common.controls_of(scene, scope.campaign_id)
    cast = [] if scene is None else [{**names.who(actor), "controller": scene.controller(actor),
                                      "role": "unseated" if scene.controller(actor) is None
                                      else "npc" if scene.controller(actor) == DIRECTOR else "player",
                                      "downtime": downtime.whose(ctx, scope.campaign_id, actor, controls)}
                                     for actor in scene.cast]
    return {"campaign": scope.campaign_id, "chat": None if found is None else str(found), "etag": clock.etag,
            "clock": common.clock_view(clock.state), "cast": cast, "owner": ctx.owner,
            "downtime": common.section(lambda: downtime.view(ctx, scope, names, scene)),
            "offscreen": common.section(lambda: offscreen.view(ctx, scope, found, names)),
            "commitments": common.section(lambda: commitments.view(ctx, scope, found, scene, names))}


_OPS = {"overview": _overview, **downtime.OPS, **offscreen.OPS, **commitments.OPS}

ADAPTER = Adapter("time", owner="116", ops={**{name: common.guarded(op) for name, op in _OPS.items()},
                                           **coverage.OPS},
                  refusals={kind: "time_refused" for kind in common.DOMAIN_ERRORS})

__all__ = ["ADAPTER", "FEATURES"]
