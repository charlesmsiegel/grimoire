"""Sprint 110 in the app: the review API as the ``imports.*`` adapter ops (108's seam) and the Import page.

Over AppApi, as the app server calls it: a change set reviewed in one request is approved in the next, a refusal
keeps the review API's own reason and folds its details into the message, and the page's manifest needs only
ops the app serves. Every fixture is synthetic; no model is called.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from card_fixtures import V3
from changeset_fixtures import move_the_guild, seed_world
from grimoire.app import AppServer
from grimoire.app.adapters.imports import OPS, review_api
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.app.server import STATIC
from grimoire.changesets import encode as encode_changeset
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.textapi import ApiRefused
from png_fixtures import png, png_card
from review_fixtures import secured
from store_helpers import owner_handle
from test_reviewapi import files_of
from textapi_fixtures import Device

JS = Path(__file__).with_name("js")


@pytest.fixture
def app(tmp_path):
    folder = secured(tmp_path / "lib", seed_world)
    with Device(folder) as device:
        yield AppApi(device.api), device, folder


def _document(device) -> dict:
    # drafted with the plain owner handle: a draft writes nothing, it is only the document a person pastes
    return encode_changeset(move_the_guild(ChangeSetLibrary(device.api.store, owner_handle())))


def test_every_review_op_is_an_imports_op_the_library_lists(app):
    api, _device, _folder = app
    listed = api.call("library", {})["ops"]
    assert {f"imports.{op}" for op in OPS} <= set(listed)
    found = api.call("imports.capabilities", {})
    assert set(found["ops"]) == {f"imports.{op}" for op in OPS}
    assert found["unavailable"] and all(row["blocked_by"] for row in found["unavailable"])


def test_a_change_set_reviewed_in_one_request_is_approved_in_the_next(app):
    api, device, folder = app
    before = files_of(folder)
    proposal = api.call("imports.changeset_review", {"changeset": _document(device)})
    assert proposal["kind"] == "changeset" and proposal["writes"] > 0 and files_of(folder) == before
    assert review_api(api.context) is review_api(api.context)  # one review API per open library
    applied = api.call("imports.proposal_apply", {"proposal": proposal["proposal"], "digest": proposal["digest"]})
    assert applied["state"] == "applied"
    after = files_of(folder)
    for row in proposal["files"]:
        assert row["path"] in after


def test_a_refusal_keeps_its_reason_and_says_its_details(app):
    api, device, _folder = app
    proposal = api.call("imports.changeset_review", {"changeset": _document(device)})
    with pytest.raises(ApiRefused) as refused:
        api.call("imports.proposal_apply", {"proposal": proposal["proposal"], "digest": "sha256:" + "0" * 64})
    assert refused.value.reason == "stale_approval" and refused.value.status == 409
    with pytest.raises(ApiRefused) as refused:
        api.call("imports.card_preview", {"card": "UEsDBHJlc3Q="})  # a CHARX (zip) container
    assert refused.value.reason == "unsupported_format" and refused.value.status == 400
    assert "charx" in str(refused.value)
    with pytest.raises(ApiRefused) as refused:
        api.call("imports.proposal_get", {"proposal": "p-nowhere"})
    assert refused.value.reason == "no_proposal" and refused.value.status == 404


def test_the_import_page_is_registered_on_its_reserved_route_with_every_op_it_needs(app):
    api, _device, _folder = app
    [page] = [page for page in load_pages(STATIC) if page.id == "import"]
    assert page.route == "import" and page.owner == "110"
    shown = page.to_json(api.ops)
    assert not shown.get("missing")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_import_page_reviews_approves_and_writes_a_change_set_end_to_end(app, tmp_path):
    _api, device, folder = app
    document = tmp_path / "changeset.json"
    document.write_text(json.dumps(_document(device)), encoding="utf-8")
    before = files_of(folder)
    server = AppServer(device.api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.address
        result = subprocess.run([shutil.which("node"), "--test", "--test-reporter=tap", str(JS / "import.e2e.mjs")],
                                capture_output=True, text=True, timeout=180, check=False,
                                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": server.token,
                                     "GRIMOIRE_CHANGESET": str(document)})
    finally:
        server.close()
        thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
    assert files_of(folder) != before  # the page's approval wrote into the library


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_import_page_finds_a_card_and_says_every_review_state(app, tmp_path):
    _api, device, folder = app
    card = tmp_path / "mara.json"
    card.write_text(json.dumps(V3), encoding="utf-8")
    (tmp_path / "mara.png").write_bytes(png_card(V3))
    (tmp_path / "picture.png").write_bytes(png())
    before = files_of(folder)
    server = AppServer(device.api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.address
        result = subprocess.run([shutil.which("node"), "--test", "--test-reporter=tap", str(JS / "import_card.e2e.mjs")],
                                capture_output=True, text=True, timeout=180, check=False,
                                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": server.token,
                                     "GRIMOIRE_CARD": str(card), "GRIMOIRE_CARD_PNG": str(tmp_path / "mara.png"),
                                     "GRIMOIRE_PNG": str(tmp_path / "picture.png")})
    finally:
        server.close()
        thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
    # the page wrote nothing itself: only the approval made beside it (the stale case) reached the library
    written = set(files_of(folder)) - set(before)
    cards = {path for path in written if path.startswith("sources/card")}
    assert cards and all(path == "sources/card" or path.startswith("sources/card/mara-vell") for path in cards)
