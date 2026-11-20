"""Agentic investigations (082): the bounded job loop, its evidence checks and the built-in auditor.

A worker is untrusted: these workers loop, ask for tools they were not offered, return things that are not
actions, raise, and cite evidence they never read. The job must stay within budget, run nothing it did not
offer, keep only checked findings and still hand over what it found.
"""

from __future__ import annotations

import itertools

import pytest
from grimoire.core.json_value import freeze
from grimoire.investigations import (
    Budget,
    Citation,
    ConsistencyAuditor,
    Finding,
    Finish,
    InvestigationError,
    InvestigationSpec,
    Listed,
    ListRecords,
    ReadRecord,
    RecordRead,
    Report,
    ToolResult,
    name_key,
    resolve,
    run_job,
)
from grimoire.providers.cancel import CancelToken

SOURCE = "world:harbor"


def record(kind, entity_id, name, payload, revision="r1"):
    return RecordRead(kind, entity_id, revision, f"sha256:{entity_id}-{revision}", name, freeze(payload))


class Tools:
    """An in-memory toolbox: what a store-backed one returns, and a count of what was asked."""

    def __init__(self, *records: RecordRead, extra_listed=()):
        self.records = {item.record: item for item in records}
        self.extra_listed = tuple(extra_listed)
        self.calls: list[tuple] = []

    def list(self):
        self.calls.append(("list",))
        listed = tuple(Listed(k, i, (r.revision,), r.revision) for (k, i), r in sorted(self.records.items()))
        return ToolResult("ok", listed=listed + self.extra_listed)

    def read(self, kind, entity_id):
        self.calls.append(("read", kind, entity_id))
        found = self.records.get((kind, entity_id))
        return ToolResult("missing") if found is None else ToolResult("ok", read=found)


class Script:
    def __init__(self, *actions):
        self.actions = iter(actions)
        self.seen = []

    def next_action(self, observation):
        self.seen.append(observation)
        return next(self.actions)


def clock():
    ticks = itertools.count()
    return lambda: float(next(ticks))


def spec(**budget) -> InvestigationSpec:
    tools = budget.pop("tools", None)
    kwargs = {"tools": tools} if tools is not None else {}
    return InvestigationSpec("job", SOURCE, "audit the harbor", Budget(**budget), **kwargs)


WREN = record("character", "wren", "Wren", {"description": "A sailor sworn to the Ashen Guild."})
GUILD = record("organization", "ashen-guild", "Ashen Guild", {"description": "Lamplighters."})


# -- budgets ------------------------------------------------------------------------------------------------

def test_a_worker_that_never_finishes_is_stopped_by_the_step_budget():
    class Forever:
        def next_action(self, observation):
            return ListRecords()

    tools = Tools(WREN)
    report = run_job(spec(max_steps=5), Forever(), tools, clock=clock())
    assert (report.status, report.reason) == ("budget_exhausted", "steps")
    assert len(report.steps) == 5 and len(tools.calls) == 5


def test_the_time_budget_is_measured_on_the_callers_clock():
    class Forever:
        def next_action(self, observation):
            return ListRecords()

    report = run_job(spec(max_seconds=3), Forever(), Tools(WREN), clock=clock())
    assert (report.status, report.reason) == ("budget_exhausted", "seconds")


def test_the_findings_budget_keeps_the_findings_it_allowed():
    tools = Tools(WREN, GUILD)
    finding = Finding("note", "one", (WREN.cite(),))
    other = Finding("note", "two", (GUILD.cite(),))
    report = run_job(spec(max_findings=1), Script(ReadRecord("character", "wren"),
                                                  ReadRecord("organization", "ashen-guild"),
                                                  Report(finding), Report(other)), tools, clock=clock())
    assert (report.status, report.reason) == ("budget_exhausted", "findings")
    assert [item.finding for item in report.findings] == [finding]
    assert (report.steps[-1].tool, report.steps[-1].outcome, report.steps[-1].detail) == ("report", "rejected",
                                                                                          "findings")


def test_a_read_past_the_read_budget_is_not_made():
    tools = Tools(WREN, GUILD)
    report = run_job(spec(max_reads=1), Script(ReadRecord("character", "wren"),
                                               ReadRecord("organization", "ashen-guild")), tools, clock=clock())
    assert (report.status, report.reason) == ("budget_exhausted", "reads")
    assert tools.calls == [("read", "character", "wren")]
    assert [(step.tool, step.outcome, step.detail) for step in report.steps][-1] == ("read", "rejected", "reads")


def test_time_spent_choosing_an_action_counts_before_the_action_runs():
    now = [0.0]

    class Slow:
        def next_action(self, observation):
            now[0] += 100.0
            return ReadRecord("character", "wren")

    tools = Tools(WREN)
    report = run_job(spec(max_seconds=1), Slow(), tools, clock=lambda: now[0])
    assert (report.status, report.reason) == ("budget_exhausted", "seconds")
    assert tools.calls == [] and report.steps == ()


class _Liar(str):
    """A string that claims to equal anything: a worker's attempt to cite a revision it never read."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


def test_a_citation_or_read_target_must_be_plain_text_not_a_lookalike():
    with pytest.raises(InvestigationError):
        Citation("character", "wren", _Liar("r7"), WREN.etag)
    with pytest.raises(InvestigationError):
        Citation(_Liar("character"), "wren", "r1", WREN.etag)
    tools = Tools(WREN)
    report = run_job(spec(), Script(ReadRecord(_Liar("character"), "wren"), Finish()), tools, clock=clock())
    assert tools.calls == [] and report.steps[0].outcome == "invalid_target"


def test_a_malformed_action_ends_the_job_failed_and_keeps_what_was_found():
    finding = Finding("note", "one", (WREN.cite(),))
    broken = object.__new__(Finish)  # never initialized: reading its note raises
    report = run_job(spec(), Script(ReadRecord("character", "wren"), Report(finding), broken), Tools(WREN),
                     clock=clock())
    assert (report.status, report.reason, report.note) == ("failed", "worker_error", "AttributeError")
    assert [item.finding for item in report.findings] == [finding]


# -- what the job runs ----------------------------------------------------------------------------------------

def test_only_offered_tools_run_and_anything_else_is_refused_without_reaching_the_toolbox():
    class Write:  # what a worker might try: there is no write action
        kind, entity_id = "character", "wren"

    tools = Tools(WREN)
    report = run_job(spec(tools=frozenset({"list"})),
                     Script(ReadRecord("character", "wren"), Write(), {"tool": "write"},
                            ReadRecord("../secrets", "x"), ListRecords(), Finish("done")),
                     tools, clock=clock())
    assert [(step.tool, step.outcome) for step in report.steps] == [
        ("read", "tool_not_allowed"), ("unknown", "unknown_action"), ("unknown", "unknown_action"),
        ("read", "tool_not_allowed"), ("list", "ok"), ("finish", "finished")]
    assert tools.calls == [("list",)]
    assert report.status == "finished" and report.note == "done"


def test_an_invalid_read_target_never_reaches_the_toolbox():
    tools = Tools(WREN)
    report = run_job(spec(), Script(ReadRecord("character", "../wren"), ReadRecord("Character", "wren"), Finish()),
                     tools, clock=clock())
    assert [step.outcome for step in report.steps[:2]] == ["invalid_target", "invalid_target"]
    assert tools.calls == []


def test_a_failing_worker_ends_the_job_and_keeps_its_findings_without_its_message():
    class Breaks:
        def __init__(self):
            self.actions = iter([ReadRecord("character", "wren"), Report(Finding("note", "kept", (WREN.cite(),)))])

        def next_action(self, observation):
            for action in self.actions:
                return action
            raise RuntimeError("secret-token-in-message")

    report = run_job(spec(), Breaks(), Tools(WREN), clock=clock())
    assert (report.status, report.reason, report.note) == ("failed", "worker_error", "RuntimeError")
    assert len(report.findings) == 1
    assert "secret-token" not in repr(report)


@pytest.mark.parametrize("result", [RuntimeError("boom"), "not a result",
                                    ToolResult("ok", read=GUILD)])  # answers for another record
def test_a_failing_or_lying_toolbox_ends_the_job_as_a_tool_error(result):
    class Bad(Tools):
        def read(self, kind, entity_id):
            if isinstance(result, Exception):
                raise result
            return result

    report = run_job(spec(), Script(ReadRecord("character", "wren")), Bad(WREN), clock=clock())
    assert (report.status, report.reason) == ("failed", "tool_error")
    assert report.reads == {}


def test_cancelling_before_the_first_action_runs_nothing():
    cancel = CancelToken()
    cancel.cancel()
    tools = Tools(WREN)
    report = run_job(spec(), ConsistencyAuditor(SOURCE), tools, clock=clock(), cancel=cancel)
    assert report.status == "cancelled" and report.steps == () and tools.calls == []


def test_the_worker_sees_what_is_left_and_the_trace_counts_the_run():
    worker = Script(ListRecords(), ReadRecord("character", "wren"), Finish())
    report = run_job(spec(max_steps=10, max_reads=4), worker, Tools(WREN), clock=clock())
    last = worker.seen[-1]
    assert (last.remaining.steps, last.remaining.reads) == (8, 3)
    assert last.result.read == WREN
    root = report.trace.spans[0]
    assert root.operation == "investigation" and root.counts["steps"] == 3 and root.counts["reads"] == 1


# -- evidence ----------------------------------------------------------------------------------------------

def test_findings_are_kept_only_when_their_evidence_is_what_the_job_read():
    reread = record("character", "wren", "Wren", {"description": "Now a smuggler."}, revision="r2")

    class Changing(Tools):
        def __init__(self):
            super().__init__(WREN)
            self.reads = iter([WREN, reread])

        def read(self, kind, entity_id):
            return ToolResult("ok", read=next(self.reads))

    good = Finding("note", "Wren names the guild", (WREN.cite("/payload/description", "Ashen Guild"),))
    report = run_job(spec(), Script(
        Report(Finding("note", "no evidence", ())),
        ReadRecord("character", "wren"),
        Report(good),
        Report(good),  # the same evidence twice
        Report(Finding("note", "not text", (WREN.cite("/payload", "Guild"),))),
        Report("a finding?"),
        ReadRecord("character", "wren"),  # read again: it is r2 now
        Report(Finding("note", "stale", (WREN.cite("/payload/description", "Ashen Guild"),))),
        Finish()), Changing(), clock=clock())
    assert [item.finding for item in report.findings] == [good]
    assert [item.reason for item in report.rejected] == ["uncited", "duplicate_finding", "quote_not_text",
                                                         "not_a_finding", "revision_mismatch"]
    assert report.findings[0].finding_id == "f1"
    assert report.reads[("character", "wren")] == reread


def test_json_pointers_resolve_escapes_and_strict_array_indices():
    value = freeze({"a/b": {"~k": ["x", "y"]}, "list": ["p", "q"]})
    assert resolve(value, "/a~1b/~0k/1") == (True, "y")
    assert resolve(value, "/list/01")[0] is False
    assert resolve(value, "/list/2")[0] is False
    assert resolve(value, "/list/-1")[0] is False
    assert resolve(value, "/a~2b")[0] is False
    assert resolve(value, "") == (True, value)


def test_citation_and_spec_values_refuse_what_cannot_be_checked():
    with pytest.raises(InvestigationError):
        Citation("character", "wren", "r1", "sha256:x", None, "a quote with no field")
    with pytest.raises(InvestigationError):
        Citation("character", "wren", "r1", "sha256:x", "payload/description")
    with pytest.raises(InvestigationError):
        InvestigationSpec("job", SOURCE, "goal", tools=frozenset({"write"}))
    with pytest.raises(InvestigationError):
        InvestigationSpec("Job!", SOURCE, "goal")
    with pytest.raises(InvestigationError):
        Budget(max_steps=0)
    with pytest.raises(InvestigationError):
        Finding("note", "x", tuple(WREN.cite() for _ in range(17)))


# -- the built-in auditor ------------------------------------------------------------------------------------

def test_names_match_across_case_spacing_punctuation_and_a_leading_article():
    assert name_key("The Ashen  Guild") == name_key("ashen-guild") == "ashen guild"
    assert name_key("Theodora") == "theodora"
    assert name_key(None) == name_key("") == name_key("  ") == ""


def test_the_auditor_reports_each_finding_as_soon_as_it_has_the_evidence():
    tools = Tools(WREN, GUILD)
    report = run_job(spec(), ConsistencyAuditor(SOURCE), tools, clock=clock())
    assert [(step.tool, step.target, step.outcome) for step in report.steps] == [
        ("list", "", "ok"), ("read", "character/wren", "ok"), ("read", "organization/ashen-guild", "ok"),
        ("report", "f1", "accepted"), ("finish", "", "finished")]
    assert report.findings[0].finding.category == "missing_link"


def test_a_derived_portrayal_that_refers_to_its_base_is_not_a_duplicate():
    base = record("character", "mara", "Mara", {"description": "A lamplighter."})
    portrayal = record("character", "mara-noir", "Mara",
                       {"derivation": {"base": "world:harbor/character/mara@r1"}})
    namesake = record("character", "mara-2", "MARA", {"description": "A cook."})
    report = run_job(spec(), ConsistencyAuditor(SOURCE), Tools(base, portrayal, namesake), clock=clock())
    pairs = [{c.entity_id for c in item.finding.citations} for item in report.findings]
    assert pairs == [{"mara", "mara-2"}, {"mara-2", "mara-noir"}]


def test_short_names_and_whole_word_boundaries_are_not_mentions():
    ash = record("item", "ash", "Ash", {"description": "Grey dust."})
    ox = record("creature", "ox", "Ox", {"description": "A beast."})
    text = record("character", "wren", "Wren", {"description": "Ashes and an oxcart."})
    report = run_job(spec(), ConsistencyAuditor(SOURCE), Tools(ash, ox, text), clock=clock())
    assert report.findings == ()


def test_references_into_other_sources_are_not_judged():
    item = record("item", "lantern", "Lantern", {"maker": "world:saltmarch/character/winifred"})
    report = run_job(spec(), ConsistencyAuditor(SOURCE), Tools(item), clock=clock())
    assert report.findings == ()


def test_a_refused_listing_ends_the_audit_with_its_reason():
    class Denied(Tools):
        def list(self):
            return ToolResult("denied", detail="not_granted")

    report = run_job(spec(), ConsistencyAuditor(SOURCE), Denied(), clock=clock())
    assert report.status == "finished" and report.note == "the listing was refused: denied"
