"""Sprint 116's Rules page ops: mechanics module authoring (086) and a campaign's recorded rolls (075). Called as
``rules.<op>``; see docs/RULES_PAGE.md.

Only Sprint 116 edits this package. Each op is ``handler(ctx, args) -> dict`` over an existing domain package (see
``grimoire.app.adapter``); nothing here keeps a record of its own.

- ``modules`` (086): the library's mechanics modules. ``authoring`` (templates and what is listable), ``module``
  (one id's draft and published revisions), ``template`` (a starting text), ``check`` (every problem, by path),
  ``save_plan``/``save`` (a draft's text, reviewed then written against its etag), ``publish_plan``/``publish`` (a
  draft with no problem, as the revision reviewed), ``revision`` (one published revision, read-only), ``sheet`` and
  ``grade`` (try a revision: fill a sheet, grade a check from entered dice; never saved).
- ``rolls`` (075): ``rolls`` (a campaign's recorded rolls as this view is shown them), ``roll_plan`` (the review:
  what would be rolled, under which attempt; nothing drawn) and ``roll`` (drawn once and recorded, or read back).
  ``configure`` gives a library a scripted draw (tests), before the app serves it.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter
from grimoire.chats.codec import ChatCodecError

from .modules import OPS as MODULE_OPS
from .rolls import OPS as ROLL_OPS
from .rolls import configure

ADAPTER = Adapter("rules", owner="116", ops={**MODULE_OPS, **ROLL_OPS},
                  # a chat record this version cannot read is refused with a reason, never a server error
                  refusals={ChatCodecError: "chat_unreadable"})

__all__ = ["ADAPTER", "configure"]
