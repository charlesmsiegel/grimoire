"""#136: text becomes a proposal; only the existing create route saves it."""

import json

import pytest

from grimoire import routes
from grimoire.store import _plaintext, paths
from tests import draft_runs
from tests.llm_fakes import FakeOpenRouterComplete

SOURCE = """Name: Seraphine
Appearance: A weathered cloak.
Personality: Patient.
Scenario: Waiting at Saltmarch.
Greeting: {{char}} waves to {{user}}.
Example dialogue:
{{user}}: Hello.
{{char}}: Welcome.
"""


@pytest.fixture
def world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def test_parse_then_reviewed_save_bakes_only_the_final_name(client, world):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": SOURCE})
    assert response.status_code == 200, response.text
    card = response.json()["card"]
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["description"] == "A weathered cloak."
    assert card["data"]["personality"] == "Patient."
    assert card["data"]["scenario"] == "Waiting at Saltmarch."
    assert card["data"]["first_mes"] == "{{char}} waves to {{user}}."
    assert card["data"]["mes_example"] == "{{user}}: Hello.\n{{char}}: Welcome."
    assert client.get(f"/api/worlds/{world}/characters").json() == []
    # Review can correct the name before macros are resolved (#137).
    card["data"]["name"] = "Mara"
    saved = client.post(f"/api/worlds/{world}/characters", json={"name": "Mara", "card": card})
    assert saved.status_code == 200, saved.text
    ids = saved.json()
    detail = client.get(f"/api/worlds/{world}/characters/{ids['character']}").json()
    stored = next(v["card"] for v in detail["versions"] if v["id"] == ids["version"])
    assert stored["data"]["first_mes"] == "Mara waves to {{user}}."
    duplicate = client.post(f"/api/worlds/{world}/characters", json={"name": "Mara", "card": card})
    assert duplicate.status_code == 409


@pytest.mark.parametrize(("text", "filename", "name", "description", "personality"), [
    ("\n# Seraphine\n\n## Personality\nPatient.\n\n## Appearance\nA cloak.", "", "Seraphine", "# Seraphine\n\nA cloak.", "Patient."),
    ("**Name:** Mara\n**Personality:** Reserved.\n## Notes\nKeeps a ledger.", "", "Mara", "## Notes\nKeeps a ledger.", "Reserved."),
    ("Name:\nWinifred\nKeeps a ledger.", "", "Winifred", "Keeps a ledger.", ""),
    ("A patient keeper.\nCarries a ledger.", "Seraphine.md", "Seraphine", "A patient keeper.\nCarries a ledger.", ""),
    ("Mara\nA patient keeper.", "", "Mara", "Mara\nA patient keeper.", ""),
    ("Name: Seraphine\nDescription: Keeps a ledger.\nAppearance: A cloak.\nDescription: Counts the tide.", "", "Seraphine", "Keeps a ledger.\n\nA cloak.\n\nCounts the tide.", ""),
    ("\ufeffName: Mara\r\nPersonality: Reserved.\r\n", "", "Mara", "", "Reserved."),
    ("Name: Mara\n## Personality ##  \nPatient.", "", "Mara", "", "Patient."),
    ("Name: Seraphine\nPersonality: Patient.\n```\nScenario: quoted, not a heading\n```", "", "Seraphine", "", "Patient.\n```\nScenario: quoted, not a heading\n```"),
])
def test_heuristics_preserve_prose_and_section_boundaries(client, world, text, filename, name, description, personality):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text, "filename": filename})
    assert response.status_code == 200, response.text
    data = response.json()["card"]["data"]
    assert (data["name"], data["description"], data["personality"]) == (name, description, personality)


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 64001], ids=["empty", "blank", "oversized"])
def test_parse_refuses_empty_or_oversized_input(client, world, text):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text})
    assert response.status_code == 422
    assert client.get(f"/api/worlds/{world}/characters").json() == []


def test_parse_requires_a_world_and_no_model_key(client, world):
    assert client.post("/api/worlds/missing/characters/parse-text", json={"text": SOURCE}).status_code == 404
    assert client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": SOURCE}).status_code == 200
    response = client.post(f"/api/worlds/{world}/characters/refine-text", json={"text": SOURCE, "card": {}})
    assert response.status_code == 409


def test_inline_markdown_is_prose_not_a_label_delimiter(client, world):
    text = "Name: Mara\nDescription: **Never** speaks aloud.\nGreeting: *{{char}} waves.*\nExample dialogue: ***"
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["description"] == "**Never** speaks aloud."
    assert data["first_mes"] == "*{{char}} waves.*"
    assert data["mes_example"] == "***"


@pytest.mark.parametrize(("text", "name", "field", "value"), [
    # A second `Name:` is prose, not a second name joined to the first (#136).
    ("Name: Mara\n\nSome prose.\n\nName: Morgan\n", "Mara", "description", "Some prose.\n\nName: Morgan"),
    # A speaker line in example dialogue keeps its text in that dialogue.
    ("Name: Mara\nExample dialogue:\n<START>\n{{user}}: hi\nName: Oh, hello there\n",
     "Mara", "mes_example", "<START>\n{{user}}: hi\nName: Oh, hello there"),
    # A label wrapped in emphasis across the whole line loses only its closer.
    ("**Name: Mara**\n**Personality: warm and kind**\n", "Mara", "personality", "warm and kind"),
    ("*Name: Mara*\n", "Mara", "description", ""),
    ("**Name: Mara** (the witch)\n", "Mara (the witch)", "description", ""),
    # Labels may come in any order: a name below another field still counts.
    ("Personality: warm\nName: Mara\n", "Mara", "personality", "warm"),
    # Bulleted labels split like any other label.
    ("- **Name:** Mara\n- **Personality:** warm\n", "Mara", "personality", "warm"),
])
def test_repeated_wrapped_and_bulleted_labels_keep_field_values_exact(client, world, text, name, field, value):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text})
    assert response.status_code == 200, response.text
    data = response.json()["card"]["data"]
    assert data["name"] == name
    assert data[field] == value


def test_an_explicit_name_over_the_folder_limit_is_refused_not_saved_as_a_500(client, world):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": "Name: " + "x" * 201})
    assert response.status_code == 422
    assert "200 characters" in response.json()["detail"]
    # A guessed title is clipped instead, and the full heading stays in the prose.
    guessed = client.post(f"/api/worlds/{world}/characters/parse-text",
                          json={"text": "# " + "x" * 300 + "\nKeeps a ledger."}).json()["card"]["data"]
    assert len(guessed["name"]) == 200
    assert guessed["description"].startswith("# " + "x" * 300)


def test_the_folder_limit_is_on_the_slug_not_the_name(client, world):
    # Lowercasing `İ` makes two code points, so 200 of them name a 399-byte folder.
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": "Name: " + "İ" * 200})
    assert response.status_code == 422
    # A guessed title is clipped until its folder fits, and the draft then saves.
    card = client.post(f"/api/worlds/{world}/characters/parse-text",
                       json={"text": "# " + "İ" * 200 + "\nKeeps a ledger."}).json()["card"]
    assert len(card["data"]["name"]) < 200
    saved = client.post(f"/api/worlds/{world}/characters", json={"name": card["data"]["name"], "card": card})
    assert saved.status_code == 200, saved.text


def test_clipping_a_long_guess_is_bounded_not_quadratic(monkeypatch):
    # Counted rather than timed: a near-limit heading used to be re-slugified
    # once per dropped code point, over the whole remaining line.
    calls = []
    real = paths.slugify
    monkeypatch.setattr(paths, "slugify", lambda text: calls.append(len(text)) or real(text))
    data = _plaintext.parse("# " + "İ" * 60000 + "\nKeeps a ledger.")["data"]
    assert _plaintext._folder_fits(data["name"])
    assert len(calls) <= _plaintext.MAX_NAME_CHARS + 2
    assert max(calls) <= _plaintext.MAX_NAME_CHARS


@pytest.mark.parametrize("text", ["﻿ ﻿", "﻿\n\t﻿"], ids=["bom-space", "bom-lines"])
def test_a_byte_order_mark_alone_is_blank_not_a_name(client, world, text):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text})
    assert response.status_code == 422


def test_bold_labels_with_an_external_colon_preserve_markdown_values(client, world):
    text = "**Name**: Mara\n**Description**: **Never** speaks aloud.\n**Greeting**: ***"
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": text})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == "**Never** speaks aloud."
    assert data["first_mes"] == "***"


def test_explicit_name_does_not_discard_the_document_title(client, world):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "# Never speaks aloud\nName: Mara\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == "# Never speaks aloud"
    assert data["personality"] == "Patient."


@pytest.mark.parametrize("body", [
    "````markdown\n```\nGreeting: quoted metadata\n```\n````",
    "~~~~markdown\n~~~\nGreeting: quoted metadata\n~~~\n~~~~",
    "```markdown\n```not a closer\nGreeting: quoted metadata\n```",
], ids=["backticks", "tildes", "trailing-info"])
def test_markdown_fence_contents_are_never_card_headings(client, world, body):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nDescription:\n" + body + "\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["description"] == body
    assert data["first_mes"] == ""
    assert data["personality"] == "Patient."


@pytest.mark.parametrize("body", [
    "<pre>\nGreeting: literal text\nName: Winifred\n</pre>",
    "<!--\nGreeting: literal text\nName: Winifred\n-->",
], ids=["preformatted", "comment"])
def test_html_blocks_are_literal_source(client, world, body):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nDescription:\n" + body + "\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == body
    assert data["first_mes"] == ""
    assert data["personality"] == "Patient."


@pytest.mark.parametrize("body", [
    '[portrait]: https://example.invalid/ "caption\nName: Winifred"',
    "[portrait\nName: Winifred]: https://example.invalid/",
    '[portrait]: https://example.invalid/\n[portrait]: https://example.invalid/ "caption\nGreeting: literal text\nName: Winifred"',
], ids=["title", "label", "duplicate"])
def test_markdown_reference_definitions_remain_in_description(client, world, body):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nDescription:\n\n" + body + "\n\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == body
    assert data["first_mes"] == ""
    assert data["personality"] == "Patient."


def test_indented_markdown_code_retains_its_text_and_indentation(client, world):
    body = "    Greeting: quoted metadata\n    Name: Winifred"
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nDescription:\n\n" + body + "\n\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == body
    assert data["first_mes"] == ""


def test_a_guessed_title_stays_in_description_for_name_corrections(client, world):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": "# Seraphine"})
    assert response.status_code == 200
    card = response.json()["card"]
    assert card["data"]["name"] == "Seraphine"
    card["data"]["name"] = "Mara"
    assert card["data"]["description"] == "# Seraphine"


@pytest.mark.parametrize("body", [
    "`A quoted example:\nGreeting: literal text\nName: Winifred`",
    "``A `quoted` example:\nGreeting: literal text\nName: Winifred``",
    "![`A quoted example:\nGreeting: literal text\nName: Winifred`](https://example.invalid/image.png)",
    "[A quoted example:\nGreeting: literal text\nName: Winifred](https://example.invalid/)",
    "Keeps notes. <!--\nGreeting: literal text\nName: Winifred\n-->",
], ids=["single-backtick", "double-backtick", "image-alt-code", "multiline-link", "inline-comment"])
def test_multiline_markup_stays_in_its_original_field(client, world, body):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nDescription: " + body + "\nPersonality: Patient."})
    assert response.status_code == 200
    data = response.json()["card"]["data"]
    assert data["name"] == "Mara"
    assert data["description"] == body
    assert data["first_mes"] == ""
    assert data["personality"] == "Patient."


def test_inline_field_values_keep_markdown_hard_breaks(client, world):
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={
        "text": "Name: Mara\nGreeting: Hello.  \nGoodbye."})
    assert response.status_code == 200
    assert response.json()["card"]["data"]["first_mes"] == "Hello.  \nGoodbye."


@pytest.mark.parametrize("source", ["Mara\n" + "x" * 63995, "# " + "x" * 63998],
                         ids=["first-line-guess", "title-guess"])
def test_an_accepted_boundary_source_produces_a_refinable_draft(client, world, source):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-test"})
    data = {"name": "Mara", "description": "A patient keeper.", "personality": "",
            "scenario": "", "first_mes": "", "mes_example": ""}
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(json.dumps(data))
    response = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": source})
    assert response.status_code == 200
    proposed = response.json()["card"]
    result = draft_runs.post(client, f"/api/worlds/{world}/characters/refine-text", json={
        "text": source, "card": proposed})
    assert result.status_code == 200, result.json()
    assert result.json()["card"]["data"]["name"] == "Mara"
    assert client.get(f"/api/worlds/{world}/characters").json() == []


def test_optional_ai_is_detached_metered_and_never_creates_a_character(client, world):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-test"})
    data = {"name": "Seraphine", "description": "A patient keeper.", "personality": "Patient.",
            "scenario": "", "first_mes": "{{char}} waves.", "mes_example": ""}
    fake = FakeOpenRouterComplete("Here is the draft:\n```json\n" + json.dumps(data) + "\n```")
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    proposed = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": SOURCE}).json()["card"]
    proposed["data"]["personality"] = "Patient; speaks softly."
    started = client.post(f"/api/worlds/{world}/characters/refine-text", json={"text": SOURCE, "card": proposed})
    assert started.status_code == 202, started.text
    result = draft_runs.settle(client, started)
    assert result.status_code == 200, result.json()
    assert result.json()["card"]["data"]["description"] == "A patient keeper."
    assert result.json()["card"]["data"]["first_mes"] == "{{char}} waves."
    assert client.get(f"/api/worlds/{world}/characters").json() == []
    assert "speaks softly" in str(fake.messages)
    assert "Saltmarch" in str(fake.messages)


@pytest.mark.parametrize("reply", ["not JSON", "[]", '{"name": "Mara"}',
    '{"name": "Mara", "description": [], "personality": "", "scenario": "", "first_mes": "", "mes_example": ""}'])
def test_bad_ai_output_fails_without_writing_a_card(client, world, reply):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-test"})
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(reply)
    proposed = client.post(f"/api/worlds/{world}/characters/parse-text", json={"text": SOURCE}).json()["card"]
    result = draft_runs.post(client, f"/api/worlds/{world}/characters/refine-text", json={"text": SOURCE, "card": proposed})
    assert result.status_code == 422, result.json()
    assert client.get(f"/api/worlds/{world}/characters").json() == []
