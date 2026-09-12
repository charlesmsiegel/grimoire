"""Passage evidence is quoted text, never model-authored dialogue."""
import pytest

from grimoire.store import campaigns, characters, overlay, passage_evidence, worlds


def test_quotes_belong_to_named_speaker_only():
    passage = 'Mara said, "Wait here." Winifred said, "Come along."'
    assert passage_evidence.quotes(passage, "Mara") == ["Wait here."]
    assert passage_evidence.quotes(passage, "Winifred") == ["Come along."]


def test_no_speech_or_ambiguous_attribution_has_no_examples():
    assert passage_evidence.examples('Mara watched the door.', 'Mara') == ''
    assert passage_evidence.examples('Mara and Winifred heard "Go."', 'Mara') == ''
    assert passage_evidence.quotes('“Wait,” said Mara. “Go,” said Winifred.', 'Mara') == ['Wait,']


def test_review_cannot_add_invented_examples():
    with pytest.raises(ValueError, match='quoted'):
        passage_evidence.validate('Mara', 'Mara said, "Wait."', 'Mara said, "Wait."', 'invented')


def test_attachment_is_local_preserves_existing_and_records_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv('GRIMOIRE_HOME', str(tmp_path))
    wid = worlds.create_world('Realm')
    wroot = worlds.world_root(wid)
    card = characters.blank_card('Mara')
    card['data']['description'] = 'An existing fact.'
    aid, vid = characters.create_character(wroot, 'Mara', card=card)
    cid = campaigns.create_campaign('Saltmarch', wid)
    source = 'Mara said, "Wait."'
    result = passage_evidence.save(cid, 'scene-1', 'response-1', name='Mara',
        description='Watches the door.', passage=source, source_text=source,
        mes_example=passage_evidence.examples(source, 'Mara'), existing_ref='characters:' + aid)
    assert result['character'] == aid
    assert characters.read_card(wroot, aid, vid) == card
    saved = characters.read_card(overlay.char_root(cid, aid), aid, vid)['data']
    assert saved['description'] == 'An existing fact.\n\nWatches the door.'
    evidence = saved['extensions']['grimoire']['passage_evidence'][0]
    assert evidence['response_id'] == 'response-1'
    assert evidence['source_text'] == source
    assert len(overlay.list_characters(cid)) == 1


def test_comma_in_previous_quote_does_not_give_it_to_the_next_speaker():
    passage = 'Mara said, "Wait," Winifred said, "Go."'
    assert passage_evidence.quotes(passage, "Winifred") == ["Go."]


def test_addressee_before_colon_is_not_the_speaker():
    assert passage_evidence.quotes('Winifred spoke to Mara: "Go."', 'Mara') == []
    assert passage_evidence.quotes('Mara: "Wait."\nWinifred: "Go."', 'Mara') == ['Wait.']
