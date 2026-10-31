"""Pure play-scope/chat values, their transitions and payload codec (Sprint 107)."""

from __future__ import annotations

import pytest

from grimoire.chats import (
    CAMPAIGN_CONTINUITY, CHAT_SCHEMA, MAX_BINDINGS, NOT_WITNESSED, SCOPE_SCHEMA, BindingLimit, ChatCodecError,
    ChatModelError, ChatRecord, Launch, NewBinding, PlayScope, RosterEntry, SourceBinding, WorldBinding,
    chat_from_payload, chat_to_payload, scope_from_payload, scope_to_payload,
)
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef

T0, T1, T2 = "2026-10-31T10:00:00Z", "2026-10-31T10:05:00Z", "2026-10-31T10:09:00Z"
CHAT = ChatRef("quick-1", "thread-1")
MARA = SourceRef("catalog:cards", "character", "mara", "r1")
OTHER_MARA = SourceRef("catalog:other", "character", "mara", "r1")
LORE = SourceRef("catalog:cards", "lorebook", "harbor-notes", "r1")
SCENARIO = SourceRef("catalog:cards", "scenario", "storm")
SAM = SourceRef("catalog:me", "character", "sam", "r1")
MARA_1 = InstanceRef("quick-1", "character", "mara-1")
SAM_1 = InstanceRef("quick-1", "character", "sam-1")


def started(**kwargs) -> ChatRecord:
    bindings = (NewBinding(MARA, "character", "r1", MARA_1), NewBinding(LORE, "lorebook", "r1"),
                NewBinding(SAM, "persona", "r1", SAM_1))
    return ChatRecord.start(CHAT, Launch("character", MARA), T0, bindings, **kwargs)


def test_a_started_chat_binds_its_launch_and_actors_at_seq_zero():
    chat = started(label="Mara")
    assert chat.seq == 0 and chat.created_at == chat.last_activity == T0
    assert [b.introduced_seq for b in chat.bindings] == [0, 0, 0]
    assert [entry.actor for entry in chat.roster] == [MARA_1, SAM_1]
    assert all(entry.prior_history == NOT_WITNESSED for entry in chat.roster)
    assert chat.persona.ref == SAM


def test_transitions_never_change_identity_launch_or_actors():
    chat = started()
    newcomer = InstanceRef("quick-1", "character", "mara-2")
    later = (chat.attach(NewBinding(SCENARIO, "scenario", "r3"), T1)
             .attach(NewBinding(OTHER_MARA, "character", "r1", newcomer), T1)
             .detach(LORE, "lorebook", T2).relabel("Storm night", T2))
    assert (later.ref, later.created_at, later.launch) == (chat.ref, chat.created_at, chat.launch)
    assert later.seq == 4 and later.last_activity == T2
    assert [entry.actor for entry in later.roster] == [MARA_1, SAM_1, newcomer]
    added = later.roster_entry(newcomer)
    assert added.introduced_seq == 2 and added.prior_history == NOT_WITNESSED
    tombstone = next(b for b in later.bindings if b.ref == LORE)
    assert not tombstone.active and tombstone.detached_seq == 3 and tombstone.detached_at == T2
    scenario = next(b for b in later.bindings if b.role == "scenario")
    assert scenario.ref == SCENARIO and scenario.resolved_revision == "r3"  # following the head, as it was then


def test_detaching_a_character_keeps_its_actor_and_reattaching_may_reuse_it():
    chat = started().detach(MARA, "character", T1)
    assert MARA_1 in [entry.actor for entry in chat.roster]
    again = chat.attach(NewBinding(MARA, "character", "r1", MARA_1, CAMPAIGN_CONTINUITY), T2)
    assert len(again.roster) == 2 and len(again.active_bindings("character")) == 1


@pytest.mark.parametrize("make, reason", [
    (lambda c: c.attach(NewBinding(MARA.unpinned(), "character", "r2", InstanceRef("quick-1", "character", "x")),
                        T1), "duplicate_binding"),
    (lambda c: c.attach(NewBinding(OTHER_MARA, "persona", "r1", InstanceRef("quick-1", "character", "x")), T1),
     "second_persona"),
    (lambda c: c.attach(NewBinding(LORE, "character", "r1", InstanceRef("quick-1", "character", "x")), T1),
     "role_kind"),
    (lambda c: c.attach(NewBinding(OTHER_MARA, "character", "r1", MARA_1), T1), "roster_mismatch"),
    (lambda c: c.attach(NewBinding(OTHER_MARA, "character", "r1", InstanceRef("elsewhere", "character", "x")),
                        T1), "foreign_actor"),
    (lambda c: c.attach(NewBinding(OTHER_MARA, "character", "r1", SAM_1), T1), "roster_mismatch"),
    (lambda c: c.detach(SCENARIO, "scenario", T1), "not_bound"),
])
def test_invalid_transitions_are_refused_by_reason(make, reason):
    with pytest.raises(ChatModelError) as error:
        make(started())
    assert error.value.reason == reason


@pytest.mark.parametrize("kwargs, reason", [
    ({"kind": "premise", "ref": MARA, "premise": "x"}, "launch_kind"),
    ({"kind": "premise", "premise": " "}, "invalid_field"),
    ({"kind": "premise", "premise": "x" * 4001}, "invalid_field"),
    ({"kind": "character"}, "launch_kind"),
    ({"kind": "character", "ref": LORE}, "role_kind"),
    ({"kind": "scenario", "ref": MARA}, "role_kind"),
    ({"kind": "world", "ref": MARA}, "launch_kind"),
    ({"kind": "character", "ref": MARA, "premise": "x"}, "launch_kind"),
])
def test_launches_are_validated(kwargs, reason):
    with pytest.raises(ChatModelError) as error:
        Launch(**kwargs)
    assert error.value.reason == reason


def test_scenario_and_premise_launches_need_no_actor():
    scenario_only = ChatRecord.start(CHAT, Launch("scenario", SCENARIO), T0,
                                     (NewBinding(SCENARIO, "scenario", "r3"),))
    assert scenario_only.roster == () and scenario_only.bindings[0].actor is None
    premise = ChatRecord.start(CHAT, Launch("premise", premise="A storm at the harbor"), T0)
    assert premise.bindings == () and premise.roster == ()
    with pytest.raises(ChatModelError) as error:
        ChatRecord.start(CHAT, Launch("scenario", SCENARIO), T0)
    assert error.value.reason == "launch_unbound"


@pytest.mark.parametrize("kwargs", [
    {"actor": None}, {"resolved_revision": "r2"}, {"introduced_at": "yesterday"}, {"detached_seq": 3},
    {"detached_seq": 0, "detached_at": T1}, {"actor": InstanceRef("q", "creature", "x")},
])
def test_bindings_are_validated(kwargs):
    base = dict(ref=MARA, role="character", resolved_revision="r1", introduced_seq=0, introduced_at=T0,
                actor=MARA_1)
    with pytest.raises(ChatModelError):
        SourceBinding(**{**base, **kwargs})


def test_bounds_are_named_and_enforced():
    chat = ChatRecord.start(CHAT, Launch("premise", premise="p"), T0)
    for n in range(MAX_BINDINGS):
        lore = SourceRef("catalog:cards", "lorebook", f"l{n}", "r1")
        chat = chat.attach(NewBinding(lore, "lorebook", "r1"), T1)
        if n % 2:
            chat = chat.detach(lore, "lorebook", T1)  # tombstones still count
    with pytest.raises(BindingLimit) as error:
        chat.attach(NewBinding(SourceRef("catalog:cards", "lorebook", "one-more", "r1"), "lorebook", "r1"), T1)
    assert error.value.reason == "binding_limit"


@pytest.mark.parametrize("label", ["", " ", "x" * 201, "a\x00b"])
def test_labels_are_bounded_display_text(label):
    with pytest.raises(ChatModelError):
        started(label=label)


def test_roster_and_binding_must_agree():
    chat = started()
    with pytest.raises(ChatModelError) as error:
        ChatRecord(CHAT, chat.launch, T0, T0, 0, chat.bindings, chat.roster[1:])
    assert error.value.reason == "roster_mismatch"
    wrong = RosterEntry(MARA_1, "character", 0, T0, OTHER_MARA)
    with pytest.raises(ChatModelError):
        ChatRecord(CHAT, chat.launch, T0, T0, 0, chat.bindings, (wrong, chat.roster[1]))


# -- scope ----------------------------------------------------------------------------------

ETAG = "sha256:" + "a" * 64


def test_a_scope_has_no_world_by_default_and_naming_changes_only_presentation():
    scope = PlayScope(ScopeRef("quick-1"), T0)
    assert scope.world is None and not scope.is_campaign
    named = scope.named("Harbor nights").with_world(WorldBinding("world:harbor", ETAG, T1))
    assert named.ref == scope.ref and named.created_at == scope.created_at and named.is_campaign
    assert named.world.source == "world:harbor"


def test_session_zero_is_opaque_and_bounded():
    data = {"boundaries": [{"theme": "gore", "treatment": "avoid"}], "future": {"x": 1.5}}
    scope = PlayScope(ScopeRef("q"), T0).with_session_zero(data)
    assert scope_from_payload(scope.ref, SCOPE_SCHEMA, scope_to_payload(scope)) == scope
    assert scope_to_payload(scope)["session_zero"] == data
    deep: dict = {}
    current = deep
    for _ in range(70):
        current["x"] = {}
        current = current["x"]
    with pytest.raises(ChatModelError):
        PlayScope(ScopeRef("q"), T0).with_session_zero(deep)
    with pytest.raises(ChatModelError):
        PlayScope(ScopeRef("q"), T0).with_session_zero(["not", "an", "object"])  # type: ignore[arg-type]


@pytest.mark.parametrize("kwargs", [
    {"source": "campaign:x"}, {"observed_manifest_etag": "r1"}, {"attached_at": "now"},
])
def test_world_bindings_are_validated(kwargs):
    with pytest.raises(ChatModelError):
        WorldBinding(**{"source": "world:harbor", "observed_manifest_etag": ETAG, "attached_at": T0, **kwargs})


# -- codec ------------------------------------------------------------------------------------

def test_chat_payloads_round_trip_and_keep_unknown_fields():
    chat = started(label="Mara").attach(NewBinding(SCENARIO, "scenario", "r3"), T1).detach(LORE, "lorebook", T2)
    payload = chat_to_payload(chat)
    payload["x-future"] = {"kept": [1, 2]}
    decoded = chat_from_payload(CHAT, CHAT_SCHEMA, payload)
    assert decoded.extras == {"x-future": {"kept": (1, 2)}}
    assert chat_to_payload(decoded) == payload
    assert chat_to_payload(decoded.relabel("New", T2))["x-future"] == {"kept": [1, 2]}


def test_scope_payloads_round_trip_and_keep_unknown_fields():
    scope = PlayScope(ScopeRef("q"), T0, "Harbor", WorldBinding("world:harbor", ETAG, T1), extras={"x": 1})
    assert scope_from_payload(scope.ref, SCOPE_SCHEMA, scope_to_payload(scope)) == scope


@pytest.mark.parametrize("change, reason", [
    (lambda p: p.update(schema="x"), "identity_in_payload"),
    (lambda p: p.update(chat_id="t"), "identity_in_payload"),
    (lambda p: p["bindings"][0].update(colour="red"), "unknown_field"),
    (lambda p: p["launch"].update(kind="world"), "launch_kind"),
    (lambda p: p.pop("seq"), "invalid_field"),
    (lambda p: p["bindings"][0].update(ref="Not A Ref"), "invalid_field"),
    (lambda p: p.update(bindings="nope"), "invalid_field"),
    (lambda p: p["roster"].pop(), "roster_mismatch"),
])
def test_payloads_this_version_cannot_interpret_are_refused_with_a_reason(change, reason):
    payload = chat_to_payload(started())
    change(payload)
    with pytest.raises(ChatCodecError) as error:
        chat_from_payload(CHAT, CHAT_SCHEMA, payload)
    assert error.value.reason == reason


def test_an_unknown_schema_is_refused_not_guessed():
    for schema in ("grimoire.chat/2", None, SCOPE_SCHEMA):
        with pytest.raises(ChatCodecError) as error:
            chat_from_payload(CHAT, schema, chat_to_payload(started()))
        assert error.value.reason == "unsupported_schema"
    with pytest.raises(ChatCodecError):
        scope_from_payload(ScopeRef("q"), CHAT_SCHEMA, {"created_at": T0})
