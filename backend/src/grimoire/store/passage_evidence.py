"""Reviewed passage evidence, stored with the campaign's character card.

Quote extraction deliberately accepts only explicit attribution immediately
beside speech. Ambiguous prose remains description evidence, not dialogue.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import appearances, campaigns, characters, locks, overlay


def quotes(passage: str, name: str) -> list[str]:
    name = name.strip()
    if not name:
        return []
    identity = r"(?<![\w'-])" + re.escape(name) + r"(?![\w'-])"
    verb = r"(?:said|asked|replied|whispered|shouted|answered|murmured|called)"
    speech = r'["“]([^"“”\n]+)["”]'
    patterns = [identity + r"\s*(?::|" + verb + r"\s*,?)\s*" + speech,
                r'["“]([^"“”\n]*,)["”]\s*(?:' + identity + r"\s+" + verb
                + r"|" + verb + r"\s+" + identity + r")(?=\s*[.!?](?:\s|$))"]
    found = sorted((m.start(), m.group(1)) for pattern in patterns
                   for m in re.finditer(pattern, passage, flags=re.IGNORECASE))
    return list(dict.fromkeys(quote for _, quote in found))


def examples(passage: str, name: str) -> str:
    return "\n".join("<START>\n{{char}}: " + quote for quote in quotes(passage, name))


def validate(name: str, passage: str, source_text: str, mes_example: str) -> None:
    if not name.strip() or len(name) > 200:
        raise ValueError("Enter a character name of at most 200 characters.")
    if not passage.strip() or len(passage) > 16000 or passage not in source_text:
        raise ValueError("Select a passage of at most 16000 characters from this response.")
    if mes_example.strip() and mes_example.strip() != examples(passage, name):
        raise ValueError("Dialogue examples must be the quoted, explicitly attributed source text.")


def _created_for_operation(cid: str, operation: str) -> dict | None:
    for summary in overlay.list_characters(cid):
        detail = characters.read_character(overlay.char_root(cid, summary["id"]), summary["id"])
        for version in detail["versions"]:
            evidence = version["card"]["data"].get("extensions", {}).get("grimoire", {}).get("passage_evidence", [])
            if any(item.get("operation") == operation for item in evidence):
                return {"character": summary["id"], "version": version["id"], "name": summary["name"]}
    return None


def save(cid: str, sid: str, rid: str, *, name: str, description: str,
         passage: str, source_text: str, mes_example: str,
         existing_ref: str = "") -> dict:
    """Append reviewed evidence; inherited cards are materialized before writing.

    The route holds the scene against generation and validates its source in
    the same campaign lock. Keeping provenance inside the card makes its text
    and evidence one atomic write and preserves evidence through card exports.
    """
    validate(name, passage, source_text, mes_example)
    if len(description) > 16000:
        raise ValueError("The description must be at most 16000 characters.")
    operation = hashlib.sha256(json.dumps([cid, rid, name.strip(), passage, source_text,
        description.strip(), mes_example.strip(), existing_ref], ensure_ascii=False).encode("utf-8")).hexdigest()
    with locks.campaign_lock(cid):
        if not existing_ref:
            # Creation and seating are separate store writes. A retry after
            # seating failed must find the card already written, not mint a
            # second identity. The marker travels atomically with its card.
            prior = _created_for_operation(cid, operation)
            if prior:
                return prior
        aid = ""
        vid = ""
        if existing_ref:
            kind, _, aid = existing_ref.partition(":")
            if kind != "characters" or not aid:
                raise ValueError("Choose an existing character.")
            detail = overlay.read_character(cid, aid)
            vid = appearances.locked_version(cid, "characters", aid) or detail["meta"]["default_version"]
            card = characters.read_card(overlay.char_root(cid, aid), aid, vid)
        else:
            card = characters.blank_card(name.strip())
        data = card["data"]
        evidence = data.setdefault("extensions", {}).setdefault("grimoire", {}).setdefault("passage_evidence", [])
        if any(item.get("operation") == operation for item in evidence):
            return {"character": aid, "version": vid, "name": data.get("name", name.strip())}
        for key, addition in (("description", description.strip()), ("mes_example", mes_example.strip())):
            if addition:
                data[key] = "\n\n".join(filter(None, (data.get(key, ""), addition)))
        evidence.append({"operation": operation, "scene_id": sid, "response_id": rid, "source_text": source_text,
                         "passage": passage, "reviewed_name": name.strip(),
                         "quotes": quotes(passage, name) if mes_example.strip() else []})
        if aid:
            overlay.materialize_actor(cid, "characters", aid)
            characters.update_version(campaigns.campaign_root(cid), aid, vid, card)
        else:
            aid, vid = overlay.create_character(cid, name.strip(), card=card)
        return {"character": aid, "version": vid, "name": data.get("name", name.strip())}
