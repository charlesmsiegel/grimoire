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
    return str(persona.get("name") or "")


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


def require_unique(name: str, *, scope: str, scope_id: str,
                   actor_ref: str | None = None) -> None:
    """Raise before a write if another visible actor owns this full name."""
    normalized = name.strip().casefold()
    if not normalized:
        return
    scopes = [(scope, scope_id)]
    if scope == "world":
        scopes.extend(("campaign", row["id"]) for row in campaigns.list_campaigns()
                      if row.get("world") == scope_id)
    for at_scope, at_id in scopes:
        for row in _rows(at_scope, at_id):
            if row["ref"] != actor_ref and row["name"].strip().casefold() == normalized:
                raise ActorNameError(
                    f"{name!r} is already used by {row['ref']} in {at_scope} {at_id}")
