"""The decide-era plumbing every decision item goes through: the structured
decision messages and the tool loop's finalize turn."""

from __future__ import annotations

from grimoire import decisions as dec
from grimoire import inference

from .harness import REPORT, check_messages, render

_DECIDE_ITEMS = [
    dec.Item("Mara closes the door behind her.\nThe lamp gutters.", (
        dec.Predicate("over", "Is the scene over?"),
        dec.Choice("next", "Who speaks next?",
                   (dec.Option("seraphine", "Seraphine, at the window"),
                    dec.Option("mara", "Mara", aliases=("Mara Vale",))),
                   allow_none=True),
        dec.Score("tone", "How tense is the room?", ("calm", "uneasy", "tense")))),
    dec.Item("Winifred counts the stalls of Saltmarch.", (
        dec.Choice("verdict", "Has Winifred's voice drifted?",
                   (dec.Option("drift", "Drifted from the anchor"),
                    dec.Option("in_voice", "In voice"))),)),
]
#: 01e's kinds, each with the system-prompt bullet that explains it. A bullet
#: renders only beside a question of its kind, so `_DECIDE_ITEMS` (today's
#: call sites' kinds) must carry none of them -- their prompts stay
#: byte-identical -- and `_DECIDE_NEW_ITEMS` must carry its own.
_DECIDE_BULLETS = {
    "rank": "- a ranking is answered with a list of candidate ids, best first",
    "select": "- a selection is answered with a list of option ids",
    "joint": "- a joint choice is answered with the id of one of its listed pairs",
}
_DECIDE_NEW_ITEMS = [
    dec.Item("Mara asks the harbourmaster about the Saltmarch ledger.", (
        dec.Rank("relevant", "Which scenes matter most to this turn?",
                 (dec.Option("scene:ledger", "Mara loses the ledger at the pier"),
                  dec.Option("scene:market", "Winifred counts the stalls"),
                  dec.Option("scene:storm", "Seraphine waits out the storm")),
                 top=2, allow_none=True, pointwise="Does this scene bear on the ledger?"),
        dec.Predicate("over", "Is the scene over?"))),
    dec.Item("Seraphine palms the harbour key while Mara watches.", (
        dec.MultiSelect("saw", "Who saw it happen?",
                        (dec.Option("characters:mara", "Mara"),
                         dec.Option("characters:winifred", "Winifred")),
                        min=1, max=1, allow_none=True),
        dec.MultiSelect("helped", "Who helped?", (dec.Option("characters:mara", "Mara"),),
                        max=0),
        dec.Joint("act", "What does Winifred do?",
                  (dec.Option("strike", "Strike"), dec.Option("wait", "Wait")),
                  (("strike", (dec.Option("characters:seraphine", "Seraphine"),
                               dec.Option("creatures:gull", "A gull"))),),
                  allow_none=True))),
]

def _decide_case(set_name, items, explain, label) -> None:
    """One item set, rationale setting and count against direct renders of
    `decide/`, and each 01e bullet present exactly beside its own kind."""
    messages = inference.structured_messages(items, explain=explain)
    check_messages(f"{set_name} ({label})",
                   [{"role": "system",
                     "content": render("decide/system.j2",
                                       schema=dec.schema(items, explain=bool(explain)),
                                       explain=bool(explain),
                                       kinds=dec.kinds(items))},
                    {"role": "user",
                     "content": render("decide/user.j2", items=items, explain=explain)}],
                   messages)
    for kind, bullet in _DECIDE_BULLETS.items():
        asked = kind in dec.kinds(items)
        REPORT.require(f"{set_name} ({label}): the {kind} bullet only beside a {kind}",
                       (bullet in messages[0]["content"]) == asked,
                       f"the {kind} bullet is {'missing' if asked else 'present'}")


def check_decide() -> None:
    # Structured decisions (slice F, spec 7.4): `inference.structured_messages`
    # against direct renders of `decide/`. Every branch the templates take: the
    # rationale on and off; a predicate, a choice with and without `allow_none`, and
    # a score; one item and two -- so a branch that moved cannot hide behind one
    # that did not.
    for set_name, items_all in (("decide", _DECIDE_ITEMS), ("decide 01e", _DECIDE_NEW_ITEMS)):
        for explain in ("", "Say in one sentence what settled it."):
            for n in (1, 2):
                items = items_all[:n]
                if len(items) < n:
                    continue
                _decide_case(set_name, items, explain, f"{n} item(s), explain={bool(explain)}")


def check_finalize() -> None:
    # The tool loop's finalize turn (01g-S4): `inference.finalize_message` against
    # a direct render of `tools/finalize.j2`, both branches of each var.
    final_schema = {"type": "object", "properties": {"keeper": {"type": "string"}},
                    "required": ["keeper"], "additionalProperties": False}
    for schema in (None, final_schema):
        for stopped in (False, True):
            check_messages(f"tool loop finalize (schema={schema is not None}, stopped={stopped})",
                           [{"role": "user", "content": render("tools/finalize.j2", schema=schema,
                                                               stopped=stopped)}],
                           [inference.finalize_message(schema, stopped=stopped)])


CHECKS = (check_decide, check_finalize)
