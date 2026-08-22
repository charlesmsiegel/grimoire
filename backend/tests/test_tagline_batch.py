"""POST /worlds/{wid}/characters/taglines/generate — derive the taglines a bulk
import left blank, in one pass over the roster (#57).

Per-character `tagline/generate` is a *preview*: it returns a sentence and
writes nothing, so the popup's Skip leaves no trace (#59). This route is the
other half of that pair and deliberately does not behave that way — a roster of
three hundred imported cards is not reviewed one modal at a time, and the
request to derive the whole roster IS the consent. So it writes as it goes, and
the rules that keep that safe are what this file pins:

  * only a character whose tagline is BLANK is ever a target, and the blank is
    re-checked immediately before the write, so a sentence written by hand
    mid-run survives (the re-check is a fresh read, not a lock -- see the
    route's own note on how far that reaches);
  * the first provider failure stops the run rather than spending the roster's
    worth of calls on a broken key — and stopping is cheap, because a re-run
    targets whatever is still blank, which is exactly what the failed run left;
  * one unusable card is skipped, not fatal.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import FailingOpenRouter, FakeOpenRouterComplete


@pytest.fixture
def client(monkeypatch, tmp_path):
    """conftest's `client`, minus its default fake.

    Shadowed deliberately: every test here says what the provider answers, and
    the shared fixture's stand-in would let one that forgot quietly derive
    "Hello" for the whole roster and still pass.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _world(client, name="Realm"):
    wid = client.post("/api/worlds", json={"name": name}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return wid


def _character(client, wid, name):
    return client.post(f"/api/worlds/{wid}/characters",
                       json={"name": name, "version_name": "main"}).json()["character"]


def _answers(client, replies):
    """One fake for the whole request, so a test can count its calls."""
    fake = FakeOpenRouterComplete(replies)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _fails(client, **kw):
    fake = FailingOpenRouter(**kw)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _derive(client, wid):
    resp = client.post(f"/api/worlds/{wid}/characters/taglines/generate")
    frames = [json.loads(ln[len("data:"):].strip())
              for ln in resp.text.splitlines() if ln.startswith("data:")]
    return resp, frames


def _tagline(client, wid, cid):
    return client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json()["tagline"]


def test_derives_only_the_blank_taglines(client):
    wid = _world(client)
    mara, seraphine, winifred = (_character(client, wid, n)
                                 for n in ("Mara", "Seraphine", "Winifred"))
    client.put(f"/api/worlds/{wid}/characters/{seraphine}/tagline",
               json={"tagline": "Already said."})
    fake = _answers(client, ["A courier with cold hands.", "A locksmith who never sleeps."])

    resp, frames = _derive(client, wid)

    assert resp.status_code == 200
    # Two calls, not three: the character who already has a sentence is not a target.
    assert fake.calls == 2
    assert _tagline(client, wid, seraphine) == "Already said."
    assert _tagline(client, wid, mara) == "A courier with cold hands."
    assert _tagline(client, wid, winifred) == "A locksmith who never sleeps."
    assert frames[0] == {"total": 2}
    assert frames[-1]["summary"] == {"total": 2, "written": 2, "skipped": 0, "stopped": False}


def test_streams_a_frame_per_character_as_each_lands(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _answers(client, ["A courier with cold hands."])

    _, frames = _derive(client, wid)

    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara",
                         "tagline": "A courier with cold hands."}


def test_an_empty_backlog_calls_nobody(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    client.put(f"/api/worlds/{wid}/characters/{cid}/tagline", json={"tagline": "Said."})
    fake = _answers(client, ["never asked"])

    _, frames = _derive(client, wid)

    assert fake.calls == 0
    assert frames[0] == {"total": 0}
    assert frames[-1]["summary"] == {"total": 0, "written": 0, "skipped": 0, "stopped": False}


def test_a_roster_with_no_characters_is_not_an_error(client):
    wid = _world(client)
    resp, frames = _derive(client, wid)
    assert resp.status_code == 200
    assert frames[0] == {"total": 0}


def test_requires_a_connection_before_streaming_anything(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    _character(client, wid, "Mara")
    _answers(client, ["never asked"])

    resp = client.post(f"/api/worlds/{wid}/characters/taglines/generate")

    # 409 with a JSON body, not a 200 whose first frame carries the bad news:
    # nothing has been attempted yet, so this is an ordinary refusal.
    assert resp.status_code == 409
    assert "data:" not in resp.text


def test_unknown_world_is_404(client):
    resp = client.post("/api/worlds/nope/characters/taglines/generate")
    assert resp.status_code == 404


def test_stops_at_the_first_provider_failure(client):
    wid = _world(client)
    mara = _character(client, wid, "Mara")
    winifred = _character(client, wid, "Winifred")
    fake = _fails(client, kind="rate_limit", message="slow down")

    _, frames = _derive(client, wid)

    # One call, not one per character: a key that is out of quota is out of
    # quota for the whole roster, and finding that out three hundred times is
    # the expensive way to learn it.
    assert fake.calls == 1
    assert _tagline(client, wid, mara) == ""
    assert _tagline(client, wid, winifred) == ""
    assert frames[-2]["error"] == {"detail": "slow down", "kind": "rate_limit"}
    assert frames[-2]["character"] == "mara"
    assert frames[-1]["summary"] == {"total": 2, "written": 0, "skipped": 0, "stopped": True}


def test_keeps_what_landed_before_the_failure(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _fails(client, deltas=["A courier with cold hands."], fail_after=1)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, "mara") == "A courier with cold hands."
    assert _tagline(client, wid, "winifred") == ""
    assert frames[-1]["summary"] == {"total": 2, "written": 1, "skipped": 0, "stopped": True}


def test_a_blank_reply_is_skipped_not_written(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    _answers(client, ["   \n  "])

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, cid) == ""
    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara", "skipped": "blank"}
    assert frames[-1]["summary"] == {"total": 1, "written": 0, "skipped": 1, "stopped": False}


def test_a_tagline_written_before_a_target_s_turn_costs_no_call(client, monkeypatch):
    """Filled since the scan, so there is nothing to derive — and finding that
    out after paying for the generation is the expensive way to do it."""
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    fake = _answers(client, ["A courier with cold hands."])
    root = store.worlds.world_root(wid)
    real_read_card = store.characters.read_card

    def racing_read_card(r, c, v):
        # Another request landing between the roster scan and this target's turn.
        store.taglines.write(root, c, "Hand-written, mid-run.")
        return real_read_card(r, c, v)

    monkeypatch.setattr(store.characters, "read_card", racing_read_card)

    _, frames = _derive(client, wid)

    assert fake.calls == 0
    assert _tagline(client, wid, cid) == "Hand-written, mid-run."
    assert frames[1]["skipped"] == "already set"
    assert frames[-1]["summary"] == {"total": 1, "written": 0, "skipped": 1, "stopped": False}


def test_a_tagline_written_during_the_call_is_not_overwritten(client, monkeypatch):
    """The other side of the same window: the check before the call cannot see a
    save that lands while the model is still answering, so the one after it is
    what actually protects the write."""
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    _answers(client, ["A courier with cold hands."])
    root = store.worlds.world_root(wid)
    real_parse = store.taglines.parse_output

    def racing_parse(text):
        # Runs after the reply and before the write -- the moment a hand-save
        # landing during the generation would become visible.
        store.taglines.write(root, "mara", "Hand-written, mid-call.")
        return real_parse(text)

    monkeypatch.setattr(store.taglines, "parse_output", racing_parse)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, cid) == "Hand-written, mid-call."
    assert frames[1]["skipped"] == "already set"


def test_a_character_replaced_under_the_same_name_keeps_its_own_blank(client, monkeypatch):
    """A delete frees the slug, so a character recreated under the same name
    takes it back, and no existence check can tell the two apart. The card can:
    the sentence belongs to the text it was derived from, and that text is what
    the write is fenced on.

    A replacement whose card is byte-identical is deliberately NOT
    distinguished. Nothing in the store carries an identity beyond the id, and
    inventing one for this would buy nothing: a sentence derived from exactly
    the text the new card holds describes it exactly as well.
    """
    wid = _world(client)
    _character(client, wid, "Mara")
    root = store.worlds.world_root(wid)
    _answers(client, ["A courier with cold hands."])
    real_parse = store.taglines.parse_output

    def replacing_parse(text):
        store.characters.delete_character(root, "mara")
        card = store.characters.blank_card("Mara")
        card["data"]["description"] = "somebody else entirely"
        cid, _ = store.characters.create_character(root, "Mara", "main", card)
        assert cid == "mara", "the slug has to come back for this to be the case it means"
        return real_parse(text)

    monkeypatch.setattr(store.taglines, "parse_output", replacing_parse)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, "mara") == "", "the old character's sentence landed on the new one"
    assert frames[1]["skipped"] == "changed"


def test_a_card_edited_during_the_call_is_not_described_by_the_stale_sentence(client, monkeypatch):
    """Same fence, gentler case: the card moved while the model was answering,
    so the sentence describes text that is no longer there. Left blank, which is
    what makes a re-run derive it from what the card says now."""
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    root = store.worlds.world_root(wid)
    _answers(client, ["A courier with cold hands."])
    real_parse = store.taglines.parse_output

    def editing_parse(text):
        card = store.characters.read_card(root, "mara", "main")
        card.setdefault("data", {})["description"] = "a locksmith now"
        store.characters.update_version(root, "mara", "main", card)
        return real_parse(text)

    monkeypatch.setattr(store.taglines, "parse_output", editing_parse)

    _, frames = _derive(client, wid)

    assert _tagline(client, wid, cid) == ""
    assert frames[1]["skipped"] == "changed"


def test_a_character_deleted_during_the_call_is_not_resurrected(client, monkeypatch):
    """`taglines.write` creates the parent directory, so writing to a character
    who has just been deleted would rebuild `characters/<cid>/` holding nothing
    but tagline.md: invisible to every listing, and still enough to make the
    next character of that name take a suffixed id."""
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    root = store.worlds.world_root(wid)
    _answers(client, ["A courier with cold hands."])
    real_parse = store.taglines.parse_output

    def deleting_parse(text):
        store.characters.delete_character(root, "mara")
        return real_parse(text)

    monkeypatch.setattr(store.taglines, "parse_output", deleting_parse)

    _, frames = _derive(client, wid)

    assert not (root / "characters" / cid).exists(), "the deleted character was rebuilt"
    assert frames[1]["skipped"] == "gone"
    assert frames[-1]["summary"] == {"total": 1, "written": 0, "skipped": 1, "stopped": False}


def test_each_target_is_prompted_from_its_current_default_version(client, monkeypatch):
    """A default version changed after the scan leaves the old card in place, so
    a stale id still reads -- it just answers with the wrong card, and the
    tagline written from it is not blank for a later run to correct.

    The change has to land INSIDE the run to mean anything, so it is made while
    the first character's reply is being parsed and asserted on the second's
    prompt."""
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Mara", "version_name": "main"})
    win = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Winifred", "version_name": "main",
                            "card": {"data": {"name": "Winifred",
                                              "description": "a courier still"}}}).json()["character"]
    later = client.post(f"/api/worlds/{wid}/characters/{win}/versions",
                        json={"name": "later",
                              "card": {"data": {"name": "Winifred",
                                                "description": "a locksmith now"}}}).json()["version"]
    fake = _answers(client, ["A courier with cold hands.", "A locksmith who never sleeps."])
    root = store.worlds.world_root(wid)
    real_parse = store.taglines.parse_output
    moved = []

    def repointing_parse(text):
        if not moved:      # once, during the FIRST character's turn
            # Through the store, not a nested request: the run holds the test
            # client's only worker, so an HTTP call from in here never returns.
            store.characters.set_default_version(root, win, later)
            moved.append(later)
        return real_parse(text)

    monkeypatch.setattr(store.taglines, "parse_output", repointing_parse)

    _derive(client, wid)

    assert moved == [later]
    assert fake.calls == 2
    prompt = fake.messages[-1]["content"]      # the second character's user message
    assert "a locksmith now" in prompt and "a courier still" not in prompt


def test_a_card_with_no_data_is_prompted_from_nothing_rather_than_500ing(client):
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    vid = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["default_version"]
    # Version PUT accepts ANY dict as a card and writes it unchanged, so this is
    # supported state, not a corrupt store (see the voice-anchor route's note).
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}", json={"card": {}})
    fake = _answers(client, ["A courier with cold hands."])

    resp, _ = _derive(client, wid)

    assert resp.status_code == 200
    assert fake.calls == 1
    assert _tagline(client, wid, cid) == "A courier with cold hands."


def test_an_unreadable_card_is_skipped_and_the_run_continues(client, monkeypatch):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    fake = _answers(client, ["A locksmith who never sleeps."])
    real_read_card = store.characters.read_card

    def one_bad_card(r, c, v):
        if c == "mara":
            raise store.characters.VersionNotFound(v)
        return real_read_card(r, c, v)

    monkeypatch.setattr(store.characters, "read_card", one_bad_card)

    _, frames = _derive(client, wid)

    assert fake.calls == 1
    assert _tagline(client, wid, "winifred") == "A locksmith who never sleeps."
    assert frames[1] == {"done": 1, "character": "mara", "name": "Mara",
                         "skipped": "unreadable card"}
    assert frames[-1]["summary"] == {"total": 2, "written": 1, "skipped": 1, "stopped": False}


def test_each_call_is_metered_as_a_tagline(client):
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _answers(client, ["A courier with cold hands.", "A locksmith who never sleeps."])

    _derive(client, wid)

    by_task = {b["key"]: b["calls"] for b in store.usage.summary(days=1)["by_task"]}
    assert by_task == {"tagline": 2}


def test_a_failed_write_becomes_an_error_frame_rather_than_a_dead_stream(client, monkeypatch):
    """The response is a 200 the moment the first frame goes out, so a failure
    after that has nowhere to go but a frame — and the summary still follows,
    because "what did this run manage" has an answer even after a crash."""
    wid = _world(client)
    _character(client, wid, "Mara")
    _character(client, wid, "Winifred")
    _answers(client, ["A courier with cold hands."])

    def no_disk(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(store.taglines, "write", no_disk)

    resp, frames = _derive(client, wid)

    assert resp.status_code == 200
    assert frames[-2]["error"] == {"detail": "read-only file system", "kind": "tagline"}
    assert frames[-1]["summary"] == {"total": 2, "written": 0, "skipped": 0, "stopped": True}


def test_a_hand_edited_card_is_derived_from_rather_than_500ing(client):
    """The roster scan runs before a single frame goes out, so a card it cannot
    read is a 500 with no stream at all — the one failure this route's
    skip-and-continue contract cannot express. `cards.card_data` is what keeps
    the scan on its feet; here it is end to end."""
    wid = _world(client)
    cid = _character(client, wid, "Mara")
    root = store.worlds.world_root(wid)
    (root / "characters" / cid / "main.json").write_text('{"data": ["speech"]}', encoding="utf-8")
    fake = _answers(client, ["A courier with cold hands."])

    resp, frames = _derive(client, wid)

    assert resp.status_code == 200
    assert fake.calls == 1
    assert _tagline(client, wid, cid) == "A courier with cold hands."
    assert frames[-1]["summary"] == {"total": 1, "written": 1, "skipped": 0, "stopped": False}
