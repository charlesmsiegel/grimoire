"""Sprint 115's Story page and its ``story.*`` adapter ops, through 108's shell contract.

The adapter serves 049's semantic reconciliation and 054's continuity review over the app's open library. The
proving scenario runs as a screen would, on two synthetic paired devices with their own durable authorities: the
desktop records a scene and an accepted payment resting on it, syncs, writes a later line of dialogue, while the
phone edits the same scene offline. A sync from the app holds the scene and its ledger for semantic review and
keeps both versions; the director sees both, sees that keeping one side is refused and why, previews what a retcon
of the scene would touch, and records (reviewed first, made once) that the dialogue rests on the payment. The
scene is then restored as a retained fork by 054's owner operation, which needs both libraries open on one
machine and so runs outside the app; the next sync from the app brings the payment across exactly once, and the
page shows the fork reconciled. The page itself runs end to end under Node (``js/story.e2e.mjs``). Synthetic
libraries; no model is called.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from effects_fixtures import CHAT
from ledger_fixtures import build_base, pay
from peersync_helpers import DESKTOP, OWNER_SCOPES, PHONE, Device, serving
from store_helpers import OWNER
from test_effect_continuity import write_turn
from test_peersync_ledger import _accept

from grimoire.app import AppServer
from grimoire.app.adapters import story as story_adapter
from grimoire.app.adapters.chronicle import FEATURES
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.continuitystore import resolve_fork
from grimoire.core.identity import ChatRef, ContinuityRef
from grimoire.peersync import new_pairing_key
from grimoire.peersync import pair as make_pair
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, ScopeRecord, etag
from grimoire.store.jsonfmt import thaw
from grimoire.sync import identify, plan_sync, semantic
from grimoire.testing.fileops import snapshot
from grimoire.textapi import ApiRefused, TextApi

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
DIRECTOR = {"audience": "director"}
SCENE = "campaigns/c/turns/t-1.000001.json"
LATER = "campaigns/c/turns/t-1.000002.json"
GIVEN = "Wren gives Tam the iron key."
DESTROYED = "The key was destroyed."
DIALOGUE = "Tam says: I will pay Ines with the coins."


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def handles(pair) -> dict:
    return {name: device.handle for name, device in pair.items()}


@pytest.fixture
def pair(tmp_path):
    """Two paired devices of the synthetic harbor campaign that agreed on the desktop's scene. Then the desktop
    accepted Tam's payment as a turn effect resting on that scene and wrote a later line of dialogue, and the phone
    edited the scene offline. Nothing has synced since."""
    base = build_base(tmp_path)
    made = {}
    for name in (DESKTOP, PHONE):
        shutil.copytree(base, tmp_path / name)
        with LibraryStore.open(tmp_path / name) as store:
            identify(store, name)
        device = Device(tmp_path, name, create=False)
        for number, scope in enumerate(OWNER_SCOPES):
            device.authority.add_grant(Grant(f"owner-{number}", OWNER, frozenset({Permission.APPLY}),
                                             ResourcePath.parse(scope), OWNER))
        made[name] = device
    key = new_pairing_key()
    make_pair(made[DESKTOP].store, made[DESKTOP].authority, PHONE, key, granted_by=OWNER)
    make_pair(made[PHONE].store, made[PHONE].authority, DESKTOP, key, granted_by=OWNER)
    desk, phone = made[DESKTOP], made[PHONE]
    write_turn(desk.store, SimpleNamespace(app=desk.handle), text=GIVEN)
    with serving(phone) as server:
        app = AppApi(TextApi(desk.store, desk.authority, owner=OWNER, peers={PHONE: server.address}))
        assert app.call("media.sync", {"peer": PHONE})["status"] == "synced"
    _accept(desk, pay(chat=CHAT))
    write_turn(desk.store, SimpleNamespace(app=desk.handle), number=2, text=DIALOGUE)
    write_turn(phone.store, SimpleNamespace(app=phone.handle), text=DESTROYED)
    yield made
    for device in made.values():
        device.close()


@pytest.fixture
def desktop(pair):
    """The desktop's app with the phone served to it on loopback."""
    with serving(pair[PHONE]) as server:
        yield AppApi(TextApi(pair[DESKTOP].store, pair[DESKTOP].authority, owner=OWNER, peers={PHONE: server.address}))


def accepted(device) -> int:
    return len(device.store.campaign_accepted("c").records)


def scenes(app: AppApi) -> dict:
    return app.call("story.scenes", {"campaign": "c", **DIRECTOR})


def pin(view: dict, path: str) -> dict:
    record = next(item for item in view["records"] if item["path"] == path)
    return {"path": record["path"], "revision": record["revision"]}


def payment(view: dict) -> str:
    return next(item["path"] for item in view["records"] if item["kind"] == "accepted_event")


def test_the_page_registers_hidden_on_its_own_route_and_the_chronicle_links_it():
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["story"]
    assert (page.route, page.slot, page.owner) == ("story", "hidden", "115")
    assert set(page.requires) == {"campaign.chats", "story.divergent", "story.scenes", "story.preview"}
    rows = {item["slice"]: item for item in FEATURES}
    assert rows["049"]["status"] == rows["054"]["status"] == "screen"
    assert rows["049"]["route"] == rows["054"]["route"] == "story"


def test_every_op_is_the_directors(desktop):
    before = snapshot(desktop.api.store.root)
    for op, args in (("story.divergent", {}), ("story.scenes", {}),
                     ("story.preview", {"action": "edit", "roots": [{"path": SCENE, "revision": "sha256:" + "0" * 64}]}),
                     ("story.declare_plan", {"operation_id": "x"}), ("story.declare_commit", {"operation_id": "x"})):
        assert refused(desktop, op, campaign="c", **args).reason == "director_only"
        assert refused(desktop, op, campaign="c", audience="table", **args).reason == "director_only"
        assert refused(desktop, op, campaign="c", audience="everyone", **args).reason == "invalid_field"
    assert refused(desktop, "story.scenes", campaign="nowhere", **DIRECTOR).reason == "no_campaign"
    assert refused(desktop, "story.scenes", campaign="../c", **DIRECTOR).reason == "invalid_field"
    assert snapshot(desktop.api.store.root) == before


def test_a_divergent_scene_keeps_both_versions_and_keeping_one_side_is_refused(pair, desktop):
    quiet = desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})
    assert quiet["scenes"] == [] and quiet["peers"] == [] and quiet["retained"] == []
    report = desktop.call("media.sync", {"peer": PHONE})
    assert report["status"] == "conflicts"
    before = snapshot(pair[DESKTOP].store.root)
    found = desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})
    assert snapshot(pair[DESKTOP].store.root) == before  # reading writes nothing
    (scene,) = found["scenes"]
    assert scene["path"] == SCENE and scene["number"] == 1 and scene["chat"] == "campaign:c#chat/t-1"
    assert scene["mine"]["said"] == GIVEN
    assert [(item["said"], item["peers"]) for item in scene["theirs"]] == [(DESTROYED, [PHONE])]
    assert scene["keep_one"] == "refused" and scene["detected"] == [PHONE]
    (review,) = scene["taking_theirs"]
    assert review["reasons"] == ["accepted_scene_changed"] and review["uncertain"] is False
    assert scene["uncertain"] is False and found["ancestry"] == []
    assert scene["continues"] is None
    # the ledger the scene holds back is listed, and was not brought across: no effect is applied twice
    assert {item["kind"] for item in found["held"]} == {"accepted_event", "holdings"}
    assert accepted(pair[DESKTOP]) == 1 and accepted(pair[PHONE]) == 0
    assert found["peers"] == [{"peer": PHONE, "status": "conflicts", "held": sorted(
        [SCENE, *(item["path"] for item in found["held"])])}]
    assert found["unavailable"][0]["what"] == "Restore one device's scene as a retained fork"
    # and the ordinary resolver the inbox offers refuses it too, keeping both
    conflict = next(item for item in desktop.call("media.conflicts", {})["conflicts"] if item["path"] == SCENE)
    result = desktop.call("media.resolve", {"peer": PHONE, "path": SCENE, "keep": "mine", "mine": conflict["mine"]["etag"],
                                            "theirs": [item["etag"] for item in conflict["theirs"]]})
    assert result["status"] == "refused"
    assert len(desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["scenes"]) == 1


def legacy_history(pair) -> None:
    """The phone continues the chat offline after its edit, and the desktop's payment loses its record of the
    scenes it rests on (as one accepted before scenes were pinned): its ancestry is not on record."""
    phone = pair[PHONE]
    write_turn(phone.store, SimpleNamespace(app=phone.handle), number=2, text="Tam mourns the key.")
    store = pair[DESKTOP].store
    (ref, stored), = store.campaign_accepted("c").records.items()
    assert "continuity_sources" in stored.record.payload
    legacy = {key: value for key, value in thaw(stored.record.payload).items() if key != "continuity_sources"}
    txn = store.transaction()
    txn.put(ScopeRecord(ref, stored.record.schema, legacy, owner=stored.record.owner), stored.etag)
    txn.commit(pair[DESKTOP].handle)


def test_a_divergent_continuation_and_an_uncertain_ancestry_show_as_refused(pair, desktop):
    legacy_history(pair)
    assert desktop.call("media.sync", {"peer": PHONE})["status"] == "conflicts"
    found = {item["path"]: item for item in desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["scenes"]}
    assert set(found) == {SCENE, LATER}
    assert found[LATER]["continues"] == SCENE and found[SCENE]["continues"] is None
    assert found[LATER]["mine"]["said"] == DIALOGUE and found[LATER]["theirs"][0]["said"] == "Tam mourns the key."
    for item in found.values():
        assert item["keep_one"] == "refused"
        assert item["uncertain"] is True
        # 049's admission check sees nothing unmet: the uncertainty is the snapshot's, so the page must read
        # `uncertain` (and `ancestry`), never the per-version review alone (the e2e "uncertain" phase shows it)
        assert [(review["reasons"], review["uncertain"]) for review in item["taking_theirs"]] == [([], False)]
    (problem,) = desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["ancestry"]
    assert "/accepted/" in problem and "scene provenance" in problem


def test_taking_another_devices_deletion_is_reviewed_and_titled_by_who_deleted(pair, desktop, monkeypatch):
    """A kept deletion is listed under the devices that deleted it, and taking it here is 049's own admission
    check with the file removed. (A turn cannot be deleted in this store, so the conflict is listed as Devices
    and sync would list one for a deleted record; the review runs over the real library.)"""
    real = story_adapter.media_of

    class Listed:
        def __init__(self, api):
            self.media = real(api)

        def call(self, op, args):
            answer = self.media.call(op, args)
            if op != "sync.conflicts":
                return answer
            mine = pair[DESKTOP].store.read_exact(SCENE)
            item = {"path": SCENE, "campaign": "c", "kind": "turn", "their_deletion": True, "theirs": [],
                    "mine": {"etag": etag(mine), "deleted": False, "peers": [], "preview": {}},
                    "peers": [{"peer": PHONE, "versions": [], "deleted": True}]}
            return {**answer, "conflicts": [*answer["conflicts"], item]}

    monkeypatch.setattr(story_adapter, "media_of", Listed)
    before = snapshot(pair[DESKTOP].store.root)
    (scene,) = desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["scenes"]
    assert scene["their_deletion"] is True and scene["deleted_by"] == [PHONE] and scene["theirs"] == []
    review = scene["taking_deletion"]
    assert "accepted_scene_missing" in review["reasons"] and review["uncertain"] is True
    assert scene["uncertain"] is True  # a deletion that leaves an accepted event without its scene is refused
    assert snapshot(pair[DESKTOP].store.root) == before


def test_the_revision_shown_is_of_the_records_once_the_chats_are_open(pair, desktop):
    """Opening a chat may write an accepted effect's pending 020 knowledge, which 054 pins. The revision a page is
    shown must be the one a review is then checked against, or the first review would always be stale."""
    runner = desktop.api.runner
    original = runner.open
    wrote = []

    def opening(chat):
        if not wrote:  # what settling pending knowledge does: a pinned record written while the chat is opened
            wrote.append(write_turn(pair[DESKTOP].store, SimpleNamespace(app=pair[DESKTOP].handle),
                                    chat=ChatRef("c", "t-2"), text="A witness remembers the key."))
        return original(chat)

    runner.open = opening
    shown = scenes(desktop)
    assert wrote and "campaigns/c/turns/t-2.000001.json" in {item["path"] for item in shown["records"]}
    plan = desktop.call("story.declare_plan", {"campaign": "c", **DIRECTOR, "operation_id": "first",
                                                "expected": shown["etag"], "reason": "dialogue",
                                                "source": pin(shown, payment(shown)), "dependent": pin(shown, LATER)})
    assert plan["plan"]["already_applied"] is False
    assert scenes(desktop)["etag"] == shown["etag"]


def test_a_retcon_preview_names_what_rests_on_the_scene_and_writes_nothing(pair, desktop):
    view = scenes(desktop)
    assert view["problems"] == [] and view["coverage"] == "recorded_dependencies_only"
    labels = {item["path"]: item["label"] for item in view["records"]}
    assert labels[SCENE] == GIVEN and labels[LATER] == DIALOGUE and labels[payment(view)] == "tam pays ines."
    before = snapshot(pair[DESKTOP].store.root)
    for action in ("edit", "cut", "replace", "replay", "fork"):
        answer = desktop.call("story.preview", {"campaign": "c", **DIRECTOR, "action": action, "roots": [pin(view, SCENE)]})
        assert answer["complete"] and answer["written"] is False
        touched = {item["target"]["path"]: (item["reason"], item["certainty"]) for item in answer["impacts"]}
        assert touched[payment(view)] == ("scene", "known")
        assert touched["campaigns/c/holdings/character/tam.json"] == ("possession", "known")
        assert touched[LATER] == ("chronological", "potential")  # later in the chat: only possibly used it
        assert {item["dependent"] for item in answer["corrections"]} == set(touched)
    rehearsed = desktop.call("story.preview", {"campaign": "c", **DIRECTOR, "action": "replace", "roots": [pin(view, SCENE)],
                                               "alternative": "Wren keeps the key."})
    assert rehearsed["rehearsal"] == {"status": "speculative", "model_cost": 0, "alternatives": ["Wren keeps the key."]}
    assert snapshot(pair[DESKTOP].store.root) == before
    # a record shown before it changed is refused, never previewed as it is now
    shown = pin(view, SCENE)
    write_turn(pair[DESKTOP].store, SimpleNamespace(app=pair[DESKTOP].handle), text="Wren gives Tam a key.")
    assert refused(desktop, "story.preview", campaign="c", **DIRECTOR, action="edit", roots=[shown]).reason == "changed"
    gone = {"path": "campaigns/c/turns/t-1.000009.json", "revision": shown["revision"]}
    assert refused(desktop, "story.preview", campaign="c", **DIRECTOR, action="edit", roots=[gone]).reason == "missing_record"
    for bad in ({"action": "rewrite", "roots": [shown]}, {"action": "edit", "roots": []},
                {"action": "edit", "roots": [{"path": SCENE}]}, {"action": "edit", "roots": [shown, shown]},
                {"action": "edit", "roots": [shown], "alternative": " "}, {"action": "edit", "roots": "everything"},
                {"action": "edit", "roots": [{"path": 7, "revision": shown["revision"]}]},
                {"action": "edit", "roots": [{"path": SCENE, "revision": None}]}, {"action": ["edit"], "roots": [shown]},
                {"action": "edit", "roots": [shown], "alternative": 3},
                {"action": "edit", "roots": [shown], "alternative": "x" * 16385},
                {"action": "edit", "roots": [{"path": "campaigns/c/turns/../../d/turns/t-1.000001.json", "revision": shown["revision"]}]},
                {"action": "edit", "roots": [{"path": "campaigns/other/turns/t-1.000001.json", "revision": shown["revision"]}]}):
        assert refused(desktop, "story.preview", campaign="c", **DIRECTOR, **bad).reason == "invalid_field", bad


def test_an_incomplete_snapshot_previews_with_its_problems_and_exports_no_correction(pair, desktop):
    store = pair[DESKTOP].store
    txn = store.transaction()
    txn.put(ScopeRecord(ContinuityRef("c", "newer"), "future.continuity/9", {}, owner=OWNER), None)
    txn.commit(pair[DESKTOP].handle)
    view = scenes(desktop)
    assert any("unsupported" in problem for problem in view["problems"])
    answer = desktop.call("story.preview", {"campaign": "c", **DIRECTOR, "action": "edit", "roots": [pin(view, SCENE)]})
    assert answer["complete"] is False and answer["corrections"] is None and "incomplete" in answer["corrections_refused"]
    plan = {"campaign": "c", **DIRECTOR, "operation_id": "dialogue-1", "reason": "dialogue", "expected": view["etag"],
            "source": pin(view, payment(view)), "dependent": pin(view, LATER)}
    assert refused(desktop, "story.declare_plan", **plan).reason == "incomplete_snapshot"
    # an unreadable continuity file is a problem of the answer, never a server error
    (store.root / "campaigns/c/continuity/newer.json").write_text("{broken")
    assert scenes(desktop)["problems"]
    assert desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["problems"]


def test_a_declaration_is_reviewed_then_made_once_and_a_stale_review_is_refused(pair, desktop):
    view = scenes(desktop)
    request = {"campaign": "c", **DIRECTOR, "operation_id": "dialogue-1", "reason": "dialogue", "expected": view["etag"],
               "source": pin(view, payment(view)), "dependent": pin(view, LATER)}
    assert refused(desktop, "story.declare_commit", **{**request, "expected": None}).reason == "review_required"
    before = snapshot(pair[DESKTOP].store.root)
    plan = desktop.call("story.declare_plan", request)["plan"]
    assert snapshot(pair[DESKTOP].store.root) == before
    assert plan["files"] == ["campaigns/c/continuity/dialogue-1.json"] and plan["already_applied"] is False
    assert [(item["target"]["path"], item["certainty"]) for item in plan["reaches"]] == [(LATER, "known")]

    done = desktop.call("story.declare_commit", request)
    assert done["done"]["already_applied"] is False
    assert {(item["source"], item["dependent"], item["reason"]) for item in done["scenes"]["dependencies"]} >= {
        (payment(view), LATER, "dialogue")}
    written = snapshot(pair[DESKTOP].store.root)
    # a commit retried after a lost answer finds its own record and writes nothing again
    again = desktop.call("story.declare_commit", request)
    assert again["done"]["already_applied"] is True and snapshot(pair[DESKTOP].store.root) == written
    assert desktop.call("story.declare_plan", request)["plan"]["already_applied"] is True
    # the same id for another declaration is refused
    other = {**request, "reason": "causal_action"}
    assert refused(desktop, "story.declare_commit", **other).reason == "operation_conflict"
    # a retcon of the scene now flags the dialogue as known, by way of the payment
    answer = desktop.call("story.preview", {"campaign": "c", **DIRECTOR, "action": "edit",
                                            "roots": [pin(done["scenes"], SCENE)]})
    later = next(item for item in answer["impacts"] if item["target"]["path"] == LATER)
    assert (later["reason"], later["certainty"]) == ("dialogue", "known")

    # reviewed, then overtaken: the reviewed declaration is refused and nothing is written
    fresh = scenes(desktop)
    stale = {**request, "operation_id": "dialogue-2", "reason": "asset", "expected": fresh["etag"],
             "source": pin(fresh, SCENE), "dependent": pin(fresh, LATER)}
    desktop.call("story.declare_plan", stale)
    write_turn(pair[DESKTOP].store, SimpleNamespace(app=pair[DESKTOP].handle), number=3, text="Clouds gather.")
    moved = snapshot(pair[DESKTOP].store.root)
    assert refused(desktop, "story.declare_commit", **stale).reason == "changed"
    assert snapshot(pair[DESKTOP].store.root) == moved
    # one that would make the dependence circular is refused by 054's own check
    now = scenes(desktop)
    circle = {**request, "operation_id": "circle", "expected": now["etag"], "source": pin(now, LATER),
              "dependent": pin(now, payment(now))}
    assert refused(desktop, "story.declare_plan", **circle).reason == "invalid_dependency"
    for bad in ({"reason": "chronological"}, {"reason": "scene"}, {"operation_id": "Not An Id"},
                {"dependent": request["source"]}, {"source": {"path": LATER}}):
        assert refused(desktop, "story.declare_plan", **{**request, **bad}).reason == "invalid_field", bad
    assert refused(desktop, "story.declare_plan", **{**request, "operation_id": "dup", "expected": now["etag"],
                                                     "source": pin(now, payment(now)), "dependent": pin(now, LATER)}).reason \
        == "already_declared"


def restore(pair, operation: str = "keep-desktop", token: str | None = None):
    """054's owner operation, run with both libraries open on this machine (the app cannot: it serves one), as
    ``(result, token)``. A retry names the review token the first run was made under."""
    hs = handles(pair)
    if token is None:
        plan = plan_sync(pair[DESKTOP].store, pair[PHONE].store, hs)
        (review,) = [item for item in semantic.reviews(plan) if item.campaign == "c"]
        token = review.token
    return resolve_fork(pair[DESKTOP].store, pair[PHONE].store, "c", operation, token, DESKTOP, hs,
                        {name: {OWNER} for name in pair}), token


def test_a_retained_fork_shows_both_originals_and_its_effects_reach_the_phone_once(pair, desktop):
    assert desktop.call("media.sync", {"peer": PHONE})["status"] == "conflicts"
    result, token = restore(pair)
    assert result.status == "pending_sync"
    # the desktop already holds what the fork predicts: its own scene, and the payment
    (fork,) = desktop.call("story.divergent", {"campaign": "c", **DIRECTOR})["retained"]
    assert fork["operation"] == "keep-desktop" and fork["source_device"] == DESKTOP and fork["reconciled_here"] is True
    with serving(pair[DESKTOP]) as server:
        phone = AppApi(TextApi(pair[PHONE].store, pair[PHONE].authority, owner=OWNER, peers={DESKTOP: server.address}))
        found = phone.call("story.divergent", {"campaign": "c", **DIRECTOR})
        (fork,) = found["retained"]
        assert fork["reconciled_here"] is False  # the phone still waits for ordinary sync to bring the payment
        (scene,) = fork["scenes"]
        assert scene["path"] == SCENE and scene["here"] == "restored"
        assert {(item["device"], item["said"], item["selected"]) for item in scene["variants"]} == {
            (DESKTOP, GIVEN, True), (PHONE, DESTROYED, False)}
        assert accepted(pair[PHONE]) == 0
        report = phone.call("media.sync", {"peer": DESKTOP})
        assert report["status"] == "synced", report
        after = phone.call("story.divergent", {"campaign": "c", **DIRECTOR})
        assert after["scenes"] == [] and after["retained"][0]["reconciled_here"] is True
    assert accepted(pair[DESKTOP]) == accepted(pair[PHONE]) == 1
    assert restore(pair, token=token)[0].status == "already_applied"
    assert accepted(pair[DESKTOP]) == accepted(pair[PHONE]) == 1


def _node(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / script)],
                          capture_output=True, text=True, timeout=240, check=False, env={**os.environ, **env})


@contextlib.contextmanager
def app_of(pair, name: str, peer: str):
    """``name``'s library served by the app, with ``peer``'s served to it on loopback: the app's address and token."""
    with serving(pair[peer]) as server:
        app = AppServer(TextApi(pair[name].store, pair[name].authority, owner=OWNER, peers={peer: server.address}))
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            yield {"GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token, "GRIMOIRE_CHAT": str(CHAT),
                   "GRIMOIRE_CAMPAIGN": "c", "GRIMOIRE_PEER": peer, "GRIMOIRE_DEVICE": name}
        finally:
            app.close()
            thread.join(10)


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_story_page_shows_an_uncertain_ancestry_as_refused(pair):
    # the real server's answer for a legacy payment and a divergent continuation, drawn by the real page
    legacy_history(pair)
    with app_of(pair, DESKTOP, PHONE) as env:
        ran = _node("story.e2e.mjs", {**env, "GRIMOIRE_PHASE": "uncertain"})
    assert ran.returncode == 0, ran.stdout[-6000:] + ran.stderr[-3000:]
    assert "# pass 1" in ran.stdout


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_story_page_end_to_end(pair):
    # the desktop's app: sync, see both versions kept and why, preview the retcon, record what rests on what
    with app_of(pair, DESKTOP, PHONE) as env:
        first = _node("story.e2e.mjs", {**env, "GRIMOIRE_PHASE": "review"})
    assert first.returncode == 0, first.stdout[-6000:] + first.stderr[-3000:]
    assert "# pass 1" in first.stdout
    assert accepted(pair[DESKTOP]) == 1 and accepted(pair[PHONE]) == 0  # held: neither applied twice nor lost
    # the desktop's scene restored as a retained fork, by 054's owner operation with both libraries open here
    assert restore(pair)[0].status == "pending_sync"
    # the phone's app: the retained fork waits on sync; one sync from the app brings the payment across, once
    with app_of(pair, PHONE, DESKTOP) as env:
        second = _node("story.e2e.mjs", {**env, "GRIMOIRE_PHASE": "restored"})
    assert second.returncode == 0, second.stdout[-6000:] + second.stderr[-3000:]
    assert "# pass 1" in second.stdout
    assert accepted(pair[DESKTOP]) == accepted(pair[PHONE]) == 1
