"""A scene's running summary, for its search document, only while intact
(roadmap 08, spec section 4.2).

The rolling summary is not invalidated when the transcript under it changes:
hiding a post or cutting into the covered prefix leaves the stored fields in
place, and the app decides intactness at read time (`store.rolling_intact`).
So a document may carry the stored summary only when that same test passes;
otherwise text the player hid, cut or replaced would reach the document, the
compiled cache, an embedding request and 09's prompt.

The test reads the transcript body, so it runs only for an UNABSORBED scene
with a non-empty summary, and it is memoized on everything it reads: the scene
file's stat signature, the rule files of every connection that produced a
message in it (`regex.layers.signature`), and the two small values computed
fresh on every call -- the scene's facts digest and the seated player's name
(a card read each), since those can move with no write to the scene file. An
open scene pays one parse per post that lands; a finished-but-unabsorbed scene
pays once per process.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import chronicle, readstats, rolling_intact, rolling_summary, statcache
from ..appearances import cast as appearances_cast
from ..campaigns import paths as campaigns_paths
from ..regex import layers as regex_layers
from ..regex import view as regex_view
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read

POOL_ENTRIES = scenes_read.POOL_ENTRIES
_POOL: dict = {}

#: The `readstats` counter of transcript parses this module makes, so a test
#: can see a warm call make none.
PARSES = "searchdocs.rolling.parse"


def _connections(cid: str, sid: str) -> tuple[str, ...]:
    readstats.bump(PARSES)
    messages = scenes_read.read_scene(cid, sid)["messages"]
    return tuple(sorted({c for c in map(regex_view.connection_of, messages) if c}))


@dataclass(frozen=True)
class Running:
    """The summary a document may carry ("" when it may not), and the rule
    files the test consulted to say so -- none when it stopped before reading
    them (no summary, or facts that already moved). A rule edit can stale the
    summary and so move the document, so `slices` records these as inputs."""
    text: str
    paths: tuple


def intact_rolling(cid: str, head) -> Running:
    """The head's rolling summary if, and only if, the app's own intactness test
    passes over the scene as it is now; else "". `head` is a `slices.Head` (its
    `sid`, `path` and parsed `meta`). Never raises for a scene, or a ledger it
    reads, that will not read: that is "" too."""
    stored = scenes_read.rolling_summary_fields(head.meta)
    if not stored["summary"]:
        return Running("", ())
    sid = head.sid
    try:
        facts = chronicle.scene_facts(cid, sid)
        label = appearances_cast.player_label(cid, sid)
        if rolling_summary.facts_digest(facts) != stored["facts"]:
            return Running("", ())   # the facts moved: stale, and no body read needed
        scene_sig = statcache.signature(head.path)
        conns = statcache.memo("searchdocs.rolling.conns", scene_sig,
                               lambda: _connections(cid, sid), pool=_POOL,
                               max_entries=POOL_ENTRIES)
        files = regex_layers.files(cid, conns) or ()
        rules = regex_layers.signature(cid, conns)

        def compute() -> str:
            readstats.bump(PARSES)
            scene = scenes_read.read_scene(cid, sid)
            return rolling_intact.view(cid, sid, scene, facts)["prior"]
        sig = None if scene_sig is None or rules is None else \
            scene_sig + rules + (("label", label), ("facts", stored["facts"]))
        return Running(statcache.memo("searchdocs.rolling.intact", sig, compute, pool=_POOL,
                                      max_entries=POOL_ENTRIES), files)
    except (scenes_paths.SceneNotFound, campaigns_paths.CampaignNotFound,
            OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        # A ledger the test reads (appearances.json) of the wrong shape fails
        # one attribute away: the summary cannot be vouched for, so it is not
        # carried.
        return Running("", ())
