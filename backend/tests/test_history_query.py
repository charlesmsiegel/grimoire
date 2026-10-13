"""Query safety: the viewed visible scan, live catalogs and protected names."""

from grimoire.store import campaigns, config, entities, pins, regex, scenes
from grimoire.store.history import query
from grimoire.store.scenes import serialize
from tests.history_fixtures import actor, location, played
from tests.history_fixtures import cid as cid


def test_hidden_and_director_posts_never_wake_terms(cid):
    sid = played(cid, text="Seraphine finds a silver ledger.")
    actor(cid, sid, "Seraphine")
    scenes.append_message(cid, sid, "assistant", "Winifred hides a velvet crown.")
    scenes.set_excluded(cid, sid, 1, True)
    scenes.append_message(
        cid, sid, "assistant", "Mara moves the brass compass.", speaker=serialize.DIRECTOR_SPEAKER
    )
    q = query.build(cid, sid, seed="amber seal")
    assert "seraphine" in q.terms
    assert "amber" in q.terms and "seal" in q.terms
    assert not {"winifred", "velvet", "crown", "mara", "brass", "compass"} & set(q.terms)
    assert "velvet" not in q.texts[0]
    assert q.turn_index == 2


def test_excluded_and_gm_only_names_are_removed_even_from_quoted_terms(cid):
    sid = played(cid, text='Mara asks Seraphine about "Saltmarch" and a silver ledger.')
    mara, _ = actor(cid, sid, "Mara")
    actor(cid, sid, "Seraphine")
    loc = location(cid, sid, secrecy="gm-only")
    pins.set_rule(cid, f"characters:{mara}", "exclude", sid=sid)
    q = query.build(cid, sid)
    assert "mara" not in q.terms and "saltmarch" not in q.terms
    assert f"characters:{mara}" not in q.subjects
    assert f"locations:{loc}" not in q.subjects
    assert "seraphine" in q.terms


def test_scan_depth_counts_visible_posts_before_clipping(cid):
    config.write_config(context_scan_depth="1")
    sid = played(cid, text="silver ledger")
    scenes.append_message(cid, sid, "assistant", "velvet crown")
    scenes.set_excluded(cid, sid, 1, True)
    q = query.build(cid, sid, seed="amber seal")
    assert q.texts == ("silver ledger\namber seal",)
    config.write_config(context_scan_depth="0")
    assert query.build(cid, sid).texts == ("",)


def test_names_match_whole_words_and_titles_resolve_weights(cid):
    sid = played(cid, text="Marauder holds Mara's silver ledger.")
    aid, _ = actor(cid, sid, "Mara")
    item = entities.create_entity(campaigns.campaign_root(cid), "items", "silver ledger")
    prepared = query.prepare(cid, sid)
    assert prepared.weights["mara"] == 2
    assert prepared.weights["silver ledger"] == 2
    assert f"items:{item}" in prepared.query.subjects
    assert f"characters:{aid}" in prepared.query.subjects
    scenes.edit_message(cid, sid, 0, "Marauder holds a lantern.")
    q = query.build(cid, sid)
    assert "mara" not in q.terms


def test_preview_does_not_mint_legacy_identity(cid):
    sid = played(cid)
    path = campaigns.campaign_root(cid) / "scenes" / (sid + ".md")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(line for line in text.split("\n") if not line.startswith("identity:")),
        encoding="utf-8",
    )
    before = path.read_bytes()
    query.build(cid, sid)
    assert path.read_bytes() == before


def test_query_terms_are_built_after_prompt_rules(cid):
    sid = played(cid, text="silver ledger")
    regex.layers.write_campaign(
        cid,
        {
            "rules": [
                {
                    "name": "Hide ledger",
                    "pattern": "silver ledger",
                    "replacement": "amber seal",
                    "applies": ["prompt"],
                    "targets": ["user", "model"],
                }
            ]
        },
    )
    q = query.build(cid, sid)
    assert q.texts == ("amber seal",)
    assert "amber" in q.terms and "seal" in q.terms
    assert "silver" not in q.terms and "ledger" not in q.terms


def test_protected_multiword_name_blocks_constituents_and_quoted_phrases(cid):
    entities.create_entity(
        campaigns.campaign_root(cid), "items", "Mara Winifred", secrecy="gm-only"
    )
    sid = played(
        cid,
        text='\uff2d\uff21\uff32\uff21 dust and Winifred entries beside "Mara ribbon" and amber seal.',
    )
    q = query.build(cid, sid)
    assert not {"mara", "winifred", "mara ribbon"}.intersection(q.terms)
    assert "amber" in q.terms and "seal" in q.terms


def test_private_archive_scan_keeps_legacy_notes_urls_and_depth_boundary(cid):
    config.write_config(context_scan_depth="1")
    sid = played(cid, text="Mara asks about a silver ledger.")
    scenes.append_message(cid, sid, "assistant", "Winifred is hidden.")
    scenes.set_excluded(cid, sid, 1, True)
    note = "Continue ![portrait](https://example.invalid/velvet/crown)"
    scenes.append_message(cid, sid, "assistant", note, speaker=serialize.DIRECTOR_SPEAKER)
    prepared = query.prepare(cid, sid, seed="amber seal")
    assert prepared.archive_scan_text == note + "\namber seal"
    assert prepared.scan_text == "Mara asks about a silver ledger.\namber seal"
    assert "velvet" not in prepared.query.terms
    assert "example.invalid" not in prepared.query.texts[0]
    config.write_config(context_scan_depth="0")
    assert query.prepare(cid, sid, seed="amber seal").archive_scan_text == "amber seal"
