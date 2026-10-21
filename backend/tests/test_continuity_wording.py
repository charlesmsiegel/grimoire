"""Relation descriptions used in journal output."""

from grimoire.store.continuity import effective, review


def test_relation_words_cover_every_relation():
    assert set(review.RELATION_WORDS) == set(effective.RELATIONS)
    for relation, words in review.RELATION_WORDS.items():
        assert "_" not in words, relation
    for relation in ("before", "on", "after", "by"):
        assert review.RELATION_WORDS[relation] == "is due " + relation
    assert review._relation_words("same_as") == "is linked to"
