"""048 at the table: the runner's traces, and turn history read once per change without changing what is read.

Over the synthetic card library (``scene_fixtures``) with scripted provider replies. Bounded I/O is checked by
the counts a read reports (files read, decoded, reused); equivalence by comparing what a long-lived runner and a
fresh one send to the model over the same edited history.
"""

from __future__ import annotations

import shutil
import threading
from decimal import Decimal

import pytest

from generation_fixtures import Harness
from scene_bench import seed_history
from scene_fixtures import ANA, LABEL_POLICY, MARA_VOICE, POLICY, V1_CHAT, Table, card_library, say, v1_library
from store_helpers import OWNER

from grimoire.core.inference import CallAttempt, InferencePolicy, Measurement, TokenUsage, TurnUsageLedger
from grimoire.core.model import ControlAssignment
from grimoire.generation.plan import plan_generation_turn
from grimoire.play import SceneRunner
from grimoire.play.tracing import record_calls, traced
from grimoire.providers.meter import AttemptReport, TurnMeter
from grimoire.scene import SceneRefused
from grimoire.tracing import Price, PriceTable, TraceLog, call_costs


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def turns_dir(folder, chat):
    return folder / "campaigns" / chat.campaign_id / "turns"


def turn_file(folder, chat, number: int):
    return turns_dir(folder, chat) / f"{chat.chat_id}.{number:06d}.json"


def play(table: Table, chat, text: str = '"Where is the ship bound?"'):
    table.runner.post(chat, table.player(), text)
    take = table.runner.generate(chat, table.player())
    table.runner.keep(chat, table.player(), take.take)
    return take


# -- the runner's traces -----------------------------------------------------------------------------

def test_every_public_call_is_one_trace_with_its_work_as_spans(cards):
    folder, chat, _ = cards
    log = TraceLog()
    with Table(folder, [say("The tide turns.")], tracer=log.record) as table:
        table.runner.seat(chat, ANA)
        play(table, chat)
        table.runner.open(chat)
        table.runner.open(chat)
    assert [trace.operation for trace in log.traces] == ["seat", "post", "generate", "keep", "open", "open"]
    assert all(trace.status == "ok" for trace in log.traces)
    after_keep, opened = log.of("open")
    operations = {span.operation for span in opened.spans}
    assert {"read.library", "read.snapshot", "read.turns", "read.transcript", "read.control", "read.party",
            "stage.cast", "voices"} <= operations
    # the keep rewrote the turn, so the open after it decodes it once; the next open reuses it
    assert dict(after_keep.counts("read.turns")) == {"decoded": 1, "files": 1, "reused": 0, "turns": 1}
    assert dict(opened.counts("read.turns")) == {"decoded": 0, "files": 1, "reused": 1, "turns": 1}
    generated = log.of("generate")[0]
    operations = {span.operation for span in generated.spans}
    assert {"plan", "provider", "review", "brief.npc", "pack", "write.turn", "read.knowledge"} <= operations
    for trace in log.traces:  # no second is counted twice and none is lost
        assert sum(each.self_seconds for each in trace.breakdown()) == pytest.approx(trace.seconds)


def test_a_generation_trace_says_what_each_model_call_used_and_how_its_time_is_known(cards):
    folder, chat, _ = cards
    log = TraceLog()
    with Table(folder, [say("The tide turns.")], tracer=log.record) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), '"Well?"')
        table.runner.generate(chat, table.player())
    (generated,) = log.of("generate")
    (sample,) = generated.calls
    assert sample.stage == "npc_generation" and sample.actor_id == "npc-1"  # its place in the plan, no id
    assert sample.attempt.outcome == "completed" and sample.latency == "synthetic"  # a scripted clock, not real
    assert sample.total_s is not None and sample.attempt.usage.input_tokens is not None
    assert dict(generated.counts("provider")) == {"calls": 1}
    costs = call_costs(generated)
    assert costs.total.cost is None and costs.total.cost_state == "unpriced" and costs.total.latency == "synthetic"
    priced = call_costs(generated, PriceTable({sample.attempt.route: Price(Decimal(3), Decimal(15))}))
    assert priced.total.cost is not None and priced.total.cost > 0
    assert priced.total.cost_state == "estimated"  # a scripted transport's counts are no evidence of a bill


def test_a_labelling_trace_holds_its_one_call_and_both_turn_writes(cards):
    folder, chat, _ = cards
    log = TraceLog()
    reply = '{"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": ["She falls"]}'
    with Table(folder, policy=LABEL_POLICY, labels=[say(reply)], tracer=log.record) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), "*I climb the rigging*")
        label = table.runner.label(chat, table.player())
    assert label.state == "proposed"
    (labelled,) = log.of("label")
    assert labelled.status == "ok"
    (sample,) = labelled.calls
    assert sample.stage == "labelling" and sample.actor_id is None and sample.attempt.outcome == "completed"
    assert dict(labelled.counts("provider")) == {"calls": 1}
    assert [labelled.spans[i].operation for i in labelled.children(0)].count("write.turn") == 2  # running, then landed


def test_a_trace_names_no_actor_campaign_or_chat(tmp_path):
    folder = v1_library(tmp_path)  # imported actors whose instance ids are their characters' names
    log = TraceLog()
    with Table(folder, [say("Ledgers drown."), say("The tide keeps its hours.")], voices=MARA_VOICE,
               tracer=log.record) as table:
        table.runner.seat(V1_CHAT, ANA)
        scene = table.runner.open(V1_CHAT)
        table.runner.post(V1_CHAT, table.player(), '"Who is there?"')
        table.runner.generate(V1_CHAT, table.player())
    (generated,) = log.of("generate")
    assert sorted(sample.actor_id for sample in generated.calls) == ["npc-1", "npc-2"]
    text = repr(log.traces)
    for secret in [V1_CHAT.campaign_id, V1_CHAT.chat_id, *(actor.instance_id for actor in scene.cast),
                   *scene.names.values()]:
        assert secret not in text, secret


def test_a_clock_that_fails_loses_the_trace_never_the_call(cards):
    folder, chat, _ = cards
    log = TraceLog()
    ticks = iter([0.0, 1.0, 2.0])

    def running_out():  # a scripted clock that runs out partway through the call
        return next(ticks)

    with Table(folder, tracer=log.record, clock=running_out) as table:
        table.runner.seat(chat, ANA)  # the clock gives out inside this call: the seat still happens
        assert table.runner.open(chat).controller(table.runner.open(chat).persona) is not None
        table.runner.begin(chat, table.player())
        turn_file(folder, chat, 2).write_bytes(b"{not json")
        with pytest.raises(SceneRefused) as error:  # and a refusal is still the call's own
            table.runner.post(chat, table.player(), '"Hello."')
    assert error.value.reason == "turns_unreadable"
    assert log.traces == ()


class _Probe:
    """Just enough of a runner for the hook: a tracer and a clock."""

    def __init__(self, log: TraceLog) -> None:
        self.tracer = log.record
        self.clock = iter(float(n) for n in range(1000)).__next__

    @traced("generate")
    def run(self, meter: TurnMeter) -> None:
        record_calls(meter)


def test_a_sent_call_whose_booking_was_lost_is_still_in_the_trace():
    plan = plan_generation_turn("camp", "turn-1", present=["a", "b"], speakers=["a", "b"],
                                controls=[ControlAssignment("camp", actor, "director") for actor in ("a", "b")],
                                policy=InferencePolicy({}, max_attempts_per_call=1))
    failing = {"now": False}
    meter = TurnMeter(TurnUsageLedger(plan), _fail_booking=lambda stage: failing["now"])
    for index, call in enumerate(plan.calls):
        failing["now"] = index == 1  # the second call is sent, then both its bookings fail

        def build(call=call):
            done = CallAttempt(call.call_id, 1, TokenUsage(1000, 200), Measurement.PROVIDER_REPORTED,
                               "completed", "test/route")
            return done, AttemptReport(call.call_id, 1, "test/route", "completed", done.usage, done.measurement,
                                       total_s=1.0, latency_measurement="measured")

        meter._begin(call, "digest", False, 1)
        meter._book(call.call_id, 1, "test/route", sent=True, measurement=Measurement.PROVIDER_REPORTED,
                    error_kind=None, build=build)
        meter._end(call.call_id, "accepted")
    assert len(meter.poisoned_call_ids) == 1
    log = TraceLog()
    _Probe(log).run(meter)
    (trace,) = log.traces
    assert [sample.lost for sample in trace.calls] == [False, True]
    costs = call_costs(trace, PriceTable({"test/route": Price(Decimal(3), Decimal(15))}))
    assert costs.total.calls == 2 and costs.total.lost == 1
    assert costs.total.cost_state == "partial" and costs.total.latency == "partial"


def test_a_refusal_is_the_trace_status_and_reaches_the_caller_unchanged(cards):
    folder, chat, _ = cards
    log = TraceLog()
    with Table(folder, tracer=log.record) as table:
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        turn_file(folder, chat, 2).write_bytes(b"{not json")
        with pytest.raises(SceneRefused) as error:
            table.runner.post(chat, table.player(), '"Hello."')
    assert error.value.reason == "turns_unreadable"
    assert log.traces[-1].operation == "post" and log.traces[-1].status == "turns_unreadable"


def test_a_tracer_that_raises_loses_its_trace_never_the_result(cards):
    folder, chat, _ = cards

    def broken(trace):
        raise RuntimeError("an observer's bug")

    with Table(folder, tracer=broken) as table:
        table.runner.seat(chat, ANA)
        assert table.runner.open(chat).persona is not None


def test_tracing_changes_no_result_and_reads_the_clock_it_is_given(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        plain = table.runner.open(chat)
    log = TraceLog()
    with Table(folder, tracer=log.record, clock=lambda: 7.0) as table:
        traced = table.runner.open(chat)
    assert traced.records == plain.records and traced.problems == plain.problems and traced.cast == plain.cast
    (trace,) = log.traces
    assert all(span.seconds == 0.0 and span.started == 0.0 for span in trace.spans)  # a stopped clock


def test_concurrent_calls_each_build_their_own_trace(cards):
    folder, chat, _ = cards
    log = TraceLog()
    with Table(folder, tracer=log.record) as table:
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        errors = []

        def opener():
            try:
                for _ in range(5):
                    table.runner.open(chat)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        workers = [threading.Thread(target=opener) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    assert errors == []
    opened = log.of("open")
    assert len(opened) == 20
    for trace in opened:  # one root each, never another call's spans folded in
        assert sum(1 for span in trace.spans if span.parent is None) == 1
        assert sum(1 for span in trace.spans if span.operation == "read.turns") == 1


# -- turn history: read what changed, and only this chat's ------------------------------------------

def test_a_warm_open_decodes_only_the_turn_that_changed(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 20)
    with Table(folder, [say("The tide turns.")]) as table:
        cold = table.runner.scenes.turns(chat)
        assert (cold.reads.files, cold.reads.decoded, cold.reads.reused) == (20, 20, 0)
        assert table.runner.scenes.turns(chat).reads.decoded == 0
        table.runner.post(chat, table.player(), '"Where to?"')
        warm = table.runner.scenes.turns(chat)
        assert (warm.reads.files, warm.reads.decoded, warm.reads.reused) == (21, 1, 20)
        assert len(warm.turns) == 21 and warm.complete


def test_another_chats_turn_files_are_never_read_and_a_stray_name_counts_against_every_chat(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 3)
    other = turns_dir(folder, chat) / f"{chat.chat_id}-other.000001.json"  # another chat of the same campaign
    other.write_bytes(b"{damaged")
    with Table(folder) as table:
        found = table.runner.scenes.turns(chat)
        assert found.complete and found.reads.files == 3  # not read at all, so not a problem of this chat
        stray = turns_dir(folder, chat) / "stray.json"
        stray.write_bytes(b"{damaged")
        found = table.runner.scenes.turns(chat)
        assert not found.complete and any("stray.json" in path for path, _ in found.problems)
        assert found.reads.files == 4


def test_a_change_made_by_another_runner_is_seen_on_the_next_read(cards):
    folder, chat, _ = cards
    with Table(folder, [say("First.")]) as table:
        elsewhere = Harness([say("Second.")])  # another runner of the same library, with its own reads
        other = SceneRunner(table.store, table.authority, table.owner, owner=OWNER, runtime=elsewhere.runtime,
                            policy=POLICY)
        table.runner.seat(chat, ANA)
        (line,) = play(table, chat).lines
        assert len(table.runner.open(chat).records) == 1
        other.post(chat, table.player(), '"And now?"')  # a new turn written by the other runner
        take = other.generate(chat, table.player())
        other.keep(chat, table.player(), take.take)
        assert len(table.runner.open(chat).records) == 2
        other.strike(chat, table.player(), line.line_id)  # an existing turn rewritten by the other runner
        reread = table.runner.scenes.turns(chat)
        assert reread.reads.decoded == 1
        assert reread.turns[0].record.takes[0].lines[0].state == "struck"


def test_a_file_damaged_after_it_was_read_is_reported_not_served_from_what_was_read(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 3)
    with Table(folder) as table:
        assert table.runner.open(chat).turns.complete
        path = turn_file(folder, chat, 2)
        good = path.read_bytes()
        path.write_bytes(b"{not json")
        scene = table.runner.open(chat)
        assert not scene.turns.complete and any(kind == "turns" for kind, _ in scene.problems)
        assert [record.number for record in scene.records] == [1, 3]
        with pytest.raises(SceneRefused) as error:
            table.runner.post(chat, table.player(), '"Hello."')
        assert error.value.reason == "turns_unreadable"
        path.write_bytes(good)  # put back: read and decoded again, never left out
        mended = table.runner.scenes.turns(chat)
        assert mended.complete and [stored.record.number for stored in mended.turns] == [1, 2, 3]


def test_a_turn_that_cannot_be_decoded_stays_a_problem_on_every_read(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 2)
    path = turn_file(folder, chat, 2)
    document = path.read_text(encoding="utf-8")
    assert '"kept"' in document
    path.write_text(document.replace('"kept"', '"no-such-state"'), encoding="utf-8")  # a valid file, a bad turn
    with Table(folder) as table:
        for _ in range(2):
            found = table.runner.scenes.turns(chat)
            assert not found.complete and [stored.record.number for stored in found.turns] == [1]
        assert found.reads.decoded == 0 and found.reads.reused == 2  # the refusal is remembered with its bytes


def test_a_warm_runner_sends_the_same_prompts_as_a_fresh_one_over_an_edited_history(cards, tmp_path):
    folder, chat, _ = cards
    with Table(folder, [say("Words to forget."), say("A second answer."), say("Third.")]) as table:
        table.runner.seat(chat, ANA)
        (struck,) = play(table, chat).lines
        play(table, chat, '"And the cargo?"')
        table.runner.strike(chat, table.player(), struck.line_id)  # an edit to a turn the runner already read
        table.runner.post(chat, table.player(), '"Who heard that?"')
        copy = tmp_path / "copy"
        shutil.copytree(folder, copy)
        warm = table.runner
        warm.open(chat)  # what it holds now is everything it read before the copy was made
        warm.generate(chat, table.player())
        warm_prompts = table.harness.prompts()[-1:]
    with Table(copy, [say("Third.")]) as fresh:
        fresh.runner.generate(chat, fresh.player())
        fresh_prompts = fresh.harness.prompts()
    assert warm_prompts == fresh_prompts
    assert "Words to forget." not in warm_prompts[0]  # the struck line is out of what was heard


# -- the store's part: skip by name, reuse by etag ---------------------------------------------------

def test_campaign_turns_reuses_a_record_only_while_its_bytes_are_unchanged(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 3)
    with Table(folder) as table:
        store = table.store
        first = store.campaign_turns(chat.campaign_id)
        again = store.campaign_turns(chat.campaign_id, known=dict(first.records))
        assert again.records == first.records
        assert all(again.records[ref] is first.records[ref] for ref in first.records)
        path = turn_file(folder, chat, 1)
        path.write_text(path.read_text(encoding="utf-8").replace("Line 1.", "Line one."), encoding="utf-8")
        changed = store.campaign_turns(chat.campaign_id, known=dict(first.records))
        moved = [ref for ref in first.records if changed.records[ref] is not first.records[ref]]
        assert [ref.turn_id for ref in moved] == [f"{chat.chat_id}.000001"]
        assert changed.records[moved[0]].etag != first.records[moved[0]].etag
        skipped = store.campaign_turns(chat.campaign_id, skip=lambda name: True)
        assert not skipped.records and not skipped.problems


def test_a_known_record_is_never_reused_at_another_address(cards):
    folder, chat, _ = cards
    seed_history(folder, chat, 2)
    with Table(folder) as table:
        first = table.store.campaign_turns(chat.campaign_id)
        one, two = sorted(first.records, key=lambda ref: ref.turn_id)
        turn_file(folder, chat, 2).write_bytes(turn_file(folder, chat, 1).read_bytes())  # turn 1's bytes as turn 2
        plain = table.store.campaign_turns(chat.campaign_id)
        miskeyed = table.store.campaign_turns(chat.campaign_id, known={two: first.records[one]})
        assert [problem.code for problem in plain.problems] == ["address_mismatch"]
        assert [problem.code for problem in miskeyed.problems] == ["address_mismatch"]
        assert set(miskeyed.records) == set(plain.records) == {one}
