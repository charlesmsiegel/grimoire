"""047 production extraction using synthetic card libraries and provider responses."""
from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from generation_fixtures import Harness, say
from scene_fixtures import ANA, Table, card_library
from store_helpers import OWNER
from grimoire.core.inference import InferencePolicy
from grimoire.providers import RouteChoice
from grimoire.scene import SceneRefused
from grimoire.security import Grant, Permission, ResourcePath


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("continuity-built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, chat, _ = built
    target = tmp_path / "cards"
    shutil.copytree(folder, target)
    return target, chat


def test_enabled_extraction_requires_its_independent_runtime_before_billing(cards):
    folder, chat = cards
    with Table(folder, [say("A gull passes.")], policy=InferencePolicy({"critique": 1})) as table:
        table.runner.seat(chat, ANA)
        with pytest.raises(SceneRefused) as refused:
            table.runner.generate(chat, table.owner)
        assert refused.value.reason == "no_extractor"
        assert not table.harness.transport.sends


def setup_review(table, chat, text, *, domain="world_fact", subject=None, voiced_by=None, complete=True,
                 continuity=(), propose=True, take=1, step=None):
    from grimoire.narration_review import ExtractionRuntime
    record = table.runner.open(chat).pending
    line = f"{record.turn_id}.{take}.1"
    output = {"lines": [{"line": line, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "complete": complete, "continuity": list(continuity),
                         "authorship": [{"id": "a1", "domain": domain, "subject": subject,
                                          "span": [0, len(text)], "quote": text, "voiced_by": voiced_by}]}]}
    reply = say(json.dumps(output))
    selected = reply if step is None else step(reply) if callable(step) else step
    harness = Harness(selected if isinstance(selected, list) else [selected], approve=True)
    table.runner.extractor = ExtractionRuntime(harness.adapter, (RouteChoice("primary", "model-a"),),
                                               harness.handle, approvals=harness.approvals)
    if propose:
        table.authority.add_grant(Grant(f"extract-{take}", "app:extract", frozenset({Permission.PROPOSE}),
                                       ResourcePath.parse("campaigns"), OWNER))
    table.runner.extract_handle = table.authority.handle_for("app:extract", trusted=False)
    return harness


def start(table, chat):
    table.runner.seat(chat, ANA)
    return table.runner.post(chat, table.player(), '"What now?"')


def test_prose_is_independently_reviewed_metered_and_reopened(cards):
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        extracted = setup_review(table, chat, text)
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].text == text
        assert take.lines[0].review == "reviewed"
        assert [c.stage.value for c in take.calls] == ["npc_generation", "critique"]
        assert all(c.status == "accepted" and c.attempts for c in take.calls)
        assert len(extracted.transport.sends) == len(table.harness.transport.sends) == 1
        assert "A gull passes." in extracted.prompts()[0]
        assert "authorship" not in table.harness.prompts()[0]
        detail = table.runner.continuity_review(chat, table.owner, record.turn_id, take.take)
        assert detail["review_token"] == take.review_token
        table.runner.keep(chat, table.owner, take.take)
    with Table(folder) as table:
        kept = table.runner.open(chat).last.kept
        assert kept.lines[0].review == "reviewed" and kept.lines[0].text == text
        assert kept.calls == take.calls


@pytest.mark.parametrize("complete,step,expected", [(False, None, "unchecked"), (True, say("not JSON"), "failed")])
def test_incomplete_or_failed_review_needs_exact_owner_exception(cards, complete, step, expected):
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        setup_review(table, chat, text, complete=complete, step=step)
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == expected
        for kwargs, reason in [({}, "review_required"),
                               ({"turn": record.turn_id, "acknowledge_review": "0" * 64}, "review_required")]:
            with pytest.raises(SceneRefused) as error:
                table.runner.keep(chat, table.owner, take.take, **kwargs)
            assert error.value.reason == reason
        with pytest.raises(SceneRefused) as error:
            table.runner.keep(chat, table.player(), take.take, turn=record.turn_id,
                              acknowledge_review=take.review_token)
        assert error.value.reason == "not_owner"
        kept = table.runner.keep(chat, table.owner, take.take, turn=record.turn_id,
                                 acknowledge_review=take.review_token)
        assert kept.kept.lines[0].review == "exception"
        assert kept.kept.lines[0].text == text


def test_pc_authorship_and_mislabel_cannot_be_overridden(cards):
    folder, chat = cards
    text = "Anya agrees to pay."
    with Table(folder, [say(text), say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        scene = table.runner.open(chat)
        npc = next(actor for actor in scene.cast if actor != scene.persona)
        setup_review(table, chat, text, domain="commitment", subject=str(scene.persona))
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "blocked"
        denied = take.lines[0].review_detail.report()["authorship"]
        assert denied[0]["span"] == [0, len(text)]
        with pytest.raises(SceneRefused) as error:
            table.runner.keep(chat, table.owner, take.take, turn=record.turn_id,
                              acknowledge_review=take.review_token)
        assert error.value.reason == "authorship_blocked"
        setup_review(table, chat, text, domain="voluntary_speech", subject=str(npc),
                     voiced_by=str(scene.persona), take=2)
        second = table.runner.generate(chat, table.owner)
        assert second.lines[0].review == "blocked"
        report = table.runner.continuity_review(chat, table.owner, record.turn_id, 2)
        assert report["lines"][0]["detail"]["report"]["mislabels"]
        with pytest.raises(SceneRefused) as error:
            table.runner.keep_lines(chat, table.owner, [second.lines[0].line_id], turn=record.turn_id,
                                    acknowledge_reviews={"2": second.review_token})
        assert error.value.reason == "authorship_blocked"


def test_denied_proposal_publishes_accounting_only(cards):
    folder, chat = cards
    with Table(folder, [say("A gull passes.")], policy=InferencePolicy({"critique": 1})) as table:
        start(table, chat)
        setup_review(table, chat, "A gull passes.", propose=False)
        take = table.runner.generate(chat, table.owner)
        assert not take.lines
        assert take.calls[-1].status == "refused" and take.calls[-1].reason == "not_authorized"
        assert take.calls[-1].attempts


def staged(table, chat, *, door="locked", as_of=0):
    from grimoire.play.stages import StageBook
    from grimoire.staging import Area, Placement, Stage, Way
    scene = table.runner.open(chat)
    npc = next(actor for actor in scene.cast if actor != scene.persona)
    stage = Stage(chat.campaign_id, chat.chat_id, as_of, (Area("quay"), Area("room")),
                  (Way("door", ("quay", "room"), door),), scene.cast,
                  (Placement(npc, area="quay"), Placement(scene.persona, area="room")))
    if table.runner.stages is None:
        table.runner.stages = StageBook()
    table.runner.stages.put(chat, stage)
    return scene.persona, npc, stage


def test_grounded_report_and_snapshot_are_owner_only_and_keep_refuses_changed_facts(cards):
    folder, chat = cards
    text = "Mara sees Anya."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        persona, npc, stage = staged(table, chat)
        claim = {"id": "sees", "kind": "sees", "actor": str(npc), "target": str(persona),
                 "span": [0, len(text)], "quote": text}
        setup_review(table, chat, text, continuity=[claim])
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "needs_review"
        full = table.runner.continuity_review(chat, table.owner, record.turn_id, 1)
        finding = full["lines"][0]["detail"]["report"]["findings"][0]
        assert finding["classification"] == "contradiction" and finding["evidence"]
        assert finding["quote"] == text
        with pytest.raises(SceneRefused) as error:
            table.runner.continuity_review(chat, table.player(), record.turn_id, 1)
        assert error.value.reason == "not_owner"
        table.runner.stages.put(chat, stage.at(1))
        with pytest.raises(SceneRefused) as error:
            table.runner.keep(chat, table.owner, 1, turn=record.turn_id, acknowledge_review=take.review_token)
        assert error.value.reason == "review_stale"
        assert table.runner.open(chat).pending is not None


def test_mismatched_stage_is_unchecked_not_resequenced(cards):
    folder, chat = cards
    text = "Mara sees Anya."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        persona, npc, stage = staged(table, chat, as_of=7)
        claim = {"id": "sees", "kind": "sees", "actor": str(npc), "target": str(persona),
                 "span": [0, len(text)], "quote": text}
        setup_review(table, chat, text, continuity=[claim])
        take = table.runner.generate(chat, table.owner)
        report = table.runner.continuity_review(chat, table.owner, record.turn_id, 1)
        assert take.lines[0].review == "needs_review"
        assert ["stage", "stale_stage"] in report["lines"][0]["detail"]["report"]["unchecked"]
        assert table.runner.stages.latest(chat) == stage


def test_cancelled_extraction_retains_original_prose_and_booked_usage(cards):
    from grimoire.testing.providers import CancelAt
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        start(table, chat)
        setup_review(table, chat, text, step=lambda reply: CancelAt(1, reply))
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].text == text and take.lines[0].review == "failed"
        assert take.calls[-1].status == "cancelled" and take.calls[-1].attempts
        assert table.runner.open(chat).pending.takes[-1].calls == take.calls


def test_superseded_extraction_keeps_usage_without_publishing_lines(cards):
    from grimoire.testing.providers import Supersede
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        start(table, chat)
        harness = setup_review(table, chat, text, step=lambda reply: Supersede(1, reply))
        harness.transport.on_supersede = lambda: table.runner.fence.claim(str(chat))
        take = table.runner.generate(chat, table.owner)
        assert take.state == "stale" and not take.lines
        assert take.calls[-1].attempts
        assert table.runner.open(chat).pending.takes[-1].calls == take.calls


def test_changed_facts_during_extraction_do_not_erase_blocked_authorship(cards):
    from grimoire.testing.providers import Supersede
    folder, chat = cards
    text = "Anya agrees to pay."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        record = start(table, chat)
        persona, npc, stage = staged(table, chat)
        harness = setup_review(table, chat, text, domain="commitment", subject=str(persona),
                               step=lambda reply: Supersede(1, reply))
        harness.transport.on_supersede = lambda: table.runner.stages.put(chat, stage.at(1))
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "blocked"
        with pytest.raises(SceneRefused) as error:
            table.runner.keep(chat, table.owner, 1, turn=record.turn_id, acknowledge_review=take.review_token)
        assert error.value.reason == "authorship_blocked"


def test_exact_player_authored_words_use_only_the_trusted_contribution(cards):
    folder, chat = cards
    text = "I will help."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), f'"{text}"')
        persona = table.runner.open(chat).persona
        setup_review(table, chat, text, domain="voluntary_speech", subject=str(persona))
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "reviewed"


def test_partial_mixed_keep_cannot_bypass_review_or_use_another_takes_token(cards):
    from scene_fixtures import attach_tamsin
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)] * 4, policy=InferencePolicy({"critique": 1})) as table:
        attach_tamsin(table.store, table.owner, chat)
        record = start(table, chat)
        # The extractor intentionally omits the second line. Both lines must remain independently held.
        setup_review(table, chat, text, complete=False)
        first = table.runner.generate(chat, table.owner)
        setup_review(table, chat, text, complete=False, take=2)
        second = table.runner.generate(chat, table.owner)
        chosen = [first.lines[0].line_id, second.lines[1].line_id]
        for acknowledgments in ({}, {"1": second.review_token, "2": second.review_token}):
            with pytest.raises(SceneRefused) as error:
                table.runner.keep_lines(chat, table.owner, chosen, turn=record.turn_id,
                                        acknowledge_reviews=acknowledgments)
            assert error.value.reason == "review_required"
        kept = table.runner.keep_lines(chat, table.owner, chosen, turn=record.turn_id,
                                       acknowledge_reviews={"1": first.review_token, "2": second.review_token})
        assert all(line.review == "exception" for line in kept.kept.lines)
        assert len(kept.kept.lines) == 2
        assert not kept.kept.calls


def test_retry_accounting_is_kept_on_the_same_take_and_reopens(cards):
    from grimoire.testing.providers import Status
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1}, max_attempts_per_call=2)) as table:
        start(table, chat)
        setup_review(table, chat, text, step=lambda reply: [Status(503), reply])
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "reviewed"
        assert [a.outcome for a in take.calls[-1].attempts] == ["failed", "completed"]
        assert len({a.call_id for a in take.calls[-1].attempts}) == 1
    with Table(folder) as table:
        assert table.runner.open(chat).pending.takes[-1].calls == take.calls


def test_changed_snapshot_during_extraction_lands_a_stale_review(cards):
    from grimoire.testing.providers import Supersede
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        start(table, chat)
        _, _, stage = staged(table, chat)
        harness = setup_review(table, chat, text, step=lambda reply: Supersede(1, reply))
        harness.transport.on_supersede = lambda: table.runner.stages.put(chat, stage.at(1))
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "stale"
        assert take.lines[0].text == text and take.calls[-1].attempts


def test_two_lines_cannot_each_spend_the_same_remaining_item(cards):
    from grimoire.holdingstore import HoldingsLibrary
    from grimoire.holdings import Line, Operation
    from scene_fixtures import attach_tamsin
    folder, chat = cards
    text = "Mara drinks a draught."
    with Table(folder, [say(text), say(text)], policy=InferencePolicy({"critique": 1})) as table:
        attach_tamsin(table.store, table.owner, chat)
        start(table, chat)
        npc = table.runner.open(chat).npcs[0]
        holdings = HoldingsLibrary(table.store, table.owner, clock=lambda: "2026-11-13T00:00:00Z", owner=OWNER)
        holdings.apply(Operation("supply", (Line("add", npc, "draught", 1),))).commit()
        claim = {"id": "use1", "kind": "spends", "actor": str(npc), "item": "draught", "count": 1,
                 "span": [0, len(text)], "quote": text}
        def both(reply):
            value = json.loads(reply.deltas[0])
            second = json.loads(json.dumps(value["lines"][0]))
            second["line"] = second["line"].rsplit(".", 1)[0] + ".2"
            second["authorship"][0]["id"] = "a2"
            second["continuity"][0]["id"] = "use2"
            value["lines"].append(second)
            return say(json.dumps(value))
        setup_review(table, chat, text, continuity=[claim], step=both)
        take = table.runner.generate(chat, table.owner)
        assert [line.review for line in take.lines] == ["reviewed", "needs_review"]
        report = take.lines[1].review_detail.report()
        assert report["findings"][0]["reason"] == "insufficient"
        assert report["findings"][0]["span"] == [0, len(text)]
        assert holdings.read(npc).holdings.stacks["draught"] == 1


def test_damaged_facts_after_billing_preserve_attempts_and_hold_original_prose(cards, monkeypatch):
    from grimoire.testing.providers import Supersede
    folder, chat = cards
    text = "A gull passes."
    with Table(folder, [say(text)], policy=InferencePolicy({"critique": 1})) as table:
        start(table, chat)
        broken = []
        original = table.runner._knowledge
        def read(chat):
            if broken:
                raise SceneRefused("continuity_damaged", "fixture damage")
            return original(chat)
        monkeypatch.setattr(table.runner, "_knowledge", read)
        harness = setup_review(table, chat, text, step=lambda reply: Supersede(1, reply))
        harness.transport.on_supersede = lambda: broken.append(True)
        take = table.runner.generate(chat, table.owner)
        assert take.lines[0].review == "stale" and take.calls[-1].attempts
        broken.clear()
        assert table.runner.open(chat).pending.takes[-1].calls == take.calls


def test_combining_clean_takes_requires_owner_review_of_the_new_aggregate(cards):
    from grimoire.holdingstore import HoldingsLibrary
    from grimoire.holdings import Line, Operation
    from scene_fixtures import attach_tamsin
    folder, chat = cards
    drink, rain = "Mara drinks a draught.", "It rains."
    with Table(folder, [say(drink), say(rain), say(rain), say(drink)],
               policy=InferencePolicy({"critique": 1})) as table:
        attach_tamsin(table.store, table.owner, chat)
        record = start(table, chat)
        npc = table.runner.open(chat).npcs[0]
        holdings = HoldingsLibrary(table.store, table.owner, clock=lambda: "2026-11-13T00:00:00Z", owner=OWNER)
        holdings.apply(Operation("supply", (Line("add", npc, "draught", 1),))).commit()
        def two(second_text, spend_index):
            def reply(base):
                output = json.loads(base.deltas[0])
                second = json.loads(json.dumps(output["lines"][0]))
                second["line"] = second["line"].rsplit(".", 1)[0] + ".2"
                second["sha256"] = hashlib.sha256(second_text.encode()).hexdigest()
                second["authorship"][0].update(id="a2", quote=second_text, span=[0, len(second_text)])
                output["lines"].append(second)
                output["lines"][spend_index]["continuity"] = [{"id": "use", "kind": "spends", "actor": str(npc),
                    "item": "draught", "count": 1, "span": [0, len(drink)], "quote": drink}]
                return say(json.dumps(output))
            return reply
        setup_review(table, chat, drink, step=two(rain, 0))
        first = table.runner.generate(chat, table.owner)
        setup_review(table, chat, rain, step=two(drink, 1), take=2)
        second = table.runner.generate(chat, table.owner)
        assert all(line.review == "reviewed" for take in (first, second) for line in take.lines)
        chosen = [first.lines[0].line_id, second.lines[1].line_id]
        with pytest.raises(SceneRefused) as error:
            table.runner.keep_lines(chat, table.owner, chosen, turn=record.turn_id)
        assert error.value.reason == "review_required"
        kept = table.runner.keep_lines(chat, table.owner, chosen, turn=record.turn_id,
                                       acknowledge_reviews={"1": first.review_token, "2": second.review_token})
        assert all(line.review == "exception" for line in kept.kept.lines)
        assert all(line.review_detail.report()["reason"] == "aggregate_unchecked" for line in kept.kept.lines)
        assert holdings.read(npc).holdings.stacks["draught"] == 1


@pytest.mark.parametrize("value", [
    {"text": 'a"\\\n\t\b\f\r\x00\x1f\x7f'},
    {"text": "é水😀", "values": [None, True, False, 10, -1, 0.5]},
    {"empty": {}, "array": [], "nested": [{"x": "y"}]},
])
def test_review_size_matches_ascii_serialization_and_report_is_immutable(value):
    import json
    from grimoire.core.json_value import freeze_object
    from grimoire.scene.review import LineReview, _encoded_size

    frozen = freeze_object(value)
    assert _encoded_size(frozen) == len(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode())
    review = LineReview.make("prose", findings=[value])
    projection = review.report()
    projection["findings"].clear()
    assert review.report()["findings"] == [value]
