"""Check full actor names in the library visible to a World or Campaign.

The transcript stores labels, so two different actors with the same full name
cannot be assigned a historical label reliably. Existing collisions remain
readable; this check runs only when an actor is created or renamed.
"""

from __future__ import annotations

from . import campaigns, characters, overlay, pcs, worlds


class ActorNameError(ValueError):
    pass


def card_name(card: dict) -> str:
    data = card.get("data") or {}
    return str(data.get("name") or "") if isinstance(data, dict) else ""


def persona_name(persona: dict) -> str:
    """The name a persona will be STORED under -- whitespace folded, exactly as
    `pcs._dump_persona` folds every frontmatter scalar. Checking the raw value
    would let `"Mara\nVance"` past uniqueness and land as an existing "Mara
    Vance" (#65)."""
    return " ".join(str(persona.get("name") or "").split())


def _rows(scope: str, scope_id: str) -> list[dict]:
    if scope == "world":
        root = worlds.world_root(scope_id)
        groups = (("characters", characters.list_characters(root)),
                  ("pcs", pcs.list_pcs(root)))
    else:
        groups = (("characters", overlay.list_characters(scope_id)),
                  ("pcs", overlay.list_pcs(scope_id)))
    found = []
    for kind, rows in groups:
        for row in rows:
            ref = f"{kind}:{row['id']}"
            found.append({"ref": ref, "name": row["name"]})
            if scope == "world":
                detail = (characters.read_character(root, row["id"]) if kind == "characters"
                          else pcs.read_pc(root, row["id"]))
            else:
                detail = (overlay.read_character(scope_id, row["id"]) if kind == "characters"
                          else overlay.read_pc(scope_id, row["id"]))
            for version in detail["versions"]:
                alternate = (card_name(version["card"]) if kind == "characters"
                             else persona_name(version["persona"]))
                if alternate:
                    found.append({"ref": ref, "name": alternate})
    return found


#: What each kind of actor is called in a sentence a reader sees. The refs
#: (`characters:mara`) are the store's spelling, and the 409 this module raises
#: is shown verbatim -- the campaign wizard printed one at a player who had
#: only typed a name.
_KIND_WORDS = {"characters": "a character", "pcs": "a player character"}


def _holder(normalized: str, at_scope: str, at_id: str, *, actor_ref: str | None,
            ignore_campaign: str | None) -> dict | None:
    """The first actor in this one scope already called `normalized`."""
    for row in _rows(at_scope, at_id):
        own_actor = row["ref"] == actor_ref and (
            ignore_campaign is None or (at_scope == "campaign" and at_id == ignore_campaign))
        if not own_actor and row["name"].strip().casefold() == normalized:
            return row
    return None


def _refusal(row: dict, where: str) -> str:
    kind = _KIND_WORDS.get(row["ref"].split(":", 1)[0], "an actor")
    return (f"There is already {kind} called \"{row['name'].strip()}\" {where}. A transcript "
            "could not tell the two apart, so this one needs another name.")


def require_unique(name: str, *, scope: str, scope_id: str,
                   actor_ref: str | None = None,
                   ignore_campaign: str | None = None) -> None:
    """Raise before a write if another visible actor owns this full name."""
    normalized = name.strip().casefold()
    if not normalized:
        return
    scopes = [(scope, scope_id, f"in this {scope}")]
    if scope == "world":
        scopes.extend(("campaign", row["id"],
                       f"in {row.get('name') or row['id']}, a campaign of this world")
                      for row in campaigns.list_campaigns() if row.get("world") == scope_id)
    for at_scope, at_id, where in scopes:
        row = _holder(normalized, at_scope, at_id, actor_ref=actor_ref,
                      ignore_campaign=ignore_campaign)
        if row is not None:
            raise ActorNameError(_refusal(row, where))


def refusal_in_new_campaign(name: str, wid: str) -> str | None:
    """What a campaign just begun in world `wid` would refuse a new PC called
    `name` with, or None when it would take it.

    The campaign wizard asks this BEFORE it creates anything. It used to learn
    the answer from `POST /campaigns/{cid}/pcs`, after `POST /campaigns` had
    already made the campaign, so every refused name left a half-made campaign
    behind. A new campaign is an overlay with nothing of its own yet, so the
    actors it can see are exactly its world's -- this one scope, and NOT the
    world's campaigns as well: `require_unique` widens a WORLD write to those
    because a world actor is visible in every one of them, and a campaign PC is
    visible in none but its own. Both spellings `POST /campaigns/{cid}/pcs`
    checks are asked: the name as typed, and the persona's whitespace-folded one.
    """
    for candidate in dict.fromkeys((name.strip(), persona_name({"name": name}))):
        if not candidate:
            continue
        row = _holder(candidate.casefold(), "world", wid, actor_ref=None, ignore_campaign=None)
        if row is not None:
            return _refusal(row, "in this world")
    return None
