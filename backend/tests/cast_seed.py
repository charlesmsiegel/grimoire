"""A scene with two NPCs cast in it, seeded through the routes -- the ground
the turn, branch and group-play suites play on.

One copy here rather than the dozen identical ones those suites each carried:
the characters are created and cast through `POST /campaigns/{cid}/characters`
and `.../cast`, so a suite stands on what a user's own setup would write, and
each step is checked as it goes so a broken seed fails here, not three
assertions later in a test about something else.
"""

import grimoire.store as store
from tests.inference_fixtures import primary


def seed(client, module=None, *, key=True,
         cast=("Mara", "Winifred")) -> tuple[str, str]:
    """(cid, sid): an OpenRouter key (unless `key=False`), a world "Realm",
    campaign "Saltmarch" (bound to `module` when given), scene "Mara", and
    each of `cast` -- Mara and Winifred by default -- created as a campaign
    character and cast into it."""
    if key:
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in cast:
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def seed_scene(client) -> tuple[str, str, str]:
    """`seed`, plus the scene's route prefix: (cid, sid, base)."""
    cid, sid = seed(client)
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def seed_primary(client) -> tuple[str, str]:
    """(cid, sid) at format 2: `inference_fixtures.primary` (a keyed provider
    holding the Primary role), then `seed`'s scene with Mara alone cast."""
    primary(client)
    return seed(client, key=False, cast=("Mara",))
