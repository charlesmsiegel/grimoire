"""Where each user-facing Wave 9 first slice (073-087) is on screen, or why it is not yet. ``time.coverage``.

Each row is ``{slice, title, status, route, where, reason}`` and, for a page whose address is this campaign's or
this chat's, ``campaign`` or ``chat``:

- ``here``: on the Time page itself.
- ``screen``: on the page ``route`` registers; ``campaign: True`` links ``#/<route>/<campaign id>?chat=...`` in the
  same view, ``chat: True`` links ``#/<route>/<ChatRef>`` (the story page's address takes the chat), and neither
  links the route alone (``chats``, for something done in a chat).
- ``unavailable``: said plainly with ``reason``, never offered as a button.

A later part of Sprint 116 that builds one of these screens flips its row to ``screen`` with its route (and the
page-registration test then checks that the route is a registered page). docs/TIME.md keeps the same table with
each row's integration test and its failure behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext

LATER = "a later part of this sprint"

FEATURES: tuple[dict, ...] = (
    {"slice": "073", "title": "Hypothetical reply", "status": "screen", "route": "chats", "where":
     "in a chat: the composer's Imagine, a reply tried out and never posted", "reason": None},
    {"slice": "074", "title": "Downtime", "status": "here", "route": "time", "where": "this page", "reason": None},
    {"slice": "075", "title": "Recorded rolls", "status": "screen", "route": "rules", "campaign": True, "where":
     "Rules, for this campaign: a check reviewed, then rolled and recorded, a player's roll backed by their own words",
     "reason": None},
    {"slice": "076", "title": "Offscreen faction plans", "status": "here", "route": "time", "where": "this page",
     "reason": None},
    {"slice": "077", "title": "Quests and commitments", "status": "here", "route": "time", "where": "this page",
     "reason": None},
    {"slice": "078", "title": "Relationships and attitudes", "status": "unavailable", "route": None, "where": None,
     "reason": f"its place (a chat's Details) is built in {LATER}"},
    {"slice": "079", "title": "Source upgrades", "status": "unavailable", "route": None, "where": None,
     "reason": f"its screen (Authoring) is built in {LATER}"},
    {"slice": "080", "title": "Speech to draft", "status": "screen", "route": "chats", "where":
     "in a chat: Dictate in the composer, a transcript that is only ever a draft", "reason": None},
    {"slice": "081", "title": "Story threads", "status": "screen", "route": "story", "chat": True, "where":
     "Story threads, for this chat: a thread of the story followed to how it ended", "reason": None},
    {"slice": "082", "title": "Read-only world audits", "status": "unavailable", "route": None, "where": None,
     "reason": f"its screen (Authoring) is built in {LATER}"},
    {"slice": "083", "title": "Character voice performance", "status": "unavailable", "route": None, "where": None,
     "reason": f"its place (in a chat) is built in {LATER}"},
    {"slice": "084", "title": "Cross-campaign scene transfer", "status": "unavailable", "route": None, "where": None,
     "reason": f"its place (a chat's Details) is built in {LATER}"},
    {"slice": "085", "title": "Live context preview", "status": "screen", "route": "providers", "where":
     "Models and costs, in Settings: one NPC's current context and a draft previewed against it (the composer's "
     f"own entry point is built in {LATER})", "reason": None},
    {"slice": "086", "title": "Mechanics module authoring", "status": "screen", "route": "rules", "where":
     "Rules, in the rail under Library: a rules module drafted, checked, saved and published as reviewed",
     "reason": None},
    {"slice": "087", "title": "World authoring proposals", "status": "unavailable", "route": None, "where": None,
     "reason": f"its screen (Authoring) is built in {LATER}"},
)


def coverage(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    return {"features": [dict(item) for item in FEATURES]}


OPS = {"coverage": coverage}
