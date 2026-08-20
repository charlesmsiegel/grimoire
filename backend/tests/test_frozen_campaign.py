"""The frozen-campaign regression harness (#205).

`tests/fixtures/frozen_campaign/home/` is a complete grimoire store — one
world, one campaign, two scenes — frozen as it was written to disk. Every test
here copies it into `tmp_path` first, so the checked-in tree is never the thing
under test's working copy and a test that writes cannot corrupt it.

What it buys that the rest of the suite does not: every other backend test
builds its store *with the code being tested*, in the same process, seconds
before reading it. That can never catch a change that breaks reading data an
older version wrote — the fixture would simply be written in the new shape too.
This one is the only store in the repository that today's code did not write.

Three different kinds of assertion live here, deliberately:

- **The snapshot compare** (`snapshot.json`) — broad, mechanical, catches any
  drift in any read at all, and is regenerated deliberately when a change is
  reviewed as correct. See `fixtures/frozen_campaign/sweep.py`.
- **The semantic assertions** — narrow, hand-written, and *not* derived from
  the snapshot. They are what stops a regeneration from making the harness
  vacuous: regenerate `snapshot.json` from a broken build and these still fail.
- **The tree digests** — a read must not write, and a migration must be
  idempotent. Neither is visible in the sweep's output at all.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire import routes, store
from grimoire.main import create_app
from tests.fixtures.frozen_campaign import build
from tests.fixtures.frozen_campaign import sweep as frozen
from tests.llm_fakes import from_cassette

# What the fixture was built with; asserted rather than discovered, so a fixture
# swapped out from under these tests fails loudly instead of testing something
# else. `LEGACY_SID` is the file's pre-migration stem, `MIGRATED_SID` what
# `migrate_scene_ids()` must rename it to.
CAMPAIGN = "the-drowned-ledger"
SCENE = "001--the-tide-comes-in"
LEGACY_SID = "2026-01-02-the-long-quay"
MIGRATED_SID = "002--2026-01-02--the-long-quay"


@pytest.fixture
def frozen_home(monkeypatch, tmp_path):
    """A private copy of the frozen store, rooted at GRIMOIRE_HOME."""
    home = tmp_path / "home"
    shutil.copytree(frozen.HOME, home)
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    return home


def _digest(home: Path) -> dict[str, str]:
    """Every entry's content hash, keyed by store-relative path.

    Directories are recorded too, with a sentinel in place of a hash. An empty
    directory is observable store state rather than nothing — actor-id
    allocation treats an existing actor directory as taken (`characters.
    create_character`'s `uniquify`), so a read that left one behind would change
    what the next write is allowed to be called while every file hash stayed
    put."""
    return {p.relative_to(home).as_posix():
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
            for p in sorted(home.rglob("*"))}


def _snapshot() -> dict:
    return json.loads(frozen.SNAPSHOT.read_text(encoding="utf-8"))


#: How many changed sections get a line-level diff before the report stops
#: expanding them. A refactor that moves every section would otherwise print
#: tens of thousands of lines, which is the same as printing nothing.
DETAILED_SECTIONS = 5


def _report(expected: dict, actual: dict) -> str:
    """A diff a human can act on: which sections moved, and for the text-heavy
    ones (the assembled prompt) which *lines* moved. A bare `assert a == b` on
    a 1000-line snapshot prints something nobody reads."""
    lines: list[str] = []
    detailed = 0
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            lines.append(f"- section disappeared: {key}")
        elif key not in expected:
            lines.append(f"- section is new (regenerate the snapshot?): {key}")
        elif expected[key] != actual[key]:
            lines.append(f"- section changed: {key}")
            detailed += 1
            if detailed > DETAILED_SECTIONS:
                continue
            lines += ["    " + d for d in difflib.unified_diff(
                json.dumps(expected[key], indent=2, sort_keys=True).splitlines(),
                json.dumps(actual[key], indent=2, sort_keys=True).splitlines(),
                fromfile="snapshot", tofile="now", lineterm="", n=2)]
    if detailed > DETAILED_SECTIONS:
        lines.append(f"({detailed - DETAILED_SECTIONS} further changed sections not expanded)")
    return "\n".join(lines)


def test_the_frozen_campaign_still_reads_the_way_it_was_recorded(frozen_home):
    store.migrations.migrate_scene_ids()
    actual = frozen.sweep(frozen_home)
    expected = _snapshot()
    if actual != expected:
        # pytest.fail, not `assert a == b`: the built-in comparison would print
        # both 1000-line snapshots and bury the diff that says what moved.
        pytest.fail(
            "the frozen campaign no longer reads as recorded:\n"
            + _report(expected, actual)
            + "\n\nIf the new output is CORRECT and reviewed, regenerate with\n"
              "  PYTHONPATH=backend/src backend/.venv/bin/python "
              "-m tests.fixtures.frozen_campaign.sweep\n"
              "(from backend/) and commit the snapshot alongside the change that moved it.",
            pytrace=False)


def test_the_sweep_covers_the_whole_store_not_a_corner_of_it(frozen_home):
    """A sweep that quietly stopped calling anything would still match a
    snapshot regenerated from it, so the shape of the sweep is asserted here
    rather than left to the snapshot."""
    store.migrations.migrate_scene_ids()
    swept = frozen.sweep(frozen_home)
    modules = {key.split(".", 1)[0] for key in swept}
    assert modules >= {"worlds", "campaigns", "characters", "entities", "greetings",
                       "overlay", "scenes", "appearances", "chronicle", "plot",
                       "commitments", "relationships", "dossiers", "sheets",
                       "checks", "context", "tags", "modules"}
    assert len(swept) >= 50


def test_the_legacy_scene_is_migrated_and_every_reference_follows(frozen_home):
    """The fixture's second scene is stored under the pre-migration real-date
    grammar. Migrating it renames the file *and* repoints every persisted
    reference — the half that is easy to break and invisible until a campaign
    loses its chronicle."""
    scenes = store.scenes.list_scenes(CAMPAIGN)
    assert LEGACY_SID in {s["id"] for s in scenes}, "fixture is not pre-migration"

    store.migrations.migrate_scene_ids()

    ids = {s["id"] for s in store.scenes.list_scenes(CAMPAIGN)}
    assert LEGACY_SID not in ids and MIGRATED_SID in ids
    assert store.scenes.read_scene(CAMPAIGN, MIGRATED_SID)["meta"]["title"] == "The Long Quay"
    # every reference that named the old stem
    assert MIGRATED_SID in store.chronicle.read_chronicle(CAMPAIGN)
    assert LEGACY_SID not in store.chronicle.read_chronicle(CAMPAIGN)
    beats = {b["scene"] for t in store.plot.read(CAMPAIGN).values() for b in t["beats"]}
    assert MIGRATED_SID in beats and LEGACY_SID not in beats
    cast = store.appearances.scene_cast(CAMPAIGN, MIGRATED_SID)
    assert [a["id"] for a in cast] == ["mara"]


def test_migrating_a_second_time_changes_nothing(frozen_home):
    store.migrations.migrate_scene_ids()
    after_first = _digest(frozen_home)
    store.migrations.migrate_scene_ids()
    assert _digest(frozen_home) == after_first


def test_the_read_only_sweep_writes_nothing(frozen_home):
    """Reads that quietly write are how a "just look at it" endpoint ends up
    rewriting a user's transcript. The migration is allowed to write; nothing
    after it is."""
    store.migrations.migrate_scene_ids()
    before = _digest(frozen_home)
    frozen.sweep(frozen_home)
    after = _digest(frozen_home)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert not changed, f"the read-only sweep wrote to: {sorted(changed)}"


def test_the_digest_notices_a_bare_directory(frozen_home):
    """Teeth for the assertion above: an empty directory changes no file hash,
    and is still a write — it is enough to make the next character created here
    get a different id."""
    before = _digest(frozen_home)
    (frozen_home / "worlds" / "saltmarch" / "characters" / "seraphine-2").mkdir()
    assert _digest(frozen_home) != before


def test_the_assembled_prompt_still_carries_the_campaign_state(frozen_home):
    """Every layer the context assembler is supposed to fold in, asserted by
    content rather than by snapshot equality — so a snapshot regenerated from a
    build that dropped a section cannot make this pass."""
    store.migrations.migrate_scene_ids()
    system = store.context.build_messages(CAMPAIGN, SCENE)[0]["content"]

    assert "The drowned keeper of the tide ledger." in system      # NPC card
    assert "Winifred, she/her" in system                           # PC persona
    assert "Winifred → Seraphine" in system                        # relationships
    assert "Winifred touched the ledger." in system                # chronicle
    assert "The debt in salt (open)" in system                     # plot threads
    assert "Bring salt to the keep" in system                      # commitments
    assert "A stone keep the tide reaches twice a day." in system  # current location
    assert "Debts written in salt" in system                       # world lore
    assert "hp 9/12" in system                                     # module sheet
    assert "athletics (Athletics)" in system                       # module checks


def test_owned_lore_stays_in_the_scene_its_owner_is_in(frozen_home):
    """The fixture's campaign lore is owned by the PC. Containment is a
    correctness property of the assembler, not a rendering detail: leaking it
    into a scene the owner is absent from tells the model a secret the story
    has not reached yet."""
    store.migrations.migrate_scene_ids()
    owned = "She signed the ledger in salt"
    with_owner = store.context.build_messages(CAMPAIGN, SCENE)[0]["content"]
    without_owner = store.context.build_messages(CAMPAIGN, MIGRATED_SID)[0]["content"]
    assert owned in with_owner
    assert owned not in without_owner


def test_the_fixture_tree_carries_no_api_key(frozen_home):
    """The frozen store includes `llm_connections/`, which is where a key would
    live. A fixture minted from a scratch home has none; this fails if one is
    ever committed by a rebuild against a configured store."""
    for conn in store.llm_connections.list_connections():
        assert not conn.get("key_set"), f"{conn['id']} carries an API key"


def test_the_builder_still_mints_a_fixture(monkeypatch, tmp_path):
    """`build.py` is the fixture's provenance, and a script no test runs is a
    script that quietly stops working — after which "this is how `home/` was
    made" is a claim nobody can check. This does not rebuild `home/` (that must
    never happen) and does not compare against it: it mints a *fresh* tree in
    tmp_path and asserts the store can read it back.

    Nor could it compare: a rebuild is deliberately not byte-identical — sheet
    `gen` tokens and connection `rev` stamps are minted per write."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(store.paths, "datetime", build.Clock())
    store.ensure_home()

    ids = build.build(store)

    assert store.worlds.read_world(ids["world"])["meta"]["name"] == "Saltmarch"
    assert store.campaigns.read_campaign(ids["campaign"])["meta"]["module"] == "d20-basic"
    scene_ids = {s["id"] for s in store.scenes.list_scenes(ids["campaign"])}
    assert scene_ids == {ids["scene"], build.LEGACY_SID}


# ---- the frozen campaign, played through the app (#205 + #204) -------------
# The sweep above never calls a provider. These do — through the app's own
# routes, against the shared cassette fake, which is the only way to cover the
# generating half of the store on a frozen campaign without spending money or
# depending on a model.

@pytest.fixture
def frozen_client(frozen_home):
    """The real app over the frozen store, with the cassette standing in for
    the gateway at the same DI seam production uses. The fake is hung off the
    client as `.llm` so a test can assert on the request the routes built."""
    # The real app migrates at startup; TestClient without a `with` block never
    # runs the lifespan, so the migration is run here instead of leaving these
    # tests on a tree the app would never actually serve.
    store.migrations.migrate_scene_ids()
    app = create_app()
    fake = from_cassette("campaign_flow")
    app.dependency_overrides[routes.get_llm] = lambda: fake
    client = TestClient(app)
    # The frozen tree carries no key (asserted above); the chat route refuses
    # to call a provider without one, so the test supplies its own.
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-test"})
    client.llm = fake
    return client


def test_a_turn_played_on_the_frozen_campaign_streams_and_persists(frozen_client):
    before = len(frozen_client.get(f"/api/campaigns/{CAMPAIGN}/scenes/{SCENE}").json()["messages"])
    resp = frozen_client.post(f"/api/campaigns/{CAMPAIGN}/scenes/{SCENE}/chat",
                              json={"content": "I have brought the salt."})
    assert resp.status_code == 200
    assert '"delta"' in resp.text and 'data: {"done": true}' in resp.text

    messages = frozen_client.get(f"/api/campaigns/{CAMPAIGN}/scenes/{SCENE}").json()["messages"]
    assert messages[before] == {"role": "user", "content": "I have brought the salt.",
                                "speaker": "Winifred"}
    # The reply is stored one message per speaker block, so assert on the text
    # rather than on a count the serializer owns.
    reply = "\n".join(m["content"] for m in messages[before + 1:])
    assert "Salt first" in reply and "over the lower step" in reply
    assert [m["role"] for m in messages[before + 1:]] == ["assistant"] * (len(messages) - before - 1)
    # and the request the route built carried the frozen campaign's own state
    system = frozen_client.llm.messages[0]["content"]
    assert "The drowned keeper of the tide ledger." in system
    assert "Winifred touched the ledger." in system


def test_absorbing_a_frozen_scene_stages_the_movements_it_reports(frozen_client):
    """End to end on a campaign this process did not create: the absorb pass
    reads the frozen chronicle and plot, the cassette answers every call it
    makes, and the movements come back as edits against the frozen ids."""
    body = frozen_client.post(f"/api/campaigns/{CAMPAIGN}/scenes/{SCENE}/absorb").json()
    assert body["one_line"] == "Winifred paid the salt she owed."
    ids = {e["id"] for e in body["edits"]}
    assert "plot:the-debt" in ids
    assert "commitment:salt-owed" in ids
    # Absorb stages; it must not have written the movements yet.
    assert store.plot.get(CAMPAIGN, "the-debt")["status"] == "open"

