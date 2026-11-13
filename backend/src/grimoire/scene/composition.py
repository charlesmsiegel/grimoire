"""Validation of combined-turn fields without changing legacy positional turn fields.

The model supplies its canonical Post type and text/error validators to avoid a model import cycle.
"""


def validate_composition(record, *, post_type, fail, text) -> None:
    object.__setattr__(record, "extra_posts", tuple(record.extra_posts))
    if len(record.extra_posts) > 15 or any(type(p) is not post_type for p in record.extra_posts):
        raise fail("invalid_field", "at most sixteen valid posts per turn")
    if record.extra_posts and record.post is None:
        raise fail("invalid_field", "additional posts require a primary post")
    if any(p.actor.campaign_id != record.chat.campaign_id for p in record.extra_posts):
        raise fail("invalid_field", "additional posts belong to this campaign")
    if record.extra_posts and (record.labels or record.effects):
        raise fail("combined_adjudication_unsupported", "combined turns cannot carry adjudication or effects")
    if record.acceptance_id is not None:
        text(record.acceptance_id, "acceptance_id", limit=100)
        if (type(record.acceptance_epoch) is not int or record.acceptance_epoch < 0
                or type(record.acceptance_digest) is not str or len(record.acceptance_digest) != 64):
            raise fail("invalid_field", "acceptance requires epoch and digest")
    elif record.acceptance_epoch is not None or record.acceptance_digest is not None:
        raise fail("invalid_field", "acceptance metadata requires an id")
